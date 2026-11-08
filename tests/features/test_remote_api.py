"""P7 HTTP API tests: remote config, auth, upload/download and catalog."""
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from backnest import engine
from backnest.remote_server import RemoteService
from backnest.tls import TlsUnavailable, ensure_test_cert
from server import DemoServer
from support import WorkspaceTemporaryDirectory


class RemoteApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.certfile, cls.keyfile, cls.cafile = ensure_test_cert()
        except TlsUnavailable as error:
            raise unittest.SkipTest(str(error))
        cls.remote_state = WorkspaceTemporaryDirectory(prefix="p7-service-")
        cls.remote = RemoteService(Path(cls.remote_state.name) / "remote", cls.certfile, cls.keyfile)
        cls.remote.add_user("alice", "alice-pass")
        cls.remote.add_user("bob", "bob-pass")
        cls.remote.start()
        os.environ["BACKNEST_REMOTE_CA"] = cls.cafile
        cls.state = WorkspaceTemporaryDirectory(prefix="p7-api-state-")
        cls.server = DemoServer(0, state_dir=cls.state.name)
        cls.server.scheduler.stop()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.remote.stop()
        cls.remote_state.cleanup()
        cls.state.cleanup()

    def setUp(self):
        self.work = WorkspaceTemporaryDirectory(prefix="p7-api-case-")
        self.root = Path(self.work.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "hello.txt").write_text("P7 API 中文\n" * 100, encoding="utf-8")
        (self.source / "data.bin").write_bytes(os.urandom(120_000))
        self.snapshot = Path(engine.backup(self.source, self.root / "backups")["path"])

    def tearDown(self):
        self.work.cleanup()

    def request(self, path, body=None, method=None):
        headers = {"X-BackNest-Token": self.server.token}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        request = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=60) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except HTTPError as error:
            raw = error.read()
            return error.code, json.loads(raw) if raw else None

    def ok(self, path, body=None, method=None):
        code, data = self.request(path, body, method)
        self.assertEqual(code, 200, (path, data))
        return data

    def wait(self, job_id, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            code, job = self.request("/api/v1/jobs/" + job_id)
            self.assertEqual(code, 200)
            if job["state"] != "running":
                return job
            time.sleep(0.05)
        self.fail("job timeout")

    def remote_config(self):
        secret = self.ok("/api/v1/secrets", {"value": "alice\nalice-pass", "purpose": "remote_auth"}, "POST")
        remote = self.ok("/api/v1/remotes", {"name": "test", "endpoint": self.remote.url,
                                             "credentialRef": secret["secretRef"], "caFile": self.cafile}, "POST")
        return remote, secret

    def test_remote_create_test_upload_download(self):
        remote, secret = self.remote_config()
        self.assertEqual(remote["status"], "configured")
        connected = self.ok("/api/v1/remotes/" + remote["id"] + "/test", {}, "POST")
        self.assertTrue(connected["connected"])
        self.assertTrue(connected["tlsVerified"])
        code, accepted = self.request("/api/v1/remotes/" + remote["id"] + "/upload",
                                      {"snapshot": str(self.snapshot), "destination": remote["id"]}, "POST")
        self.assertEqual(code, 202, accepted)
        job = self.wait(accepted["id"])
        self.assertEqual(job["state"], "completed", job)
        remote_id = job["result"]["path"]
        catalog = self.ok("/api/v1/remotes/" + remote["id"] + "/snapshots")
        self.assertTrue(any(item["snapshot"] == remote_id for item in catalog))
        destination = self.root / "downloaded"
        code, accepted = self.request("/api/v1/remotes/" + remote["id"] + "/download",
                                      {"snapshot": remote_id, "snapshotId": remote["id"],
                                       "destination": str(destination)}, "POST")
        job = self.wait(accepted["id"])
        self.assertEqual(job["state"], "completed", job)
        self.assertEqual((destination / "data" / "hello.txt").read_bytes(),
                         (self.source / "hello.txt").read_bytes())
        self.ok("/api/v1/secrets/" + secret["secretRef"], method="DELETE")
        self.ok("/api/v1/remotes/" + remote["id"], method="DELETE")

    def test_users_and_auth(self):
        secret = self.ok("/api/v1/secrets", {"value": "local-pass", "purpose": "remote_auth"}, "POST")
        user = self.ok("/api/v1/users", {"username": "alice", "roles": ["admin"],
                                         "secretRef": secret["secretRef"]}, "POST")
        self.assertEqual(user["username"], "alice")
        login = self.ok("/api/v1/auth/sessions", {"username": "alice", "password": "local-pass"}, "POST")
        self.assertEqual(login["user"]["username"], "alice")
        self.ok("/api/v1/auth/sessions/" + login["id"], method="DELETE")
        code, _ = self.request("/api/v1/auth/sessions", {"username": "alice", "password": "wrong"}, "POST")
        self.assertEqual(code, 401)
        self.ok("/api/v1/users/" + user["id"], method="DELETE")
        self.ok("/api/v1/secrets/" + secret["secretRef"], method="DELETE")

    def test_bad_credentials_and_remote_errors(self):
        secret = self.ok("/api/v1/secrets", {"value": "alice\nwrong", "purpose": "remote_auth"}, "POST")
        remote = self.ok("/api/v1/remotes", {"name": "bad", "endpoint": self.remote.url,
                                             "credentialRef": secret["secretRef"], "caFile": self.cafile}, "POST")
        result = self.ok("/api/v1/remotes/" + remote["id"] + "/test", {}, "POST")
        self.assertFalse(result["connected"])
        code, error = self.request("/api/v1/remotes", {"name": "bad", "endpoint": "http://insecure"}, "POST")
        self.assertEqual(code, 400, error)
        self.ok("/api/v1/remotes/" + remote["id"], method="DELETE")
        self.ok("/api/v1/secrets/" + secret["secretRef"], method="DELETE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
