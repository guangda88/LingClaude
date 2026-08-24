from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "read", "grep", "glob", "ls", "find", "head", "tail",
    "cat", "view", "search", "list", "stat", "wc",
})


@dataclass(frozen=True)
class PermissionContext:
    deny_names: frozenset[str] = field(default_factory=frozenset)
    deny_prefixes: tuple[str, ...] = ()
    auto_approve_names: frozenset[str] = field(default_factory=lambda: READ_ONLY_TOOLS)

    @classmethod
    def from_config(
        cls,
        deny_tools: list[str] | None = None,
        deny_prefixes: list[str] | None = None,
        auto_approve: list[str] | None = None,
    ) -> "PermissionContext":
        auto = READ_ONLY_TOOLS
        if auto_approve is not None:
            auto = frozenset(name.lower() for name in auto_approve) | READ_ONLY_TOOLS
        return cls(
            deny_names=frozenset(name.lower() for name in (deny_tools or [])),
            deny_prefixes=tuple(prefix.lower() for prefix in (deny_prefixes or [])),
            auto_approve_names=auto,
        )

    def blocks(self, tool_name: str) -> bool:
        lowered = tool_name.lower()
        return lowered in self.deny_names or any(lowered.startswith(prefix) for prefix in self.deny_prefixes)

    def is_auto_approved(self, tool_name: str) -> bool:
        return tool_name.lower() in self.auto_approve_names and not self.blocks(tool_name)

    def requires_approval(self, tool_name: str) -> bool:
        if self.blocks(tool_name):
            return True
        return not self.is_auto_approved(tool_name)

    def filter_tools(self, tools: tuple[object, ...], name_getter: object = None) -> tuple[object, ...]:
        result = []
        for tool in tools:
            name = name_getter(tool) if name_getter else getattr(tool, "name", str(tool))
            if not self.blocks(name):
                result.append(tool)
        return tuple(result)

    def with_allow(self, tool_name: str) -> "PermissionContext":
        """T0-3: 返回新的 PermissionContext，将 tool_name 加入 auto_approve"""
        new_auto = self.auto_approve_names | {tool_name.lower()}
        return PermissionContext(
            deny_names=self.deny_names,
            deny_prefixes=self.deny_prefixes,
            auto_approve_names=new_auto,
        )

    def with_deny(self, tool_name: str) -> "PermissionContext":
        """T0-3: 返回新的 PermissionContext，将 tool_name 加入 deny"""
        new_deny = self.deny_names | {tool_name.lower()}
        return PermissionContext(
            deny_names=new_deny,
            deny_prefixes=self.deny_prefixes,
            auto_approve_names=self.auto_approve_names - {tool_name.lower()},
        )


class PermissionStore:
    """T0-3: 可变的权限上下文 — 支持审批回路回灌"""

    def __init__(self, initial: PermissionContext | None = None):
        self._ctx = initial or PermissionContext()
        self._history: list[tuple[str, str]] = []  # (tool_name, decision)
        self._explicit_allows: set[str] = set()  # 显式放行过的工具（区别于 read-only 默认放行）

    @property
    def context(self) -> PermissionContext:
        return self._ctx

    def record_approval(self, tool_name: str, decision: str) -> None:
        """记录审批决策并更新权限上下文"""
        self._history.append((tool_name, decision))
        if decision in ("allow", "always_allow", "allow_persist"):
            self._ctx = self._ctx.with_allow(tool_name)
            self._explicit_allows.add(tool_name.lower())
        elif decision == "deny":
            self._ctx = self._ctx.with_deny(tool_name)
            self._explicit_allows.discard(tool_name.lower())

    def explicitly_allowed(self, tool_name: str) -> bool:
        """是否有过显式放行决策（敏感路径门等 fail-closed 机制的逃生门）。"""
        return tool_name.lower() in self._explicit_allows

    def blocks(self, tool_name: str) -> bool:
        return self._ctx.blocks(tool_name)

    def requires_approval(self, tool_name: str) -> bool:
        return self._ctx.requires_approval(tool_name)

    def history(self) -> list[tuple[str, str]]:
        return list(self._history)


# ── T0-3: 会话级审批回路 — webUI /permission 决策 → 工具执行实时生效 ──
# api.py /permission 写入；CodingRuntime.execute_tool / sensitive_path_gate 读取。
# 进程内单例按 session_id 隔离；allow_persist 额外落 JSON 跨会话生效。

_STORES: dict[str, PermissionStore] = {}
_STORES_LOCK = threading.Lock()
_PERSIST_PATH = Path(__file__).resolve().parent.parent / "data" / "approvals.json"
_PERSISTED_TOOLS: set[str] = set()


def _load_persisted() -> None:
    try:
        if _PERSIST_PATH.exists():
            data = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
            _PERSISTED_TOOLS.update(str(t).lower() for t in data.get("always_allow", []))
    except Exception as e:  # noqa: BLE001 — 持久化文件损坏不应阻塞启动
        logger.warning("approvals.json 读取失败: %s", e)


_load_persisted()


def _save_persisted() -> None:
    try:
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PERSIST_PATH.write_text(
            json.dumps({"always_allow": sorted(_PERSISTED_TOOLS)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001 — 持久化失败不影响内存决策
        logger.warning("approvals.json 写入失败: %s", e)


def get_permission_store(session_id: str = "default") -> PermissionStore:
    """获取（或创建）会话级 PermissionStore 单例。

    新 store 创建时自动应用 allow_persist 的跨会话放行。
    """
    with _STORES_LOCK:
        store = _STORES.get(session_id)
        if store is None:
            store = PermissionStore()
            for tool in _PERSISTED_TOOLS:
                store.record_approval(tool, "always_allow")
            _STORES[session_id] = store
        return store


def record_permission_decision(session_id: str, tool_name: str, decision: str) -> None:
    """审批决策入口 — api.py /permission 调用，实时回灌后续工具执行。"""
    if not tool_name:
        return
    store = get_permission_store(session_id or "default")
    store.record_approval(tool_name, decision)
    if decision == "allow_persist":
        with _STORES_LOCK:
            if tool_name.lower() not in _PERSISTED_TOOLS:
                _PERSISTED_TOOLS.add(tool_name.lower())
                _save_persisted()


def reset_permission_stores() -> None:
    """清空会话 store（测试用）。"""
    with _STORES_LOCK:
        _STORES.clear()
