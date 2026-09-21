"""后台任务管理器 —— 线程池 + 状态字典。

提供：
- submit: 后台执行 bash 命令，立即返回 job_id
- list_jobs / status / cancel: 任务状态查询与取消

任务状态机: pending → running → completed | failed | cancelled

退出安全（挂死修复，2026-09-21）：
worker 线程为非 daemon，解释器 shutdown 阶段 concurrent.futures._python_exit
会 join 每个 worker。若 worker 卡在 communicate() 等一个永不退出的子进程，
join 无限挂起（只能 Ctrl+C）。cancel_futures 只 drain 队列里未开始的
future，救不了已在运行的 worker。修复语义：
- communicate() 改短超时轮询 —— cancelled 置位后 worker 秒级退出
- cancel()/shutdown() 强杀运行中子进程（BackgroundJob.proc 句柄）
- 进程退出时后台任务按 fire-and-forget 语义强杀，不留孤儿 bash
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

# communicate 短超时轮询间隔（cancel/shutdown 置位 → worker 退出的最大延迟）。
# Popen.communicate 超时后重试是文档化安全操作，不会丢失已读输出。
_POLL_INTERVAL = 0.5


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
    # 运行中子进程句柄：cancel/shutdown 强杀的依据（None=未启动或已收尾）。
    # 不入 repr/compare，to_dict 不输出——纯内部控制面。
    proc: subprocess.Popen[str] | None = field(default=None, repr=False, compare=False)

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
        """在线程池中执行命令。

        communicate 用短超时轮询而非一次长阻塞：cancel/shutdown 置位
        cancelled 后，worker 至多一个 _POLL_INTERVAL 内退出——不再把
        解释器 shutdown 阶段的 join 拖成无限挂死。
        RUNNING 状态与 proc 句柄就绪绑定（先 Popen 存句柄再置态），
        外部观察到 running 即可安全强杀。
        """
        deadline = time.monotonic() + timeout
        if job.cancelled:
            return  # pending 阶段已被取消：不启动子进程
        try:
            proc = subprocess.Popen(
                ["/bin/bash", "-c", job.command],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            job.proc = proc  # 先存句柄再置态：观察到 RUNNING ⟹ 可杀
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            stdout = stderr = ""
            while True:
                try:
                    stdout, stderr = proc.communicate(timeout=_POLL_INTERVAL)
                    break  # 自然结束
                except subprocess.TimeoutExpired:
                    pass
                if job.cancelled:
                    # cancel()/shutdown() 已置终态并杀进程；此处兜底收尸
                    self._reap(proc)
                    return
                if time.monotonic() >= deadline:
                    self._reap(proc)
                    job.status = JobStatus.FAILED
                    job.error = f"timeout after {timeout}s"
                    job.finished_at = time.time()
                    return
            job.stdout = stdout
            job.stderr = stderr
            job.exit_code = proc.returncode
            if job.cancelled:
                return  # 与 cancel() 竞态：取消优先，不覆盖终态
            job.status = JobStatus.COMPLETED if proc.returncode == 0 else JobStatus.FAILED
            job.finished_at = time.time()
        except Exception as e:  # noqa: BLE001 — 任务失败记录到 job
            if job.cancelled:
                return  # 退出路径上的异常（如 kill 竞态）不覆盖取消终态
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.finished_at = time.time()
            logger.warning("Background job %s failed: %s", job.job_id, e)

    @staticmethod
    def _reap(proc: subprocess.Popen[str]) -> None:
        """确保子进程死亡并回收（幂等：已退出则 no-op）。"""
        if proc.poll() is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        try:
            proc.communicate(timeout=5.0)
        except Exception:  # noqa: BLE001 — 收尸失败不影响主流程
            pass

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
        """取消任务：杀运行中子进程 + 置 cancelled（worker 轮询发现后立即退出）。

        旧实现只置标志不杀进程——worker 仍卡在 communicate 等子进程，
        线程无法释放；现强杀使 worker 秒级收尾。
        """
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
            return False
        job.cancelled = True
        job.status = JobStatus.CANCELLED
        job.finished_at = time.time()
        proc = job.proc
        if proc is not None:
            self._reap(proc)
        return True

    def shutdown(self) -> None:
        """关闭线程池（进程退出时调用）。

        先杀全部运行中子进程再 shutdown 池：worker 的 communicate 因子进程
        死亡立即返回，线程自然退出——解释器 shutdown 阶段 _python_exit 的
        join 不再挂死（挂死根因的直接消解）。pending 任务被 cancel_futures
        drain 且已置 CANCELLED，不产生孤儿。
        """
        with self._lock:
            jobs = [
                j for j in self._jobs.values()
                if j.status in (JobStatus.PENDING, JobStatus.RUNNING)
            ]
        for job in jobs:
            job.cancelled = True
            job.status = JobStatus.CANCELLED
            if job.finished_at is None:
                job.finished_at = time.time()
            if job.proc is not None:
                self._reap(job.proc)
        self._pool.shutdown(wait=False, cancel_futures=True)
