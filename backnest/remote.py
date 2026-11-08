"""HTTPS remote backup client and protocol helpers.

P7 is deliberately small but real: a remote HTTPS service stores one snapshot
directory per user, uploads are chunked and resumable, every file is verified
with SHA-256, downloads support HTTP Range resume, and users cannot read each
other's snapshots.

The remote service is implemented in `backnest.remote_server`; this module only
contains the client and shared path/checksum helpers.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
from pathlib import Path
import ssl
from typing import Callable
from urllib.parse import quote, urlsplit

CHUNK_BYTES = 256 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024 * 1024
MAX_FILES = 200_000


class RemoteError(RuntimeError):
    """Raised for connection, authentication, protocol or integrity failures."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_files(snapshot: Path) -> list[dict]:
    """List `manifest.bkn` and `data/**` files of a BACKNEST snapshot."""
    snapshot = Path(snapshot).expanduser()
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise RemoteError("备份版本目录不存在。")
    manifest = snapshot / "manifest.bkn"
    data = snapshot / "data"
    if manifest.is_symlink() or not manifest.is_file():
        raise RemoteError("备份版本缺少 manifest.bkn。")
    if data.is_symlink() or not data.is_dir():
        raise RemoteError("备份版本缺少 data 目录。")
    files: list[dict] = []

    def add(path: Path, relative: str) -> None:
        if path.is_symlink() or not path.is_file():
            raise RemoteError("只支持普通文件：" + relative)
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise RemoteError("文件超过远端传输大小限制：" + relative)
        files.append({"path": relative, "size": size, "sha256": sha256_file(path)})

    def walk(directory: Path, prefix: str) -> None:
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda item: item.name)
        except OSError as error:
            raise RemoteError(f"无法读取备份目录：{directory}") from error
        for item in children:
            relative = item.name if not prefix else prefix + "/" + item.name
            info = item.stat(follow_symlinks=False)
            if item.is_symlink():
                raise RemoteError("远端传输不支持符号链接。")
            if info.st_mode & 0o040000:
                walk(Path(item.path), relative)
            elif info.st_mode & 0o100000:
                add(Path(item.path), relative)
            else:
                raise RemoteError("远端传输不支持特殊文件。")

    add(manifest, "manifest.bkn")
    walk(data, "data")
    if len(files) > MAX_FILES:
        raise RemoteError("文件数量超过远端传输限制。")
    return files


class RemoteClient:
    def __init__(self, endpoint: str, *, token: str = "", cafile: str | None = None, timeout: float = 30):
        parsed = urlsplit(endpoint)
        if parsed.scheme != "https" or not parsed.hostname:
            raise RemoteError("远端地址必须是 https:// 地址。")
        self.endpoint = endpoint.rstrip("/")
        self.host = parsed.hostname
        self.port = parsed.port or 443
        self.base_path = parsed.path.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.context = ssl.create_default_context(cafile=cafile) if cafile else ssl.create_default_context()

    def _request(self, method: str, path: str, body: bytes | None = None,
                 headers: dict | None = None, *, json_body=None):
        headers = dict(headers or {})
        if self.token:
            headers.setdefault("Authorization", "Bearer " + self.token)
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if body is not None:
            headers["Content-Length"] = str(len(body))
        connection = http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout, context=self.context)
        try:
            connection.request(method, self.base_path + path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
            content_type = response.getheader("Content-Type", "")
            if response.status >= 400:
                try:
                    payload = json.loads(data.decode("utf-8"))
                    message = payload.get("error", "远端请求失败。")
                except Exception:
                    message = data.decode("utf-8", "replace") or "远端请求失败。"
                raise RemoteError(f"{response.status}: {message}")
            if "application/json" in content_type:
                return json.loads(data.decode("utf-8")) if data else {}
            return data
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            raise RemoteError(f"无法连接远端：{error}") from error
        finally:
            connection.close()

    # ------------------------------------------------------------------ auth
    def health(self) -> dict:
        return self._request("GET", "/api/v1/health")

    def login(self, username: str, password: str) -> dict:
        result = self._request("POST", "/api/v1/login", json_body={"username": username, "password": password})
        self.token = result["token"]
        return result

    def logout(self) -> dict:
        return self._request("POST", "/api/v1/logout")

    # ------------------------------------------------------------- snapshots
    def catalog(self) -> list[dict]:
        return self._request("GET", "/api/v1/catalog")

    def create_snapshot(self, name: str, files: list[dict]) -> dict:
        return self._request("POST", "/api/v1/snapshots", json_body={"name": name, "files": files})

    def snapshot(self, snapshot_id: str) -> dict:
        return self._request("GET", "/api/v1/snapshots/" + quote(snapshot_id, safe=""))

    def status(self, snapshot_id: str) -> dict:
        return self._request("GET", "/api/v1/snapshots/" + quote(snapshot_id, safe="") + "/status")

    def delete(self, snapshot_id: str) -> dict:
        return self._request("DELETE", "/api/v1/snapshots/" + quote(snapshot_id, safe=""))

    def upload_file(self, snapshot_id: str, relative: str, source: Path, *, offset: int = 0,
                    progress: Callable[[int, int], None] | None = None,
                    retries: int = 3) -> int:
        source = Path(source)
        size = source.stat().st_size
        position = offset
        total = size
        with source.open("rb") as stream:
            stream.seek(position)
            while position < size:
                chunk = stream.read(CHUNK_BYTES)
                if not chunk:
                    raise RemoteError("源文件在传输过程中变短。")
                attempt = 0
                while True:
                    try:
                        self._request(
                            "PUT",
                            "/api/v1/snapshots/" + quote(snapshot_id, safe="") + "/files?path=" + quote(relative, safe="/"),
                            body=chunk,
                            headers={"X-Offset": str(position)},
                            json_body=None,
                        )
                        break
                    except RemoteError:
                        attempt += 1
                        if attempt >= retries:
                            raise
                position += len(chunk)
                if progress:
                    progress(position, total)
        return position

    def finalize(self, snapshot_id: str) -> dict:
        return self._request("POST", "/api/v1/snapshots/" + quote(snapshot_id, safe="") + "/finalize")

    def upload_snapshot(self, snapshot: Path, *, name: str = "", resume_id: str = "",
                        progress: Callable[[int, int], None] | None = None,
                        retries: int = 3) -> dict:
        files = snapshot_files(snapshot)
        total = sum(item["size"] for item in files)
        done = 0
        if resume_id:
            remote_id = resume_id
            state = self.status(remote_id)
            received = state.get("files", {})
        else:
            meta = self.create_snapshot(name or Path(snapshot).name, files)
            remote_id = meta["id"]
            received = {item["path"]: 0 for item in files}
        for item in files:
            offset = int(received.get(item["path"], 0))
            done += offset
            if offset > item["size"]:
                raise RemoteError("远端已有数据超过文件大小，拒绝续传。")
            local_path = Path(snapshot).joinpath(*item["path"].split("/"))
            if offset < item["size"]:
                self.upload_file(remote_id, item["path"], local_path, offset=offset, retries=retries,
                                 progress=(lambda current, _total, base=done: progress(base + current, total)) if progress else None)
            done += item["size"] - offset
            if progress:
                progress(done, total)
        result = self.finalize(remote_id)
        result["bytes"] = total
        return result

    # -------------------------------------------------------------- download
    def download_file(self, snapshot_id: str, relative: str, destination: Path, *,
                      offset: int = 0, progress: Callable[[int, int], None] | None = None,
                      retries: int = 3) -> int:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if offset and (not destination.is_file() or destination.stat().st_size != offset):
            raise RemoteError("续传目标的已有大小与偏移不一致。")
        position = offset
        mode = "ab" if position else "wb"
        with destination.open(mode) as stream:
            while True:
                headers = {"Range": f"bytes={position}-"} if position else {}
                attempt = 0
                while True:
                    try:
                        connection = http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout, context=self.context)
                        connection.request("GET", self.base_path + "/api/v1/snapshots/" + quote(snapshot_id, safe="") +
                                           "/files?path=" + quote(relative, safe="/"),
                                           headers={**headers, "Authorization": "Bearer " + self.token})
                        response = connection.getresponse()
                        if response.status not in (200, 206):
                            raw = response.read()
                            raise RemoteError(f"{response.status}: {raw.decode('utf-8', 'replace')}")
                        length = int(response.getheader("Content-Length", "0"))
                        received = 0
                        while True:
                            chunk = response.read(CHUNK_BYTES)
                            if not chunk:
                                break
                            stream.write(chunk)
                            received += len(chunk)
                            if progress:
                                progress(position + received, None)
                        connection.close()
                        position += received
                        if length == 0 or received == 0:
                            break
                        break
                    except (OSError, ssl.SSLError, http.client.HTTPException, RemoteError):
                        try:
                            stream.flush()
                            stream.truncate(position)
                            stream.seek(position)
                        except OSError:
                            pass
                        attempt += 1
                        if attempt >= retries:
                            raise
                # A range response with unknown total: stop when the server says
                # Content-Range total has been reached.
                break
        return position

    def download_snapshot(self, snapshot_id: str, destination: Path, *,
                          progress: Callable[[int, int], None] | None = None) -> dict:
        meta = self.snapshot(snapshot_id)
        destination = Path(destination)
        if destination.exists() and (destination.is_symlink() or not destination.is_dir() or any(destination.iterdir())):
            raise RemoteError("下载目标必须是新目录或空目录。")
        destination.mkdir(parents=True, exist_ok=True)
        total = sum(int(item["size"]) for item in meta.get("files", []))
        done = 0
        for item in meta.get("files", []):
            target = destination.joinpath(*item["path"].split("/"))
            self.download_file(snapshot_id, item["path"], target, offset=0,
                               progress=(lambda current, _total, base=done: progress(base + current, total)) if progress else None)
            actual = sha256_file(target)
            if actual != item["sha256"]:
                raise RemoteError("下载文件 SHA-256 不一致：" + item["path"])
            done += int(item["size"])
            if progress:
                progress(done, total)
        return {"id": snapshot_id, "path": str(destination), "files": len(meta.get("files", [])),
                "bytes": total, "verified": True}
