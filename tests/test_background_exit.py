"""BackgroundTaskManager 退出安全测试 —— 锚定「退出挂死」修复。

根因（2026-09-21 实证）：worker 线程非 daemon，卡在 communicate() 等一个
永不退出的子进程时，解释器 shutdown 阶段 concurrent.futures._python_exit
对每个 worker join 无限挂起（只能 Ctrl+C）——cancel_futures 只 drain
未开始的 future，救不了已运行的 worker。

修复语义：cancel/shutdown 强杀运行中子进程（job.proc 句柄），worker 的
communicate 改短超时轮询，置位后秒级退出。
"""
from __future__ import annotations

import threading
import time

from lingclaude.engine.background import BackgroundTaskManager, JobStatus


def _wait_running(mgr: BackgroundTaskManager, job_id: str, timeout: float = 5.0) -> bool:
    """轮询等待 job 进入 RUNNING（worker 真正起了子进程）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = mgr.status(job_id)
        if st and st["status"] == JobStatus.RUNNING.value:
            return True
        time.sleep(0.02)
    return False


def _wait_terminal(mgr: BackgroundTaskManager, job_id: str, timeout: float = 8.0):
    """轮询等待 job 到达终态，返回 status dict（超时返回 None）。"""
    terminal = {JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = mgr.status(job_id)
        if st and st["status"] in terminal:
            return st
        time.sleep(0.02)
    return None


class TestExitSafety:
    def test_shutdown_kills_running_child(self):
        """shutdown 必须杀掉运行中的子进程——不等 sleep 60，不留孤儿。"""
        mgr = BackgroundTaskManager()
        job_id = mgr.submit("sleep 60")
        assert _wait_running(mgr, job_id)
        t0 = time.monotonic()
        mgr.shutdown()
        assert time.monotonic() - t0 < 3.0, "shutdown 不应阻塞等待长任务"
        st = mgr.status(job_id)
        assert st["status"] == JobStatus.CANCELLED.value
        proc = mgr._jobs[job_id].proc
        assert proc is not None and proc.poll() is not None, "子进程应已被杀死"
        # worker 收尾不得覆盖取消终态
        time.sleep(1.0)
        assert mgr.status(job_id)["status"] == JobStatus.CANCELLED.value

    def test_worker_joinable_after_shutdown(self):
        """挂死根因直接锚定：shutdown 后非 daemon worker 线程可 join。

        修复前此用例等价于用户实测的 Ctrl+C 现场：worker 卡 communicate，
        join 永不返回。修复后 worker 因子进程被杀而秒级退出。
        """
        mgr = BackgroundTaskManager()
        job_id = mgr.submit("sleep 60")
        assert _wait_running(mgr, job_id)
        mgr.shutdown()
        workers = [t for t in threading.enumerate() if t.name.startswith("bg")]
        assert workers, "worker 线程应存在"
        for t in workers:
            t.join(timeout=5.0)
            assert not t.is_alive(), f"worker {t.name} shutdown 后未退出（挂死复现）"

    def test_cancel_kills_running_child(self):
        """cancel 强杀子进程且终态不被 worker 覆盖。"""
        mgr = BackgroundTaskManager()
        job_id = mgr.submit("sleep 60")
        assert _wait_running(mgr, job_id)
        assert mgr.cancel(job_id) is True
        st = mgr.status(job_id)
        assert st["status"] == JobStatus.CANCELLED.value
        proc = mgr._jobs[job_id].proc
        assert proc is not None and proc.poll() is not None, "子进程应已被杀死"
        time.sleep(1.0)  # 跨一个轮询周期，确认 worker 收尾不改终态
        assert mgr.status(job_id)["status"] == JobStatus.CANCELLED.value
        mgr.shutdown()

    def test_cancel_before_start_pending(self):
        """pending 阶段 cancel：worker 后续轮询发现置位即收尾，不留孤儿。"""
        mgr = BackgroundTaskManager()
        job_id = mgr.submit("sleep 60")
        assert mgr.cancel(job_id) is True
        assert mgr.status(job_id)["status"] == JobStatus.CANCELLED.value
        mgr.shutdown()
        for t in [t for t in threading.enumerate() if t.name.startswith("bg")]:
            t.join(timeout=5.0)
            assert not t.is_alive(), f"worker {t.name} 未退出"

    def test_natural_completion_unchanged(self):
        """回归：轮询改造不破坏正常路径（stdout/exit_code/状态）。"""
        mgr = BackgroundTaskManager()
        job_id = mgr.submit("echo hello")
        st = _wait_terminal(mgr, job_id)
        assert st is not None
        assert st["status"] == JobStatus.COMPLETED.value
        assert st.get("stdout_preview", "").strip() == "hello"
        mgr.shutdown()

    def test_timeout_path_still_fails(self):
        """超时路径回归：置 FAILED + 杀子进程（不留超时孤儿）。"""
        mgr = BackgroundTaskManager()
        job_id = mgr.submit("sleep 30", timeout=1.0)
        st = _wait_terminal(mgr, job_id, timeout=10.0)
        assert st is not None
        assert st["status"] == JobStatus.FAILED.value
        assert "timeout" in (st.get("error") or "")
        proc = mgr._jobs[job_id].proc
        assert proc is not None and proc.poll() is not None, "超时子进程应已被杀"
        mgr.shutdown()

    def test_to_dict_still_hides_proc(self):
        """proc 句柄不泄漏到对外投影（compare=False + to_dict 白名单）。"""
        mgr = BackgroundTaskManager()
        job_id = mgr.submit("sleep 60")
        assert _wait_running(mgr, job_id)
        job = mgr._jobs[job_id]
        assert job.proc is not None
        assert "proc" not in job.to_dict()
        mgr.shutdown()
