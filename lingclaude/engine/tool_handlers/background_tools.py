"""后台任务工具 handler 插片 — 从 coding.py 拆分（灵元：工具是插片）。

BackgroundToolsMixin: run_in_background / list_jobs / job_status / cancel_job。
含 _get_background_manager 惰性初始化（依赖 self._background_manager）。
"""

from __future__ import annotations

from typing import Any

from lingclaude.core.types import ToolResult


class BackgroundToolsMixin:
    """后台任务工具 handler（P0-1，对标 DSH jobs / CC 后台 shell）。"""

    def _get_background_manager(self):
        """P0-1: 惰性初始化后台任务管理器。"""
        manager = getattr(self, "_background_manager", None)
        if manager is None:
            from lingclaude.engine.background import BackgroundTaskManager
            manager = BackgroundTaskManager()
            self._background_manager = manager
        return manager

    def _run_in_background_handler(self, command: str, timeout: int = 300, **_kwargs: Any) -> ToolResult[dict[str, Any]]:
        """P0-1: 后台执行 bash 命令，立即返回 job_id。"""
        if not command or not command.strip():
            return ToolResult.err("command is required", tool_name="run_in_background")
        manager = self._get_background_manager()
        job_id = manager.submit(command, timeout=float(timeout))
        return ToolResult.ok({"job_id": job_id, "status": "pending", "message": f"Background job {job_id} started"})

    def _list_jobs_handler(self, **_kwargs: Any) -> ToolResult[dict[str, Any]]:
        """P0-1: 列出所有后台任务。"""
        manager = self._get_background_manager()
        jobs = manager.list_jobs()
        return ToolResult.ok({"jobs": jobs, "count": len(jobs)})

    def _job_status_handler(self, job_id: str, **_kwargs: Any) -> ToolResult[dict[str, Any]]:
        """P0-1: 查询单个后台任务状态。"""
        manager = self._get_background_manager()
        job = manager.status(job_id)
        if job is None:
            return ToolResult.err(f"Job not found: {job_id}", tool_name="job_status")
        return ToolResult.ok(job)

    def _cancel_job_handler(self, job_id: str, **_kwargs: Any) -> ToolResult[dict[str, Any]]:
        """P0-1: 取消后台任务。"""
        manager = self._get_background_manager()
        cancelled = manager.cancel(job_id)
        if not cancelled:
            return ToolResult.err(
                "Job not found or already finished",
                tool_name="cancel_job",
            )
        return ToolResult.ok({"success": True, "job_id": job_id, "message": "Job cancelled"})
