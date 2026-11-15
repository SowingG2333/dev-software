"""Acceptance of added ports through the real local HTTP adapter."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server import DemoServer


class ExtendedHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.start()

    def start(self):
        self.server = DemoServer(0, self.root / 'db')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def tearDown(self):
        self.stop(); self.temp.cleanup()

    def request(self, route, body=None):
        req = Request(f'http://127.0.0.1:{self.server.server_port}/api/v1/' + route,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={'X-BackNest-Token': self.server.token, 'Content-Type': 'application/json'})
        try:
            with build_opener(ProxyHandler({})).open(req, timeout=15) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def wait(self, accepted):
        self.assertEqual(accepted[0], 202, accepted)
        for _ in range(300):
            code, job = self.request('jobs/' + accepted[1]['id'])
            self.assertEqual(code, 200)
            if job['state'] != 'running':
                self.assertEqual(job['state'], 'completed', job)
                return job['result']
            time.sleep(.02)
        self.fail('Job timeout')

    def test_preview_compressed_backup_restore_and_restart(self):
        source = self.root / 'source'; (source / 'empty').mkdir(parents=True)
        (source / '中文.txt').write_bytes(b'A' * 120000)
        (source / 'skip.bin').write_bytes(b'skip')
        filters = {'paths': {'exclude': ['*.bin']}}
        code, preview = self.request('filters/preview', {'source': str(source), 'filters': filters})
        self.assertEqual(code, 200); self.assertEqual(preview['files'], 1)
        result = self.wait(self.request('backups', {'source': str(source), 'destination': str(self.root / 'versions'),
            'options': {'filters': filters, 'compression': {'algorithm': 'rle-block-v1'}}}))
        self.assertTrue(Path(result['path']).is_file())
        self.wait(self.request('snapshots/verify', {'snapshot': result['path']}))
        target = self.root / 'restored'
        self.wait(self.request('restores', {'snapshot': result['path'], 'destination': str(target)}))
        self.assertEqual((target / '中文.txt').read_bytes(), b'A' * 120000)
        self.assertFalse((target / 'skip.bin').exists()); self.assertTrue((target / 'empty').is_dir())
        state = {'schemaVersion': 2, 'tasks': [{'id': 'test', 'options': {'filters': filters}, 'versions': [result]}], 'logs': [], 'settings': {'target': str(target)}}
        code, saved = self.request('workspace/state', {'revision': 0, 'state': state})
        self.assertEqual(code, 200)
        self.stop(); self.start()
        code, loaded = self.request('workspace/state')
        self.assertEqual(loaded, saved)

    def test_codec_ports_and_invalid_options_do_not_write(self):
        source = self.root / 'input'; source.write_bytes(b'z' * 90000)
        encoded, output = self.root / 'encoded', self.root / 'output'
        for action, a, b in [('compress', source, encoded), ('decompress', encoded, output)]:
            self.wait(self.request('compression/' + action, {'source': str(a), 'destination': str(b), 'options': {'algorithm': 'rle-block-v1'}}))
        self.assertEqual(source.read_bytes(), output.read_bytes())
        code, _ = self.request('compression/compress', {'source': str(source), 'destination': str(self.root / 'invalid'), 'options': {'algorithm': 'unknown'}})
        self.assertEqual(code, 400); self.assertFalse((self.root / 'invalid').exists())


if __name__ == '__main__': unittest.main()
