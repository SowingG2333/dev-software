"""P2-P4 acceptance tests against actual files and the compiled C++ engine."""
import csv
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.build import build
from native_options import native_options
from repository import Repository
from server import DemoServer


def independently_decode(data):
    """Independent Python decoder oracle; never used by the application."""
    assert data[:7] == b'BNRLE1\n'
    expected, = struct.unpack_from('<Q', data, 7)
    offset, output = 15, bytearray()
    while len(output) < expected:
        raw, stored, mode, crc = struct.unpack_from('<IIBI', data, offset)
        offset += 13
        payload = data[offset:offset+stored]; offset += stored
        decoded = bytearray()
        if mode:
            i = 0
            while i < len(payload):
                control = payload[i]; i += 1
                count = (control & 127) + 1
                if control & 128:
                    decoded.extend([payload[i]] * count); i += 1
                else:
                    decoded.extend(payload[i:i+count]); i += count
        else:
            decoded.extend(payload)
        assert len(decoded) == raw and zlib.crc32(decoded) == crc
        output.extend(decoded)
    assert len(output) == expected and offset == len(data)
    return bytes(output)


class P234Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary = build()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='backnest-p234-')
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def command(self, *args, options=None, success=True, decision=None):
        option_file = self.root / 'options'
        option_file.write_text(native_options(options or {}), encoding='utf-8')
        env = {**os.environ, 'BACKNEST_OPTIONS': str(option_file), 'BACKNEST_DECISION': str(decision) if decision else ''}
        run = subprocess.run([str(self.binary), *map(str, args)], capture_output=True, encoding='utf-8', timeout=90, env=env)
        events = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual(run.returncode == 0, success, run.stdout[-3000:] + run.stderr)
        self.assertEqual(events[-1]['event'], 'result' if success else 'error')
        return events[-1]

    def test_rle_vectors_and_independent_decoder(self):
        rng = random.Random(42)
        for name, data in [('empty', b''), ('one', b'x'), ('run', b'Z' * 2_000_000),
                           ('random', rng.randbytes(1_000_003)), ('large', bytes(range(256)) * 65536)]:
            with self.subTest(name=name):
                source, compressed, restored = [self.root / (name + suffix) for suffix in ('.raw', '.rle', '.out')]
                source.write_bytes(data)
                self.command('compress', source, compressed)
                self.assertEqual(independently_decode(compressed.read_bytes()), data)
                self.command('decompress', compressed, restored)
                self.assertEqual(restored.read_bytes(), data)
                if name == 'run': self.assertLess(compressed.stat().st_size, len(data) // 20)

    def test_rle_corruption_truncation_limits_and_existing_output(self):
        source = self.root / 'data'; source.write_bytes(b'hello' * 30000)
        encoded = self.root / 'rle'; self.command('compress', source, encoded)
        original = encoded.read_bytes()
        for index, data in enumerate([original[:-1], original + b'x', original[:25] + bytes([original[25] ^ 1]) + original[26:],
                                      b'BNRLE1\n' + struct.pack('<Q', 2**63),
                                      b'BNRLE1\n' + struct.pack('<Q', 2) + struct.pack('<IIBI', 2, 2, 1, 0) + b'\xffA']):
            damaged = self.root / f'bad-{index}'; damaged.write_bytes(data)
            target = self.root / f'out-{index}'
            self.command('decompress', damaged, target, success=False)
            self.assertFalse(target.exists())
        self.command('decompress', encoded, self.root / 'limited', options={'compression': {'algorithm': 'rle-block-v1', 'maxOutputBytes': 5}}, success=False)
        self.command('decompress', encoded, source, success=False)
        self.assertEqual(source.read_bytes(), b'hello' * 30000)
        self.assertFalse(list(self.root.glob('.backnest-*')))

    def sample(self):
        source = self.root / '源目录'; (source / '文档/空目录').mkdir(parents=True)
        (source / '文档/报告.txt').write_bytes(b'content' * 40000)
        (source / '文档/excluded.bin').write_bytes(b'123')
        (source / 'empty.txt').touch()
        return source

    def test_compressed_snapshot_metadata_verify_restore(self):
        source = self.sample()
        stamp = 1700000000123456700
        os.utime(source / '文档/报告.txt', ns=(stamp, stamp))
        expected = (source / '文档/报告.txt').stat().st_mtime_ns
        backup = self.command('backup', source, self.root / 'backups', options={
            'archive': {'algorithm': 'bkn-container-v2'}, 'compression': {'algorithm': 'rle-block-v1'},
            'metadata': {'preserve': ['times', 'permissions']}})
        snapshot = Path(backup['path']); self.assertTrue(snapshot.is_file())
        self.command('verify', snapshot)
        target = self.root / 'restored'; self.command('restore', snapshot, target)
        self.assertEqual((target / '文档/报告.txt').read_bytes(), (source / '文档/报告.txt').read_bytes())
        self.assertEqual((target / '文档/报告.txt').stat().st_mtime_ns, expected)
        self.assertTrue((target / '文档/空目录').is_dir())
        self.assertTrue((target / 'empty.txt').is_file())

    def test_archive_roundtrip_empty_and_corruption(self):
        for source in [self.sample(), self.root / 'empty']:
            source.mkdir(exist_ok=True)
            archive = self.root / (source.name + '.bka')
            self.command('pack', source, archive)
            restored = self.root / (source.name + '-out'); self.command('unpack', archive, restored)
            self.assertEqual(sorted(str(p.relative_to(source)) for p in source.rglob('*')),
                             sorted(str(p.relative_to(restored)) for p in restored.rglob('*')))
        archive = self.root / '源目录.bka'; original = archive.read_bytes()
        for index, changed in enumerate([original[:-1], original + b'junk', original[:22] + bytes([original[22] ^ 1]) + original[23:]]):
            bad = self.root / f'bad-{index}.bka'; bad.write_bytes(changed)
            target = self.root / f'bad-output-{index}'
            self.command('unpack', bad, target, success=False); self.assertFalse(target.exists())
        self.assertFalse(list(self.root.glob('.backnest-*')))

    def test_malicious_archive_paths_counts_and_blocks(self):
        for index, name in enumerate([b'../escape', b'/absolute', b'a/../../escape', b'a\\b', b'C:/escape']):
            data = b'BKNARC2\n' + struct.pack('<IQBI', 1, 0, 1, len(name)) + name + struct.pack('<Q', 0)
            data += struct.pack('<I', zlib.crc32(data))
            file = self.root / f'attack-{index}'; file.write_bytes(data)
            self.command('unpack', file, self.root / f'out-{index}', success=False)
        data = b'BKNARC2\n' + struct.pack('<IQ', 200003, 0)
        data += struct.pack('<I', zlib.crc32(data)); file = self.root / 'count'; file.write_bytes(data)
        self.command('unpack', file, self.root / 'count-out', success=False)
        self.assertFalse(list(self.root.glob('.backnest-*')))

    def test_six_filters_preview_equals_actual_backup(self):
        source = self.sample()
        if os.name == 'nt':
            row = next(csv.reader(io.StringIO(subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True))))
            owner = {'platform': 'windows', 'id': row[1]}
        else:
            owner = {'platform': 'posix', 'id': str(os.getuid())}
        filters = {'paths': {'include': ['文档/*'], 'exclude': ['*/空目录']}, 'names': ['报?.txt'], 'types': ['regular'],
                   'time': {'field': 'modified', 'from': '2020-01-01T00:00:00Z', 'to': '2099-01-01T00:00:00Z'},
                   'size': {'minBytes': 10, 'maxBytes': 1000000}, 'users': [owner]}
        preview = self.command('preview', source, options={'filters': filters})
        self.assertEqual(preview['files'], 1)
        snapshot = self.command('backup', source, self.root / 'backups', options={'filters': filters})
        data = Path(snapshot['path']) / 'data'
        self.assertEqual({e['path'] for e in preview['included']}, {p.relative_to(data).as_posix() for p in data.rglob('*')})
        self.assertEqual(preview['bytes'], snapshot['bytes'])

    def test_cancel_before_work_cannot_publish_snapshot(self):
        source = self.sample(); decision = self.root / 'decision'; decision.mkdir()
        self.command('backup', source, self.root / 'cancel-backup', decision=decision, success=False)
        self.assertFalse((self.root / 'cancel-backup').exists())

    def test_server_cancel_and_persisted_terminal_job(self):
        source = self.sample(); (source / 'large').write_bytes(b'x' * 32 * 1024 * 1024)
        data = self.root / 'db'; server = DemoServer(0, data)
        job_id = server.start_job('backup', [str(source), str(self.root / 'backup')])
        server.cancel_job(job_id)
        server.server_close()  # waits for cooperative cleanup
        reopened = DemoServer(0, data)
        try:
            self.assertEqual(reopened.jobs[job_id]['state'], 'cancelled')
            self.assertNotIn('result', reopened.jobs[job_id])
            self.assertFalse(list((self.root / 'backup').glob('snapshot-*')))
            self.assertFalse(list((self.root / 'backup').glob('.partial-*')))
        finally:
            reopened.server_close()

    def test_repository_restart_conflict_lock_and_interrupted_jobs(self):
        path = self.root / 'db'; repo = Repository(path)
        state = {'tasks': [{'id': 't', 'name': '中文任务'}], 'logs': [], 'settings': {'target': 'test'}}
        repo.save(state, 0)
        with self.assertRaises(ValueError): repo.save(state, 0)
        with self.assertRaises(RuntimeError): Repository(path)
        repo.save_job({'id': 'interrupted', 'state': 'running'}); repo.close()
        repo = Repository(path)
        try:
            self.assertEqual(repo.load()['state'], state)
            self.assertEqual(repo.recover_jobs()['interrupted']['state'], 'failed')
        finally:
            repo.close()


if __name__ == '__main__': unittest.main()
