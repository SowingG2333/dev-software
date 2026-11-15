"""Native P2-P4 extensions for the P5-P7 application service."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from native_options import native_options

class NativeFeatures:
    def start_job(self, operation, arguments, options=None, *, on_success=None, on_failure=None, task_id=""):
        if not self.binary: raise ValueError("此操作需要 C++ 引擎。")
        content = native_options(options or {})
        if self._closing or not self.engine_lock.acquire(blocking=False):
            raise ValueError("已有备份或还原正在运行，请等待完成。")
        job_id = uuid.uuid4().hex
        with self.jobs_lock:
            self.jobs[job_id] = {"id": job_id, "operation": operation, "state": "running", "phase": "starting",
                                 "files": 0, "totalFiles": 0, "bytes": 0, "totalBytes": 0, "path": ""}
        self.repository.save_job(self.jobs[job_id])
        self.native_jobs.add(job_id)
        worker = threading.Thread(target=self.run_job, args=(job_id, operation, arguments, content, on_success, on_failure, task_id), daemon=True)
        self.workers.append(worker)
        worker.start()
        return job_id

    def run_job(self, job_id, operation, arguments, content, on_success, on_failure, task_id):
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
            if on_success:
                try: on_success(result, job_id, task_id)
                except Exception as error:
                    self.store.add_activity(title="结果登记失败", detail=str(error), level="warning", task_id=task_id)
            terminal = dict(state="completed", phase="completed", result=result)
        except Exception as error:
            if process and process.poll() is None:
                process.terminate()
                process.wait()
            with self.jobs_lock:
                accepted = self.jobs[job_id].pop('cancelRequested', False)
                terminal = dict(state='cancelled' if accepted else 'failed', phase='cancelled' if accepted else 'failed', error=str(error))
            if on_failure:
                try: on_failure(str(error), job_id, task_id)
                except Exception: pass
        finally:
            if process and process.stdout:
                process.stdout.close()
            (self.controls / (job_id + '.options')).unlink(missing_ok=True)
            control = self.controls / job_id
            with self.jobs_lock:
                if control.is_dir(): control.rmdir()
                self.engine_lock.release()
                self.jobs[job_id].pop('cancelRequested', None)
                self.jobs[job_id].update(terminal)
                self.repository.save_job(self.jobs[job_id])
                self.native_jobs.discard(job_id)
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
            if job_id not in self.native_jobs:
                raise ValueError('此扩展任务不支持中途取消，请等待完成。')
            if job.get('cancelRequested'):
                return
            try:
                (self.controls / job_id).mkdir()
            except FileExistsError:
                raise ValueError('任务已进入提交阶段，不能取消；请等待最终结果。')
            job['cancelRequested'] = True
            self.repository.save_job(job)

    def preview(self, body):
        if not self.binary: raise ValueError('筛选预览需要 C++ 引擎。')
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
