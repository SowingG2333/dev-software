"""Transactional local repository. SQLite is infrastructure, not a backup codec."""
import json
import sqlite3
from pathlib import Path
import threading
import os


class Repository:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.guard = open(self.directory / 'service.lock', 'a+b')
        self.guard.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.guard.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.guard.close()
            raise RuntimeError('该工作区已有服务运行，请关闭原服务或指定不同 --data-dir。')
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.directory / 'workspace.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, body TEXT)')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.execute('INSERT OR IGNORE INTO workspace VALUES(1,0,NULL)')
        self.db.commit()

    def load(self):
        with self.lock:
            revision, body = self.db.execute('SELECT revision,body FROM workspace WHERE id=1').fetchone()
            return {'revision': revision, 'state': json.loads(body) if body else None}

    def save(self, state, revision):
        if not isinstance(state, dict) or not isinstance(state.get('tasks'), list) or not isinstance(state.get('logs'), list) or not isinstance(state.get('settings'), dict):
            raise ValueError('Invalid workspace state.')
        if len(state['tasks']) > 1000 or len(state['logs']) > 1000:
            raise ValueError('Workspace exceeds record limit.')
        with self.lock, self.db:
            updated = self.db.execute('UPDATE workspace SET revision=revision+1,body=? WHERE id=1 AND revision=?',
                                      (json.dumps(state, ensure_ascii=False), revision)).rowcount
            if not updated:
                raise ValueError('配置已被另一个窗口修改，请刷新后重试。')
            return self.load()

    def save_job(self, job):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO jobs VALUES(?,?)', (job['id'], json.dumps(job, ensure_ascii=False)))
            self.db.execute('DELETE FROM jobs WHERE rowid NOT IN (SELECT rowid FROM jobs ORDER BY rowid DESC LIMIT 100)')

    def recover_jobs(self):
        with self.lock:
            jobs = {row[0]: json.loads(row[1]) for row in self.db.execute('SELECT id,body FROM jobs')}
            for job in jobs.values():
                if job['state'] == 'running':
                    job.update(state='failed', phase='interrupted', error='服务意外中断；请校验磁盘版本后导入，不推定成功。')
                    self.save_job(job)
            return jobs

    def close(self):
        with self.lock:
            self.db.close()
            self.guard.close()
