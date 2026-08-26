"""后台任务管理器 — P0-1 run_in_background（对标 DSH jobs / CC 后台 shell 简化版）。

提供：
- submit: 后台执行 bash 命令，立即返回 job_id
- list_jobs / status / cancel: 任务状态查询与取消

任务状态机: pending → running → completed | failed | cancelled
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BackgroundJob:
    """后台任务记录。"""

    job_id: str
    command: str
    status: JobStatus = JobStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    cancelled: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "job_id": self.job_id,
            "command": self.command,
            "status": self.status.value,
            "created_at": self.created_at,
            "duration": (self.finished_at - self.created_at) if self.finished_at else None,
        }
        if self.exit_code is not None:
            d["exit_code"] = self.exit_code
        if self.status == JobStatus.COMPLETED:
            d["stdout_preview"] = self.stdout[:200]
            d["stderr_preview"] = self.stderr[:200]
        elif self.error:
            d["error"] = self.error
        return d


class BackgroundTaskManager:
    """后台任务执行器 — 线程池 + 状态字典。"""

    def __init__(self, max_workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="bg")
        self._jobs: dict[str, BackgroundJob] = {}
        self._lock = threading.Lock()

    def submit(self, command: str, timeout: float = 300.0) -> str:
        """后台执行 bash 命令，立即返回 job_id。"""
        job_id = uuid4().hex[:12]
        job = BackgroundJob(job_id=job_id, command=command)
        with self._lock:
            self._jobs[job_id] = job
        self._pool.submit(self._run, job, timeout)
        return job_id

    def _run(self, job: BackgroundJob, timeout: float) -> None:
        """在线程池中执行命令（bash 由 BashExecutor 处理，此处用 subprocess 简化）。"""
        import subprocess

        job.status = JobStatus.RUNNING
        job.started_at = time.time()
        try:
            proc = subprocess.Popen(
                ["/bin/bash", "-c", job.command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                job.status = JobStatus.FAILED
                job.error = f"timeout after {timeout}s"
                job.finished_at = time.time()
                return
            job.stdout = stdout
            job.stderr = stderr
            job.exit_code = proc.returncode
            job.status = JobStatus.COMPLETED if proc.returncode == 0 else JobStatus.FAILED
            job.finished_at = time.time()
        except Exception as e:  # noqa: BLE001 — 任务失败记录到 job
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.finished_at = time.time()
            logger.warning("Background job %s failed: %s", job.job_id, e)

    def list_jobs(self) -> list[dict[str, Any]]:
        """列出所有任务（按创建时间倒序）。"""
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return [j.to_dict() for j in jobs]

    def status(self, job_id: str) -> dict[str, Any] | None:
        """查询单个任务状态；不存在返回 None。"""
        with self._lock:
            job = self._jobs.get(job_id)
        return job.to_dict() if job else None

    def cancel(self, job_id: str) -> bool:
        """取消任务（仅对 pending/running 生效；无法强杀线程，置 cancelled 标志）。"""
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.status in (JobStatus.PENDING, JobStatus.RUNNING):
            job.cancelled = True
            job.status = JobStatus.CANCELLED
            job.finished_at = time.time()
            return True
        return False

    def shutdown(self) -> None:
        """关闭线程池（进程退出时调用）。"""
        self._pool.shutdown(wait=False, cancel_futures=True)
