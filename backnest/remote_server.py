"""Minimal HTTPS remote storage service for P7.

The service is intentionally separate from the local demo bridge.  It binds to
loopback by default, speaks HTTPS, requires a bearer token issued by its own
login endpoint, and stores snapshots under a per-user directory.  Uploads are
chunked and resumable; every file is checked with SHA-256 at finalize time.

Run standalone:

    python scripts/remote_server.py --state .backnest/remote --port 4780 \
        --cert tests/fixtures/tls/server.pem --key tests/fixtures/tls/server.key \
        --add-user demo:demo-password

The test certificate is self-signed and test-only; production deployments must
provide a real certificate and trust chain.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlsplit

MAX_JSON = 64 * 1024
MAX_CHUNK = 8 * 1024 * 1024
MAX_FILES = 200_000
MAX_FILE = 8 * 1024 * 1024 * 1024
TOKEN_TTL = 3600
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RemoteServiceError(ValueError):
    pass


def _safe_relative(value: str) -> str:
    if not value or len(value) > 4096 or "\0" in value or "\\" in value or value.startswith("/"):
        raise RemoteServiceError("非法文件路径。")
    for part in value.split("/"):
        if part in ("", ".", ".."):
            raise RemoteServiceError("非法文件路径。")
    return value


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000).hex()


class RemoteService:
    def __init__(self, root: Path, certfile: str, keyfile: str, host: str = "127.0.0.1", port: int = 0):
        self.root = Path(root)
        self.certfile = certfile
        self.keyfile = keyfile
        self.host = host
        self.port = port
        self.users_path = self.root / "users.json"
        self.users_root = self.root / "users"
        self.root.mkdir(parents=True, exist_ok=True)
        self.users_root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.tokens: dict[str, tuple[str, float]] = {}
        self.users = self._load_users()
        self.httpd = None
        self.thread = None

    # ----------------------------------------------------------------- users
    def _load_users(self) -> dict:
        if not self.users_path.exists():
            return {}
        try:
            data = json.loads(self.users_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_users(self) -> None:
        temporary = self.users_path.with_name(self.users_path.name + ".partial-" + secrets.token_hex(4))
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(self.users, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.users_path)

    def add_user(self, username: str, password: str, roles: list[str] | None = None) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username or ""):
            raise RemoteServiceError("用户名不合法。")
        if not password or len(password) > 4096:
            raise RemoteServiceError("密码不合法。")
        salt = secrets.token_bytes(16)
        with self.lock:
            self.users[username] = {"salt": salt.hex(), "hash": _hash_password(password, salt),
                                    "roles": roles or ["user"]}
            self._save_users()
        return {"username": username, "roles": roles or ["user"]}

    def authenticate(self, username: str, password: str) -> str | None:
        record = self.users.get(username)
        if not record:
            return None
        salt = bytes.fromhex(record["salt"])
        if hmac.compare_digest(record["hash"], _hash_password(password, salt)):
            return username
        return None

    def issue_token(self, username: str) -> dict:
        token = secrets.token_urlsafe(32)
        expires = time.time() + TOKEN_TTL
        with self.lock:
            self.tokens[token] = (username, expires)
        return {"token": token, "expiresAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires)),
                "user": {"username": username, "roles": self.users[username].get("roles", ["user"])}}

    def validate_token(self, token: str) -> str | None:
        with self.lock:
            entry = self.tokens.get(token)
            if not entry:
                return None
            username, expires = entry
            if expires < time.time():
                self.tokens.pop(token, None)
                return None
            return username

    def revoke_token(self, token: str) -> bool:
        with self.lock:
            return self.tokens.pop(token, None) is not None

    # ------------------------------------------------------------- snapshots
    def user_root(self, username: str) -> Path:
        return self.users_root / username

    def snapshot_root(self, username: str, snapshot_id: str) -> Path:
        if not SAFE_ID.fullmatch(snapshot_id or ""):
            raise RemoteServiceError("快照标识不合法。")
        return self.user_root(username) / snapshot_id

    def meta_path(self, username: str, snapshot_id: str) -> Path:
        return self.snapshot_root(username, snapshot_id) / "meta.json"

    def load_meta(self, username: str, snapshot_id: str) -> dict:
        path = self.meta_path(username, snapshot_id)
        if not path.is_file():
            raise FileNotFoundError
        return json.loads(path.read_text(encoding="utf-8"))

    def save_meta(self, username: str, snapshot_id: str, meta: dict) -> None:
        path = self.meta_path(username, snapshot_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".partial-" + secrets.token_hex(4))
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(meta, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def create_snapshot(self, username: str, name: str, files: list[dict]) -> dict:
        if not isinstance(files, list) or not files or len(files) > MAX_FILES:
            raise RemoteServiceError("文件清单不合法。")
        seen = set()
        normalized = []
        for item in files:
            relative = _safe_relative(str(item.get("path", "")))
            if relative in seen:
                raise RemoteServiceError("文件清单包含重复路径。")
            seen.add(relative)
            size = int(item.get("size", -1))
            if size < 0 or size > MAX_FILE:
                raise RemoteServiceError("文件大小不合法。")
            digest = str(item.get("sha256", ""))
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise RemoteServiceError("文件 SHA-256 不合法。")
            normalized.append({"path": relative, "size": size, "sha256": digest, "received": 0})
        snapshot_id = "snap-" + secrets.token_hex(8)
        root = self.snapshot_root(username, snapshot_id)
        (root / "files").mkdir(parents=True, exist_ok=False)
        meta = {"id": snapshot_id, "name": name[:200] or snapshot_id, "owner": username,
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "complete": False, "files": normalized}
        self.save_meta(username, snapshot_id, meta)
        return meta

    def update_received(self, username: str, snapshot_id: str, relative: str, received: int) -> dict:
        meta = self.load_meta(username, snapshot_id)
        for item in meta["files"]:
            if item["path"] == relative:
                item["received"] = received
                self.save_meta(username, snapshot_id, meta)
                return meta
        raise RemoteServiceError("快照中没有该文件。")

    def finalize(self, username: str, snapshot_id: str) -> dict:
        meta = self.load_meta(username, snapshot_id)
        root = self.snapshot_root(username, snapshot_id) / "files"
        for item in meta["files"]:
            path = root.joinpath(*item["path"].split("/"))
            if not path.is_file():
                raise RemoteServiceError("远端缺少文件：" + item["path"])
            if path.stat().st_size != item["size"]:
                raise RemoteServiceError("远端文件大小不一致：" + item["path"])
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            if digest.hexdigest() != item["sha256"]:
                raise RemoteServiceError("远端文件 SHA-256 不一致：" + item["path"])
        meta["complete"] = True
        self.save_meta(username, snapshot_id, meta)
        return meta

    def catalog(self, username: str) -> list[dict]:
        root = self.user_root(username)
        if not root.is_dir():
            return []
        result = []
        for item in sorted(root.iterdir(), key=lambda path: path.stat().st_mtime_ns if path.is_dir() else 0, reverse=True):
            try:
                meta = self.load_meta(username, item.name)
                result.append({key: meta[key] for key in ("id", "name", "owner", "createdAt", "complete")})
            except (OSError, ValueError, FileNotFoundError):
                continue
        return result

    def delete(self, username: str, snapshot_id: str) -> None:
        import shutil
        root = self.snapshot_root(username, snapshot_id)
        if not root.is_dir():
            raise FileNotFoundError
        shutil.rmtree(root)

    # ------------------------------------------------------------------ http
    def start(self) -> None:
        service = self

        class Handler(RemoteHandler):
            server_service = service

        httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        httpd.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.certfile, self.keyfile)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        self.httpd = httpd
        self.port = httpd.server_address[1]
        self.thread = threading.Thread(target=httpd.serve_forever, name="backnest-remote", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=3)

    @property
    def url(self) -> str:
        return f"https://{self.host}:{self.port}"


class RemoteHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "BackNestRemote/1.0"

    @property
    def service(self) -> RemoteService:
        return self.server_service

    def log_message(self, fmt, *args):
        pass

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str, code: str = "ERROR"):
        self._json(status, {"error": message, "code": code})

    def _body(self, limit: int = MAX_JSON) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > limit:
            raise RemoteServiceError("请求体过大。")
        return self.rfile.read(length)

    def _json_body(self) -> dict:
        raw = self._body()
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise RemoteServiceError("请求必须是 JSON 对象。")
        return data

    def _user(self) -> str | None:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            self._error(401, "需要远端登录令牌。", "UNAUTHORIZED")
            return None
        username = self.service.validate_token(header[7:].strip())
        if not username:
            self._error(401, "远端登录已过期。", "UNAUTHORIZED")
            return None
        return username

    def _parts(self):
        return [unquote(part) for part in urlsplit(self.path).path.split("/") if part]

    def _file_path(self) -> str:
        query = parse_qs(urlsplit(self.path).query)
        return _safe_relative(query.get("path", [""])[0])

    def do_GET(self):
        try:
            parts = self._parts()
            if parts == ["api", "v1", "health"]:
                return self._json(200, {"service": "backnest-remote", "version": "1.0", "tls": True})
            username = self._user()
            if not username:
                return
            if parts == ["api", "v1", "catalog"]:
                return self._json(200, self.service.catalog(username))
            if len(parts) >= 4 and parts[:3] == ["api", "v1", "snapshots"]:
                snapshot_id = parts[3]
                if len(parts) == 4:
                    return self._json(200, self.service.load_meta(username, snapshot_id))
                if len(parts) == 5 and parts[4] == "status":
                    meta = self.service.load_meta(username, snapshot_id)
                    return self._json(200, {"id": snapshot_id, "complete": meta.get("complete", False),
                                            "files": {item["path"]: item.get("received", 0) for item in meta["files"]}})
                if len(parts) == 5 and parts[4] == "files":
                    relative = self._file_path()
                    return self._download(username, snapshot_id, relative)
            return self._error(404, "接口不存在。", "NOT_FOUND")
        except FileNotFoundError:
            return self._error(404, "快照不存在。", "NOT_FOUND")
        except (RemoteServiceError, ValueError) as error:
            return self._error(400, str(error), "INVALID_ARGUMENT")
        except Exception:
            return self._error(500, "远端服务异常。", "INTERNAL_ERROR")

    def do_POST(self):
        try:
            parts = self._parts()
            if parts == ["api", "v1", "login"]:
                body = self._json_body()
                username = self.service.authenticate(str(body.get("username", "")), str(body.get("password", "")))
                if not username:
                    return self._error(401, "用户名或密码错误。", "UNAUTHORIZED")
                return self._json(200, self.service.issue_token(username))
            if parts == ["api", "v1", "logout"]:
                username = self._user()
                if not username:
                    return
                header = self.headers.get("Authorization", "")
                self.service.revoke_token(header[7:].strip())
                return self._json(200, {"ok": True})
            username = self._user()
            if not username:
                return
            if parts == ["api", "v1", "snapshots"]:
                body = self._json_body()
                return self._json(200, self.service.create_snapshot(username, str(body.get("name", "")),
                                                                     body.get("files", [])))
            if len(parts) == 5 and parts[:3] == ["api", "v1", "snapshots"] and parts[4] == "finalize":
                return self._json(200, self.service.finalize(username, parts[3]))
            return self._error(404, "接口不存在。", "NOT_FOUND")
        except FileNotFoundError:
            return self._error(404, "快照不存在。", "NOT_FOUND")
        except (RemoteServiceError, ValueError) as error:
            return self._error(400, str(error), "INVALID_ARGUMENT")
        except Exception:
            return self._error(500, "远端服务异常。", "INTERNAL_ERROR")

    def do_PUT(self):
        try:
            username = self._user()
            if not username:
                return
            parts = self._parts()
            if len(parts) == 5 and parts[:3] == ["api", "v1", "snapshots"] and parts[4] == "files":
                snapshot_id = parts[3]
                relative = self._file_path()
                offset = int(self.headers.get("X-Offset", "-1"))
                if offset < 0:
                    raise RemoteServiceError("缺少 X-Offset。")
                chunk = self._body(MAX_CHUNK)
                meta = self.service.load_meta(username, snapshot_id)
                item = next((entry for entry in meta["files"] if entry["path"] == relative), None)
                if item is None:
                    raise RemoteServiceError("快照中没有该文件。")
                target = self.service.snapshot_root(username, snapshot_id) / "files" / Path(*relative.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                current = target.stat().st_size if target.is_file() else 0
                if offset != current:
                    return self._error(409, f"续传偏移不匹配：期望 {current}。", "OFFSET_MISMATCH")
                if current + len(chunk) > item["size"]:
                    raise RemoteServiceError("写入超过文件大小。")
                with target.open("ab") as stream:
                    stream.write(chunk)
                self.service.update_received(username, snapshot_id, relative, target.stat().st_size)
                return self._json(200, {"received": target.stat().st_size})
            return self._error(404, "接口不存在。", "NOT_FOUND")
        except FileNotFoundError:
            return self._error(404, "快照不存在。", "NOT_FOUND")
        except (RemoteServiceError, ValueError) as error:
            return self._error(400, str(error), "INVALID_ARGUMENT")
        except Exception:
            return self._error(500, "远端服务异常。", "INTERNAL_ERROR")

    def do_DELETE(self):
        try:
            username = self._user()
            if not username:
                return
            parts = self._parts()
            if len(parts) == 4 and parts[:3] == ["api", "v1", "snapshots"]:
                self.service.delete(username, parts[3])
                return self._json(200, {"id": parts[3], "deleted": True})
            return self._error(404, "接口不存在。", "NOT_FOUND")
        except FileNotFoundError:
            return self._error(404, "快照不存在。", "NOT_FOUND")
        except (RemoteServiceError, ValueError) as error:
            return self._error(400, str(error), "INVALID_ARGUMENT")

    def _download(self, username: str, snapshot_id: str, relative: str):
        meta = self.service.load_meta(username, snapshot_id)
        item = next((entry for entry in meta["files"] if entry["path"] == relative), None)
        if item is None:
            return self._error(404, "快照中没有该文件。", "NOT_FOUND")
        path = self.service.snapshot_root(username, snapshot_id) / "files" / Path(*relative.split("/"))
        if not path.is_file():
            return self._error(404, "远端文件不存在。", "NOT_FOUND")
        size = path.stat().st_size
        start = 0
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes="):
            text = range_header[6:].split(",")[0]
            first = text.split("-", 1)[0]
            if first.isdigit():
                start = int(first)
        if start >= size:
            return self._error(416, "Range 超出文件大小。", "RANGE_NOT_SATISFIABLE")
        self.send_response(206 if range_header else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(size - start))
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as stream:
            stream.seek(start)
            while True:
                chunk = stream.read(256 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
