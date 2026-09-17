"""git 工具组插件（SeamType.TOOL 槽位）。

灵元纪律：
- 变化（工具实现）= 插片，不焊进主干 —— 本文件自包含 manifest + 实现。
- 不复制主干逻辑：execute 委托 engine/git 模块函数（与 tool_handlers/git_tools.py
  共用同一实现，只做「按名分派」，不重复 Git 参数化/白名单/防注入逻辑）。
- 插件提供 git_status/git_diff/git_log/git_blame/git_push/git_push_preflight/
  git_probe_remotes 七种能力，按 name 分派到 git.py / remote_probe 对应函数。
- 铁律 3/J4：每次工具调用全 record 化（git_tool_log type），失败路径也入账
  ——与 work_claim._record 同一家法：本体与事件流分离，可 query 可回放。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from lingclaude.core.state_store import StateStore
from lingclaude.engine.git import (
    git_blame,
    git_diff,
    git_log,
    git_push,
    git_push_preflight,
    git_status,
)
from lingclaude.plugins.tools.git.remote_probe import probe_all_remotes

GIT_TOOL_LOG = "git_tool_log"  # record type：调用事件流（与工具返回值分离）


class GitPlugin:
    """ToolPlugin 协议实现：name + execute（SeamType.TOOL 槽位）。"""

    name = "git_plugin"

    def __init__(self, store: StateStore | None = None) -> None:
        self._store = store  # None → 首次记账时惰性取默认 StateStore（J4 不缺席）

    # ── J4：调用事件全入账 ────────────────────────────────────────────
    def _log(self, name: str, kwargs: dict[str, Any], ok: bool,
             error: str | None = None, duration_ms: int | None = None) -> None:
        """每次工具调用记一条事件（成功/失败都记）。

        StateStore.save 内部自吞写盘异常（logger.warning），与 work_claim
        同规：记账不抛不炸，但不缺席。
        """
        if self._store is None:
            self._store = StateStore()
        log_id = f"{name}:{int(time.time() * 1000)}:{uuid.uuid4().hex[:6]}"
        self._store.save(GIT_TOOL_LOG, log_id, {
            "tool": name,
            "args": {k: v for k, v in kwargs.items()
                     if isinstance(v, (str, int, float, bool))},
            "ok": ok,
            "error": (error or "")[:200],
            "duration_ms": duration_ms,
            "at": time.time(),
        })

    def execute(self, name: str = "git_status", **kwargs: Any) -> Any:
        """按工具名分派到 engine.git / remote_probe 对应函数。

        - git_status → git_status(path)
        - git_diff → git_diff(path, target, staged, stat)
        - git_log → git_log(path, count, follow)
        - git_blame → git_blame(file_path, cwd, start_line, end_line)
        - git_push → git_push(path, remote, branch, force, timeout)
        - git_push_preflight → git_push_preflight(path)
        - git_probe_remotes → probe_all_remotes(path, root)  # DNS→TCP→协议分层
        """
        started = time.monotonic()
        try:
            result = self._dispatch(name, **kwargs)
        except Exception as e:  # 失败也必须入账（J4），再按原样抛给调用方
            self._log(name, kwargs, ok=False, error=f"{type(e).__name__}: {e}",
                      duration_ms=int((time.monotonic() - started) * 1000))
            raise
        error = None
        if getattr(result, "is_error", False):
            error = getattr(result, "error", "unknown git error")
        elif isinstance(result, dict) and "error" in result:
            error = str(result["error"])
        self._log(name, kwargs, ok=error is None, error=error,
                  duration_ms=int((time.monotonic() - started) * 1000))

        if getattr(result, "is_error", False):
            return {"error": error}
        if isinstance(result, dict):
            return result
        return result.data

    def _dispatch(self, name: str, **kwargs: Any) -> Any:
        if name == "git_diff":
            return git_diff(
                kwargs.pop("path", "."),
                target=kwargs.pop("target", ""),
                staged=kwargs.pop("staged", False),
                stat=kwargs.pop("stat", False),
            )
        if name == "git_log":
            return git_log(
                kwargs.pop("path", "."),
                count=kwargs.pop("count", 10),
                follow=kwargs.pop("follow", None),
            )
        if name == "git_blame":
            return git_blame(
                kwargs.pop("file_path", ""),
                cwd=kwargs.pop("cwd", "."),
                start_line=kwargs.pop("start_line", None),
                end_line=kwargs.pop("end_line", None),
            )
        if name == "git_push":
            return git_push(
                path=kwargs.pop("path", "."),
                remote=kwargs.pop("remote", "origin"),
                branch=kwargs.pop("branch", "master"),
                force=kwargs.pop("force", False),
                timeout=kwargs.pop("timeout", 120),
            )
        if name == "git_push_preflight":
            return git_push_preflight(path=kwargs.pop("path", "."))
        if name == "git_probe_remotes":
            return probe_all_remotes(
                path=kwargs.pop("path", "."),
                store=self._store,
                root=kwargs.pop("root", None),
            )
        # 默认 git_status
        return git_status(kwargs.pop("path", "."))
