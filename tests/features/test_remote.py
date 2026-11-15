"""P7 remote HTTPS storage tests: TLS, users, isolation, resume, integrity."""
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from backnest import engine
from backnest.remote import RemoteClient, RemoteError, snapshot_files
from backnest.remote_server import RemoteService
from backnest.tls import TlsUnavailable, ensure_test_cert
from support import WorkspaceTemporaryDirectory


class RemoteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            tls = ensure_test_cert()
        except TlsUnavailable as error:
            raise unittest.SkipTest(str(error))
        cls.certfile, cls.keyfile, cls.cafile = tls
        cls.temp = WorkspaceTemporaryDirectory(prefix="remote-service-")
        cls.service = RemoteService(Path(cls.temp.name) / "remote", cls.certfile, cls.keyfile)
        cls.service.add_user("alice", "alice-pass")
        cls.service.add_user("bob", "bob-pass")
        cls.service.start()

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.work = WorkspaceTemporaryDirectory(prefix="remote-case-")
        self.root = Path(self.work.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "hello.txt").write_text("remote 中文\n" * 100, encoding="utf-8")
        (self.source / "empty").mkdir()
        (self.source / "binary.bin").write_bytes(os.urandom(200_000))
        self.snapshot = Path(engine.backup(self.source, self.root / "backups")["path"])

    def tearDown(self):
        self.work.cleanup()

    def client(self, user="alice", password="alice-pass"):
        client = RemoteClient(self.service.url, cafile=self.cafile)
        client.login(user, password)
        return client

    def test_upload_catalog_download_round_trip(self):
        client = self.client()
        result = client.upload_snapshot(self.snapshot, name="roundtrip")
        self.assertTrue(result["complete"])
        self.assertEqual(result["bytes"], sum(item["size"] for item in snapshot_files(self.snapshot)))
        catalog = client.catalog()
        self.assertTrue(any(item["id"] == result["id"] for item in catalog))
        destination = self.root / "downloaded"
        downloaded = client.download_snapshot(result["id"], destination)
        self.assertTrue(downloaded["verified"])
        self.assertEqual((destination / "manifest.bkn").read_bytes(),
                         (self.snapshot / "manifest.bkn").read_bytes())
        self.assertEqual((destination / "data" / "binary.bin").read_bytes(),
                         (self.source / "binary.bin").read_bytes())

    def test_wrong_password_and_missing_ca(self):
        with self.assertRaises(RemoteError):
            RemoteClient(self.service.url, cafile=self.cafile).login("alice", "wrong")
        with self.assertRaises(RemoteError):
            RemoteClient(self.service.url).health()

    def test_user_isolation(self):
        alice = self.client()
        result = alice.upload_snapshot(self.snapshot, name="alice")
        bob = self.client("bob", "bob-pass")
        self.assertEqual(bob.catalog(), [])
        with self.assertRaises(RemoteError):
            bob.download_snapshot(result["id"], self.root / "stolen")

    def test_upload_resume_after_partial_chunk(self):
        client = self.client()
        files = snapshot_files(self.snapshot)
        meta = client.create_snapshot("resume", files)
        first = files[0]
        path = self.snapshot.joinpath(*first["path"].split("/"))
        partial = path.read_bytes()[:70000]
        client._request("PUT", "/api/v1/snapshots/" + meta["id"] + "/files?path=" + first["path"],
                        body=partial, headers={"X-Offset": "0"})
        state = client.status(meta["id"])
        self.assertEqual(state["files"][first["path"]], len(partial))
        result = client.upload_snapshot(self.snapshot, resume_id=meta["id"])
        self.assertTrue(result["complete"])
        destination = self.root / "resumed"
        client.download_snapshot(meta["id"], destination)
        self.assertEqual((destination / "data" / "hello.txt").read_bytes(),
                         (self.source / "hello.txt").read_bytes())

    def test_download_resume_with_range(self):
        client = self.client()
        result = client.upload_snapshot(self.snapshot, name="range")
        destination = self.root / "partial"
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = destination / "data" / "binary.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        original = (self.source / "binary.bin").read_bytes()
        target.write_bytes(original[:50_000])
        client.download_file(result["id"], "data/binary.bin", target, offset=50_000)
        self.assertEqual(target.read_bytes(), original)

    def test_finalize_detects_tampered_chunk(self):
        client = self.client()
        files = snapshot_files(self.snapshot)
        meta = client.create_snapshot("tamper", files)
        first = files[0]
        path = self.snapshot.joinpath(*first["path"].split("/"))
        data = bytearray(path.read_bytes())
        data[0] ^= 1
        client._request("PUT", "/api/v1/snapshots/" + meta["id"] + "/files?path=" + first["path"],
                        body=bytes(data), headers={"X-Offset": "0"})
        with self.assertRaises(RemoteError):
            client.finalize(meta["id"])

    def test_delete_only_own_snapshot(self):
        alice = self.client()
        result = alice.upload_snapshot(self.snapshot, name="delete")
        bob = self.client("bob", "bob-pass")
        with self.assertRaises(RemoteError):
            bob.delete(result["id"])
        self.assertTrue(alice.delete(result["id"])["deleted"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
