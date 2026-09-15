from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "read", "grep", "glob", "ls", "find", "head", "tail",
    "cat", "view", "search", "list", "stat", "wc",
})

# R5 阶段2:resume 时需二次确认的副作用工具（与 READ_ONLY_TOOLS 互斥，显式列出防误降级）。
SIDE_EFFECT_TOOLS: frozenset[str] = frozenset({
    "write", "edit", "bash", "rm", "mv", "cp",
    "curl", "wget", "ssh", "git", "python", "apply_patch",
    "request_user_input", "plan_mode",
})

# ── H1 (2026-09-15): 动作闸门常量单源（guard 引用别名；只读真源=READ_ONLY_TOOLS）。
VALID_MODES: tuple[str, ...] = ("auto", "ask", "strict")
# ask 模式下待审批动作的落盘位置 (相对 CWD)
PENDING_LOG_PATH: Path = Path(".lingclaude") / "guard_pending.jsonl"

# 硬拒绝动作 — 任何模式都不放行
DENY_ACTIONS: frozenset[str] = frozenset({
    "rm_rf_root", "sudo", "format_disk", "shutdown",
})

# reason 常量 — 管线按字符串匹配分流, 不要散落裸字符串
REASON_DENIED = "denied_by_rule"
REASON_EMPTY = "empty_action"
REASON_READ_ONLY = "read_only_auto"
REASON_AUTO = "auto_mode_pass"
REASON_NEED_APPROVAL = "requires_approval"
REASON_PENDING = "pending approval"

@dataclass(frozen=True)
class PermissionContext:
    deny_names: frozenset[str] = field(default_factory=frozenset)
    deny_prefixes: tuple[str, ...] = ()
    auto_approve_names: frozenset[str] = field(default_factory=lambda: READ_ONLY_TOOLS)
    # T1-2: permission mode — auto(非deny全放行含写) / ask(写需审批,默认) / strict(非auto_approve需审批)
    mode: str = "ask"

    @classmethod
    def from_config(
        cls,
        deny_tools: list[str] | None = None,
        deny_prefixes: list[str] | None = None,
        auto_approve: list[str] | None = None,
        mode: str = "ask",
    ) -> "PermissionContext":
        auto = READ_ONLY_TOOLS
        if mode == "auto":
            # auto 模式：除 deny 外全部放行 — auto_approve 集合无意义，放空交由 requires_approval 处理
            auto = frozenset()
        elif auto_approve is not None:
            auto = frozenset(name.lower() for name in auto_approve) | READ_ONLY_TOOLS
        return cls(
            deny_names=frozenset(name.lower() for name in (deny_tools or [])),
            deny_prefixes=tuple(prefix.lower() for prefix in (deny_prefixes or [])),
            auto_approve_names=auto,
            mode=mode,
        )

    def blocks(self, tool_name: str) -> bool:
        lowered = tool_name.lower()
        return lowered in self.deny_names or any(lowered.startswith(prefix) for prefix in self.deny_prefixes)

    def blocks_with_rule(self, tool_name: str) -> tuple[bool, str]:
        """5a：返回 (是否拦截, rule_id)。用于让调用方拿到结构化 rule_id 而非 bool。

        rule_id 是熔断 key 喂给 R5b（_ToolLoopDetector 按 rule_id 熔断）。
        """
        lowered = tool_name.lower()
        if lowered in self.deny_names:
            return True, "config.deny_tools.exact"
        for prefix in self.deny_prefixes:
            if lowered.startswith(prefix):
                return True, "config.deny_prefixes.prefix"
        return False, "no_match"

    def is_auto_approved(self, tool_name: str) -> bool:
        lowered = tool_name.lower()
        if self.blocks(lowered):
            return False
        if self.mode == "auto":
            # auto 模式：非 deny 工具全部放行
            return True
        return lowered in self.auto_approve_names

    def requires_approval(self, tool_name: str) -> bool:
        if self.blocks(tool_name):
            return True
        return not self.is_auto_approved(tool_name)

    # ── H1 (2026-09-15): 动作级统一闸门 — ApprovalGuard.check 的语义收敛入口。
    # 语义与 guard.check 完全一致：空/deny 硬拒、只读放行、auto 全放、
    # strict 需审批（不落盘）、ask 落盘 pending 后返回 (False, REASON_PENDING)。
    def check_action(self, action: str, params: dict | None = None) -> tuple[bool, str]:
        """判定单个动作是否放行，语义与 ApprovalGuard.check 对齐（H1 单源）。

        返回 (allowed, reason):
          (True,  REASON_READ_ONLY)      只读动作, 任意模式放行
          (True,  REASON_AUTO)           auto 模式放行
          (False, REASON_PENDING)        ask 模式写动作: 已落盘待审批(params 一并记录)
          (False, REASON_NEED_APPROVAL)  strict 模式写动作: 需审批, 不落盘
          (False, REASON_DENIED)         命中硬拒绝名单
          (False, REASON_EMPTY)          空 action, fail-closed
        """
        if not action or not action.strip():
            return (False, REASON_EMPTY)

        lowered = action.strip().lower()

        if lowered in DENY_ACTIONS:
            return (False, REASON_DENIED)

        if lowered in READ_ONLY_TOOLS:
            return (True, REASON_READ_ONLY)

        if self.mode == "auto":
            return (True, REASON_AUTO)

        if self.mode == "strict":
            return (False, REASON_NEED_APPROVAL)

        # ask: 记录到待审批日志, 返回 pending approval
        log_pending_action(lowered, params, mode=self.mode)
        return (False, REASON_PENDING)

    def filter_tools(self, tools: tuple[object, ...], name_getter: object = None) -> tuple[object, ...]:
        result = []
        for tool in tools:
            name = name_getter(tool) if name_getter else getattr(tool, "name", str(tool))
            if not self.blocks(name):
                result.append(tool)
        return tuple(result)

    def with_allow(self, tool_name: str) -> "PermissionContext":
        """T0-3: 返回新的 PermissionContext，将 tool_name 加入 auto_approve。

        同时从 deny_names 移除 — 显式审批决策覆盖静态/历史拒绝（否则 deny 后永远无法翻案）。
        """
        lowered = tool_name.lower()
        return PermissionContext(
            deny_names=self.deny_names - {lowered},
            deny_prefixes=self.deny_prefixes,
            auto_approve_names=self.auto_approve_names | {lowered},
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

    def context_with_allow(self, tool_name: str) -> None:
        """仅放开权限上下文（不记入显式会话审批）— 持久化放行预载专用。

        工具级持久放行不应绕过 sensitive_path_gate 的 fail-closed：
        敏感路径要求本会话级显式审批，否则 allow_persist 一次 = 凭据路径永久失守。
        """
        self._ctx = self._ctx.with_allow(tool_name)


# ── T0-3: 会话级审批回路 — webUI /permission 决策 → 工具执行实时生效 ──
# api.py /permission 写入；CodingRuntime.execute_tool / sensitive_path_gate 读取。
# 进程内单例按 session_id 隔离；allow_persist 额外落 JSON 跨会话生效。

_STORES: dict[str, PermissionStore] = {}
_STORES_LOCK = threading.Lock()
_PERSIST_PATH = Path(__file__).resolve().parent.parent / "data" / "approvals.json"
_PERSISTED_TOOLS: set[str] = set()
# T1-2: 全局 permission mode — auto/ask/strict（webUI 可读可设，落同一 JSON）
_GLOBAL_MODE: str = "ask"


def get_permission_mode() -> str:
    """T1-2 深化: 读取当前全局 permission mode。"""
    return _GLOBAL_MODE


def set_permission_mode(mode: str) -> bool:
    """T1-2 深化: 设置全局 permission mode（auto/ask/strict），持久化落盘。

    Returns:
        True 设置成功；False 参数非法（保持原值）。
    """
    global _GLOBAL_MODE
    normalized = mode.lower()
    if normalized not in ("auto", "ask", "strict"):
        logger.warning("无效 permission mode: %s（允许: auto/ask/strict）", mode)
        return False
    with _STORES_LOCK:
        _GLOBAL_MODE = normalized
        _save_persisted()
    logger.info("permission mode 已切换: %s", normalized)
    return True


def _load_persisted() -> None:
    global _GLOBAL_MODE  # 2026-09-15 修复: 缺此声明，赋值成局部变量，mode 永远停在默认 ask
    try:
        if _PERSIST_PATH.exists():
            data = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
            _PERSISTED_TOOLS.update(str(t).lower() for t in data.get("always_allow", []))
            if data.get("mode") in ("auto", "ask", "strict"):
                _GLOBAL_MODE = data["mode"]
    except Exception as e:  # noqa: BLE001 — 持久化文件损坏不应阻塞启动
        logger.warning("approvals.json 读取失败: %s", e)
    if _GLOBAL_MODE == "ask":  # 2026-09-15 修复: 兜底读 config.yaml（启动链此前从不读）
        import os as _os
        from lingclaude.core.guard import load_approval_mode
        _GLOBAL_MODE = load_approval_mode(Path(_os.environ.get("LINGCLAUDE_CONFIG") or "config.yaml"))


_load_persisted()


def _save_persisted() -> None:
    try:
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PERSIST_PATH.write_text(
            json.dumps({
                "mode": _GLOBAL_MODE,
                "always_allow": sorted(_PERSISTED_TOOLS),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001 — 持久化失败不影响内存决策
        logger.warning("approvals.json 写入失败: %s", e)


def log_pending_action(
    action: str,
    params: dict | None = None,
    mode: str | None = None,
    state: str = "pending",
) -> None:
    """把审批动作追加到 .lingclaude/guard_pending.jsonl（H1 单源）。

    原 guard._log_pending 迁移至此 — 灰区 escalate 与 PermissionContext.check_action
    共用同一落盘点。落盘失败不影响判定结果（只对日志 fail-open）。

    mode 参数：记录实际判定用的模式（默认取全局 _GLOBAL_MODE；
    daemon 等独立判定场景应显式传 ctx.mode，保证落盘可考）。

    state 参数（P0-N5, 2026-09-15）：
      - "pending"            灰区 escalate 的待审批记录（默认）
      - "approved_by_daemon" daemon 写配置成功后补录的已执行留痕
    与灰区形成「申请→审批→执行→留痕」闭环：同一条 guard_pending.jsonl 里，
    state=pending 是入口（申请），state=approved_by_daemon 是出口（执行），
    审批人可依据 params 字段核对两端的动作域一致性。
    """
    try:
        PENDING_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "mode": mode or _GLOBAL_MODE,
            "state": state,
        }
        # 2026-09-05 事故复盘: pending 只记 action 名导致被拦参数不可考 —
        # params 摘要必须随记录落盘, 审批人才有裁决依据。
        if params:
            record["params"] = params
        with PENDING_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


_CONFIG_MODE_PATH = ("guard", "approval_mode")


def load_approval_mode(config_path: Path | str | None = None) -> str:
    """从 config.yaml 读取 guard.approval_mode, 缺省 auto。

    读取失败一律回退 auto (fail-open 仅限配置缺失场景; 一旦读到非法值,
    PermissionContext/ApprovalGuard 构造会 fail-closed 抛错)。
    """
    if config_path is None:
        return "auto"
    try:
        import yaml

        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        mode = raw
        for key in _CONFIG_MODE_PATH:
            mode = (mode or {}).get(key) if isinstance(mode, dict) else None
        normalized = str(mode or "auto").strip().lower()
        return normalized if normalized in VALID_MODES else "auto"
    except Exception:  # noqa: BLE001 — 配置缺失/损坏时回退默认
        return "auto"


def get_permission_store(session_id: str = "default") -> PermissionStore:
    """获取（或创建）会话级 PermissionStore 单例。

    新 store 创建时自动应用 allow_persist 的跨会话放行（仅作用于 blocks /
    is_auto_approved — 不标记 explicitly_allowed，敏感路径门要求本会话显式审批）。
    """
    with _STORES_LOCK:
        store = _STORES.get(session_id)
        if store is None:
            store = PermissionStore()
            for tool in _PERSISTED_TOOLS:
                store.context_with_allow(tool)
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
    """清空会话 store 与持久化内存态（测试隔离用）。

    同时清 _PERSISTED_TOOLS，防止真实 approvals.json 的 always_allow
    污染测试会话（工具级持久放行不应影响测试断言）。
    """
    with _STORES_LOCK:
        _STORES.clear()
        _PERSISTED_TOOLS.clear()
