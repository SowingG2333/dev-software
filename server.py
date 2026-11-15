"""Local demo bridge. All backup/restore I/O is performed by the C++ executable.

Run: python3 server.py --port 4173
Only binds loopback. This development bridge is not an internet-facing service.
"""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import tempfile
from repository import Repository
from native_options import native_options
from urllib.parse import parse_qs, urlsplit
import uuid
from api_contract import OPERATIONS, SCHEMAS, capabilities, match_operation, openapi, unsupported_option, validate

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from build import build, ROOT  # noqa: E402

STATIC_FILES = {"index.html", "app.js", "state.js", "native.js", "styles.css", "backend-api.js", "workspace-store.js", "features.js", "desktop-transport.js"}


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, data_dir=None):
        self.binary = build()
        self.token = secrets.token_urlsafe(32)
        self.temporary_data = tempfile.TemporaryDirectory(prefix='backnest-service-') if port == 0 and data_dir is None else None
        self.repository = Repository(data_dir or (self.temporary_data.name if self.temporary_data else ROOT / '.backnest'))
        self.jobs = self.repository.recover_jobs()
        self.workers = []
        self.closing = False
        self.controls = self.repository.directory / 'controls'
        self.controls.mkdir(exist_ok=True)
        self.jobs_lock = threading.Lock()
        self.engine_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), Handler)

    def start_job(self, operation, arguments, options=None):
        content = native_options(options or {})
        if self.closing or not self.engine_lock.acquire(blocking=False):
            raise ValueError("已有备份或还原正在运行，请等待完成。")
        job_id = uuid.uuid4().hex
        with self.jobs_lock:
            self.jobs[job_id] = {"id": job_id, "operation": operation, "state": "running", "phase": "starting",
                                 "files": 0, "totalFiles": 0, "bytes": 0, "totalBytes": 0, "path": ""}
        self.repository.save_job(self.jobs[job_id])
        worker = threading.Thread(target=self.run_job, args=(job_id, operation, arguments, content), daemon=True)
        self.workers.append(worker)
        worker.start()
        return job_id

    def run_job(self, job_id, operation, arguments, content):
        process = None
        try:
            option_file = self.controls / (job_id + '.options')
            option_file.write_text(content, encoding='utf-8')
            environment = {**os.environ, 'BACKNEST_OPTIONS': str(option_file), 'BACKNEST_DECISION': str(self.controls / job_id)}
            # Argument array only: user paths are never interpreted as shell code.
            process = subprocess.Popen([str(self.binary), operation] + arguments, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", env=environment)
            result = None
            failure = None
            for line in process.stdout:
                event = json.loads(line)
                with self.jobs_lock:
                    if event["event"] == "progress":
                        self.jobs[job_id].update({k: v for k, v in event.items() if k != "event"})
                    elif event["event"] == "result":
                        result = {k: v for k, v in event.items() if k != "event"}
                    elif event["event"] == "error":
                        failure = event.get("message", "C++ 引擎执行失败。")
            code = process.wait()
            if code or failure or not result:
                raise RuntimeError(failure or "C++ 引擎未返回成功结果。")
            result["completedAt"] = datetime.now(timezone.utc).isoformat()
            with self.jobs_lock:
                self.jobs[job_id].update(state="completed", phase="completed", result=result)
        except Exception as error:
            if process and process.poll() is None:
                process.terminate()
                process.wait()
            with self.jobs_lock:
                accepted = self.jobs[job_id].pop('cancelRequested', False)
                self.jobs[job_id].update(state='cancelled' if accepted else 'failed', phase='cancelled' if accepted else 'failed', error=str(error))
        finally:
            if process and process.stdout:
                process.stdout.close()
            with self.jobs_lock:
                self.jobs[job_id].pop('cancelRequested', None)
                self.repository.save_job(self.jobs[job_id])
            (self.controls / (job_id + '.options')).unlink(missing_ok=True)
            control = self.controls / job_id
            if control.is_dir():
                control.rmdir()
            self.engine_lock.release()
            with self.jobs_lock:
                # Bound retained results; never discard the active job.
                for old in list(self.jobs)[:-100]:
                    if self.jobs[old]["state"] != "running":
                        del self.jobs[old]

    def cancel_job(self, job_id):
        with self.jobs_lock:
            job = self.jobs.get(job_id)
            if not job or job['state'] != 'running':
                raise ValueError('任务已结束或不存在。')
            if job.get('cancelRequested'):
                return
            try:
                (self.controls / job_id).mkdir()
            except FileExistsError:
                raise ValueError('任务已进入提交阶段，不能取消；请等待最终结果。')
            job['cancelRequested'] = True
            self.repository.save_job(job)

    def preview(self, body):
        content = native_options({key: body[key] for key in ('filters', 'fileTypes') if key in body})
        with tempfile.TemporaryDirectory(prefix='backnest-preview-') as directory:
            path = Path(directory) / 'options'
            path.write_text(content, encoding='utf-8')
            env = {**os.environ, 'BACKNEST_OPTIONS': str(path), 'BACKNEST_DECISION': ''}
            run = subprocess.run([str(self.binary), 'preview', body['source']], capture_output=True,
                                 encoding='utf-8', timeout=120, env=env)
            events = [json.loads(line) for line in run.stdout.splitlines()]
            if run.returncode or not events:
                raise ValueError(events[-1].get('message', '扫描失败。') if events else '扫描失败。')
            return {key: value for key, value in events[-1].items() if key != 'event'}

    def server_close(self):
        self.closing = True
        for job in list(self.jobs.values()):
            if job['state'] == 'running':
                try:
                    self.cancel_job(job['id'])
                except ValueError:
                    pass
        for worker in self.workers:
            worker.join()
        super().server_close()
        self.repository.close()
        if self.temporary_data:
            self.temporary_data.cleanup()


class Handler(BaseHTTPRequestHandler):
    server_version = "BackNestLocal/0.2"

    def log_message(self, fmt, *args):
        # Do not echo tokens or local file paths into access logs.
        pass

    def send_data(self, status, data, content_type="application/json; charset=utf-8"):
        # Consume a bounded rejected request body before closing the connection.
        # Otherwise Windows can reset the socket and discard the 4xx/501 response.
        if not getattr(self, '_body_consumed', False):
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = 0
            if 0 < length <= 65536:
                self.connection.settimeout(5)
                try:
                    self.rfile.read(length)
                except (OSError, TimeoutError):
                    pass
            self._body_consumed = True
        if isinstance(data, (dict, list)):
            data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        elif isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' qrc:; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(data)

    def local_request(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            self.contract_error(403, "只允许本地访问。", "FORBIDDEN")
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + host for host in hosts}:
            self.contract_error(403, "拒绝跨来源请求。", "FORBIDDEN")
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.contract_error(403, "拒绝跨站请求。", "FORBIDDEN")
            return False
        return True

    def authorized(self):
        if not secrets.compare_digest(self.headers.get("X-BackNest-Token", ""), self.server.token):
            self.contract_error(403, "本地会话已过期，请刷新页面。", "FORBIDDEN")
            return False
        return True

    def contract_error(self, status, message, code, operation_id=None, capability=None):
        result = {"error": message, "code": code}
        if operation_id:
            result["operationId"] = operation_id
        if capability:
            result["capability"] = capability
        self.send_data(status, result)

    def read_body(self, required=True):
        length = int(self.headers.get("Content-Length", "0"))
        if not required and not length:
            return {}
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            raise ValueError("请求必须使用 JSON。")
        if not 0 < length <= (2 * 1024 * 1024 if urlsplit(self.path).path == '/api/v1/workspace/state' else 65536):
            raise ValueError("请求大小不合法。")
        self._body_consumed = True
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError("请求必须是 JSON 对象。")
        return body

    def send_directories(self, query):
        value = query.get("path", [str(ROOT)])[0]
        directory = Path(value).expanduser().resolve(strict=True)
        if not directory.is_dir():
            raise ValueError("请选择目录。")
        items = [{"name": child.name, "path": str(child)} for child in directory.iterdir()
                 if not child.is_symlink() and child.is_dir()]
        items.sort(key=lambda item: item["name"].casefold())
        self.send_data(200, {"path": str(directory), "parent": str(directory.parent), "directories": items})

    def start_native_operation(self, operation_id, body):
        operation, schema, names = {
            "backup.start": ("backup", "BackupRequest", ["source", "destination"]),
            "restore.start": ("restore", "RestoreRequest", ["snapshot", "destination"]),
            "snapshots.verify": ("verify", "SnapshotRequest", ["snapshot"]),
        }[operation_id]
        validate(body, SCHEMAS[schema])
        unsupported = unsupported_option(operation_id, body)
        if unsupported:
            self.contract_error(501, "该选项尚未实现，未执行文件操作。", "NOT_IMPLEMENTED", operation_id, unsupported)
            return
        args = []
        for name in names:
            path = Path(body[name]).expanduser()
            if not path.is_absolute():
                raise ValueError("请使用绝对路径，或点击“浏览”选择本机目录。")
            args.append(str(path))
        self.send_data(202, {"id": self.server.start_job(operation, args, body.get('options', {}))})

    def handle_contract(self, method):
        url = urlsplit(self.path)
        if not url.path.startswith("/api/v1/"):
            return False
        if not self.authorized():
            return True
        operation, parameters = match_operation(method, url.path)
        if not operation:
            known_path = any(match_operation(candidate, url.path)[0] for candidate in {op["method"] for op in OPERATIONS})
            self.contract_error(405 if known_path else 404, "接口不支持该方法。" if known_path else "接口不存在。", "METHOD_NOT_ALLOWED" if known_path else "NOT_FOUND")
            return True
        operation_id = operation["id"]
        if not operation["implemented"]:
            self.contract_error(501, "接口已预留，后端功能尚未实现。", "NOT_IMPLEMENTED", operation_id, operation_id)
            return True
        try:
            if operation_id == "system.capabilities":
                self.send_data(200, capabilities())
            elif operation_id == "system.contract":
                self.send_data(200, openapi())
            elif operation_id == "filesystem.directories":
                self.send_directories(parse_qs(url.query))
            elif operation_id == 'workspace.load':
                self.send_data(200, self.server.repository.load())
            elif operation_id == 'workspace.save':
                body = self.read_body()
                validate(body, SCHEMAS['WorkspaceSave'])
                self.send_data(200, self.server.repository.save(body['state'], body['revision']))
            elif operation_id == 'jobs.cancel':
                try:
                    self.server.cancel_job(parameters['jobId'])
                    self.send_data(202, {'id': parameters['jobId']})
                except ValueError as error:
                    self.contract_error(409, str(error), 'CONFLICT', operation_id)
            elif operation_id in ('filters.preview', 'filters.validate', 'fileTypes.inspect'):
                body = self.read_body()
                validate(body, SCHEMAS['ScanRequest'])
                preview = self.server.preview(body)
                self.send_data(200, {'valid': True, 'errors': []} if operation_id == 'filters.validate' else
                               {'entries': preview['included'] + preview['excluded'], 'unsupported': [], 'warnings': []} if operation_id == 'fileTypes.inspect' else preview)
            elif operation_id in ('archive.algorithms', 'compression.algorithms'):
                group = operation_id.split('.')[0]
                self.send_data(200, [{'id': capabilities()['algorithms'][group][0], 'version': '2' if group == 'archive' else '1',
                                     'selfImplemented': True, 'supportedPlatforms': ['Windows', 'macOS', 'Linux']}])
            elif operation_id in ('archive.pack', 'archive.unpack', 'compression.compress', 'compression.decompress'):
                body = self.read_body()
                validate(body, SCHEMAS['CodecRequest'])
                if body.get('secretRef'):
                    raise ValueError('此算法不支持加密。')
                group, action = operation_id.split('.')
                options = {group: body['options']}
                native_options(options)
                if not all(Path(body[name]).is_absolute() for name in ('source', 'destination')):
                    raise ValueError('请使用绝对路径。')
                self.send_data(202, {'id': self.server.start_job(action, [body['source'], body['destination']], options)})
            elif operation_id == "jobs.get":
                with self.server.jobs_lock:
                    job = self.server.jobs.get(parameters["jobId"])
                    if job:
                        self.send_data(200, job)
                    else:
                        self.contract_error(404, "运行记录不存在，请检查目标目录。", "NOT_FOUND", operation_id)
            else:
                self.start_native_operation(operation_id, self.read_body())
        except (OSError, ValueError, TypeError) as error:
            self.contract_error(400, str(error), "INVALID_ARGUMENT", operation_id)
        except Exception:
            self.contract_error(500, "后端执行异常，请检查本机服务。", "INTERNAL_ERROR", operation_id)
        return True

    def do_GET(self):
        if not self.local_request():
            return
        if self.handle_contract("GET"):
            return
        url = urlsplit(self.path)
        if url.path == "/api/session":
            self.send_data(200, {"token": self.server.token, "engine": "C++17", "version": "0.2", "home": str(Path.home()),
                                 "workspace": str(ROOT), "platform": sys.platform})
            return
        if url.path.startswith("/api/"):
            if not self.authorized():
                return
            if url.path.startswith("/api/jobs/"):
                with self.server.jobs_lock:
                    job = self.server.jobs.get(url.path.rsplit("/", 1)[-1])
                    self.send_data(200 if job else 404, job or {"error": "任务运行记录不存在，请检查目标目录。"})
                return
            if url.path == "/api/directories":
                try:
                    value = parse_qs(url.query).get("path", [str(ROOT)])[0]
                    directory = Path(value).expanduser().resolve(strict=True)
                    if not directory.is_dir():
                        raise ValueError("请选择目录。")
                    items = []
                    for child in directory.iterdir():
                        if not child.is_symlink() and child.is_dir():
                            items.append({"name": child.name, "path": str(child)})
                    items.sort(key=lambda item: item["name"].casefold())
                    self.send_data(200, {"path": str(directory), "parent": str(directory.parent), "directories": items})
                except (OSError, ValueError) as error:
                    self.send_data(400, {"error": str(error)})
                return
            self.send_data(404, {"error": "接口不存在。"})
            return
        name = url.path.lstrip("/") or "index.html"
        if name not in STATIC_FILES:
            self.send_data(404, {"error": "文件不存在。"})
            return
        path = ROOT / name
        self.send_data(200, path.read_bytes(), mimetypes.guess_type(name)[0] or "application/octet-stream")

    def do_POST(self):
        if not self.local_request():
            return
        if self.handle_contract("POST"):
            return
        if not self.authorized():
            return
        try:
            operation_id = {"/api/backup": "backup.start", "/api/restore": "restore.start", "/api/verify": "snapshots.verify"}.get(self.path)
            if not operation_id:
                self.send_data(404, {"error": "接口不存在。"})
                return
            self.start_native_operation(operation_id, self.read_body())
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self.send_data(400, {"error": str(error)})
        except Exception as error:
            self.send_data(500, {"error": str(error)})

    def do_PATCH(self):
        if self.local_request() and not self.handle_contract("PATCH"):
            self.contract_error(404, "接口不存在。", "NOT_FOUND")

    def do_DELETE(self):
        if self.local_request() and not self.handle_contract("DELETE"):
            self.contract_error(404, "接口不存在。", "NOT_FOUND")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument('--data-dir', type=Path, default=ROOT / '.backnest')
    parser.add_argument('--desktop-parent', action='store_true')
    args = parser.parse_args()
    server = DemoServer(args.port, args.data_dir)
    print(f"BackNest C++ basic demo: http://127.0.0.1:{server.server_port}", flush=True)
    print("Backup/restore writes are performed only after an explicit UI action. Ctrl+C to stop.", flush=True)
    if args.desktop_parent:
        def parent_closed():
            sys.stdin.readline()
            server.shutdown()
        threading.Thread(target=parent_closed, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
