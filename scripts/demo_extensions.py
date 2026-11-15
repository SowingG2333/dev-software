"""Run a real end-to-end demo of P5/P6/P7 through the v1 HTTP bridge.

Usage: python scripts/demo_extensions.py

Creates an isolated directory under .backnest/demo-ext-<id>, starts the local
bridge and a separate HTTPS remote, then exercises:
  * P5 BNE1 encrypt/decrypt, wrong-password failure
  * P6 full + incremental backup, preview, verify, restore, cron, watcher
  * P7 remote create/test, resumable upload, catalog, download
Artifacts are intentionally kept for inspection.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets as _secrets
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backnest.remote_server import RemoteService  # noqa: E402
from backnest.tls import TlsUnavailable, ensure_test_cert  # noqa: E402
from server import DemoServer  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    base = ROOT / ".backnest" / ("demo-ext-" + _secrets.token_hex(4))
    base.mkdir(parents=True, exist_ok=True)
    source = base / "源 目录"
    source.mkdir()
    (source / "空目录").mkdir()
    (source / "空.txt").touch()
    (source / "说明.txt").write_text("BackNest P5/P6/P7 演示\n" * 200, encoding="utf-8")
    (source / "binary.bin").write_bytes(os.urandom(300_000))

    remote_state = base / "remote"
    try:
        tls = ensure_test_cert(ROOT / ".backnest" / "tls")
    except TlsUnavailable as error:
        raise SystemExit(str(error))
    remote = RemoteService(remote_state, tls.certfile, tls.keyfile)
    remote.add_user("demo", "demo-password")
    remote.start()
    os.environ["BACKNEST_REMOTE_CA"] = tls.cafile

    state = base / "state"
    state.mkdir()
    server = DemoServer(0, state_dir=state)
    server.scheduler.stop()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"

    def request(path, body=None, method=None):
        headers = {"X-BackNest-Token": server.token}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = Request(url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=120) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else None
        except HTTPError as error:
            raw = error.read()
            return error.code, json.loads(raw) if raw else None

    def ok(path, body=None, method=None):
        code, data = request(path, body, method)
        if code != 200:
            raise SystemExit(f"{path} failed: {code} {data}")
        return data

    def accepted(path, body):
        code, data = request(path, body, "POST")
        if code != 202:
            raise SystemExit(f"{path} was not accepted: {code} {data}")
        return data["id"]

    def wait(job_id, timeout=120):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = ok("/api/v1/jobs/" + job_id)
            if job["state"] != "running":
                return job
            time.sleep(0.03)
        raise SystemExit("job timeout: " + job_id)

    print("BackNest P5/P6/P7 demo")
    print("workspace:", base)
    print("engine   :", server.engine_name)
    try:
        # P5
        secret = ok("/api/v1/secrets", {"value": "demo-password", "purpose": "encryption"}, "POST")
        plain = base / "plain.txt"
        plain.write_text("机密内容\n" * 500, encoding="utf-8")
        encrypted = base / "plain.bne"
        job = wait(accepted("/api/v1/encryption/encrypt", {
            "source": str(plain), "destination": str(encrypted),
            "options": {"algorithm": "aes-256-gcm"}, "secretRef": secret["secretRef"]}))
        decrypted = base / "plain.out"
        job2 = wait(accepted("/api/v1/encryption/decrypt", {
            "source": str(encrypted), "destination": str(decrypted),
            "options": {"algorithm": "aes-256-gcm"}, "secretRef": secret["secretRef"]}))
        wrong = ok("/api/v1/secrets", {"value": "wrong", "purpose": "encryption"}, "POST")
        job3 = wait(accepted("/api/v1/encryption/decrypt", {
            "source": str(encrypted), "destination": str(base / "must-not-exist"),
            "options": {"algorithm": "aes-256-gcm"}, "secretRef": wrong["secretRef"]}))
        print(f"\n[P5] encrypt/decrypt : {job['state']}/{job2['state']} identical={decrypted.read_bytes() == plain.read_bytes()}")
        print(f"[P5] wrong password  : {job3['state']} output_exists={(base / 'must-not-exist').exists()}")
        ok("/api/v1/secrets/" + secret["secretRef"], method="DELETE")
        ok("/api/v1/secrets/" + wrong["secretRef"], method="DELETE")

        # P6
        backups = base / "backups"
        job = wait(accepted("/api/v1/backups", {
            "source": str(source), "destination": str(backups), "options": {"mode": "full"}}))
        first = job["result"]["path"]
        (source / "说明.txt").write_text("changed\n", encoding="utf-8")
        (source / "新增.txt").write_text("new\n", encoding="utf-8")
        (source / "空.txt").unlink()
        preview = ok("/api/v1/incremental/preview", {"source": str(source), "destination": str(backups)}, "POST")
        job = wait(accepted("/api/v1/backups", {
            "source": str(source), "destination": str(backups), "options": {"mode": "incremental"}}))
        second = job["result"]["path"]
        wait(accepted("/api/v1/snapshots/verify", {"snapshot": second}))
        restored = base / "restored"
        wait(accepted("/api/v1/restores", {"snapshot": second, "destination": str(restored)}))
        print(f"\n[P6] full/incremental: {first} / {second}")
        print(f"[P6] preview         : added={len(preview['added'])} changed={len(preview['changed'])} deleted={len(preview['deleted'])}")
        task = ok("/api/v1/tasks", {"name": "演示任务", "source": str(source),
                                    "destination": str(base / "scheduled"), "options": {"mode": "incremental"}}, "POST")
        schedule = ok("/api/v1/schedules", {"taskId": task["id"], "timezone": "Asia/Shanghai",
                                            "expression": "*/5 * * * *", "enabled": True}, "POST")
        runs = ok("/api/v1/schedules/" + schedule["id"] + "/next-runs")
        print(f"[P6] schedule        : {schedule['expression']} next={runs[0]}")
        ok("/api/v1/schedules/" + schedule["id"], method="DELETE")

        # P7
        credential = ok("/api/v1/secrets", {"value": "demo\ndemo-password", "purpose": "remote_auth"}, "POST")
        remote_cfg = ok("/api/v1/remotes", {
            "name": "demo", "endpoint": remote.url, "credentialRef": credential["secretRef"],
            "caFile": tls.cafile}, "POST")
        connection = ok("/api/v1/remotes/" + remote_cfg["id"] + "/test", {}, "POST")
        upload = wait(accepted("/api/v1/remotes/" + remote_cfg["id"] + "/upload", {
            "snapshot": second, "destination": remote_cfg["id"]}))
        remote_snapshot = upload["result"]["path"]
        catalog = ok("/api/v1/remotes/" + remote_cfg["id"] + "/snapshots")
        download_to = base / "remote-download"
        downloaded = wait(accepted("/api/v1/remotes/" + remote_cfg["id"] + "/download", {
            "snapshot": remote_snapshot, "snapshotId": remote_cfg["id"], "destination": str(download_to)}))
        same = (download_to / "data" / "说明.txt").read_bytes() == (source / "说明.txt").read_bytes()
        print(f"\n[P7] remote test     : connected={connection['connected']} tls={connection['tlsVerified']}")
        print(f"[P7] upload          : {upload['state']} remote={remote_snapshot} catalog={len(catalog)}")
        print(f"[P7] download        : {downloaded['state']} files={downloaded['result']['files']} identical={same}")
        ok("/api/v1/remotes/" + remote_cfg["id"], method="DELETE")
        ok("/api/v1/secrets/" + credential["secretRef"], method="DELETE")

        overview = ok("/api/v1/workspace/overview")
        print(f"\n[summary] versions={overview['snapshots']} files={overview['backedUpFiles']} "
              f"activity={len(ok('/api/v1/activity'))}")
        print("artifacts are kept under:", base)
        return 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        remote.stop()


if __name__ == "__main__":
    raise SystemExit(main())
