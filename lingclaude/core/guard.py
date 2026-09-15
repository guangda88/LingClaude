"""ApprovalGuard — 动作审批闸门 (P0-1)。

为动作 (action) 提供统一的审批判定入口: check(action) -> (allowed, reason)。

mode 语义:
  auto:   非 deny 动作全部自动放行 (含写动作)
  ask:    只读动作自动放行; 写动作记录到 .lingclaude/guard_pending.jsonl
          并返回 (False, "pending approval")
  strict: 仅只读动作自动放行, 其余需审批 (返回 requires_approval, 不落盘)

fail-closed 原则:
  - 未知 mode 构造时直接 raise (不静默降级)
  - 空 action / deny 名单一律 (False, ...)
  - "待审批" / "需审批" / "硬拒绝" 都返回 False, 由 reason 区分:
    管线拿到 pending 可稍后重放动作, 拿到 denied 直接终止

ask 模式落盘格式 (JSONL, 每行一条):
  {"ts": "<UTC ISO8601>", "action": "...", "mode": "ask"}

其他模块不应绕过本判定入口。
"""

from __future__ import annotations

from pathlib import Path

# H1 (2026-09-15): 全部语义/名单/落盘单源到 lingclaude.core.permissions。
# 本模块保留兼容符号（测试/外部引用），实现一律委托。
from lingclaude.core.permissions import (
    DENY_ACTIONS,
    PENDING_LOG_PATH,
    READ_ONLY_TOOLS,
    REASON_AUTO,
    REASON_DENIED,
    REASON_EMPTY,
    REASON_NEED_APPROVAL,
    REASON_PENDING,
    REASON_READ_ONLY,
    VALID_MODES,
    PermissionContext,
    load_approval_mode as _load_approval_mode,
    log_pending_action,
)

# H1: 只读名单单源 = permissions.READ_ONLY_TOOLS，本别名仅做兼容。
READ_ONLY_ACTIONS: frozenset[str] = READ_ONLY_TOOLS


class ApprovalGuard:
    """动作审批闸门 — H1 后为 PermissionContext.check_action 的薄包装（保持兼容）。

    语义/落盘/名单全部单源到 lingclaude.core.permissions；本类仅保留
    daemon 写配置场景的既有调用形态（mode + check(action, params)）。

    用法:
        guard = ApprovalGuard(mode="ask")
        allowed, reason = guard.check("read")
        if not allowed and reason == REASON_PENDING:
            ...  # 已记录到 guard_pending.jsonl, 等人工审批后重放
    """

    def __init__(self, mode: str = "auto"):
        normalized = (mode or "").strip().lower()
        if normalized not in VALID_MODES:
            raise ValueError(
                f"ApprovalGuard: 未知 mode={mode!r}, 合法值: {VALID_MODES}"
            )
        self.mode: str = normalized
        self._ctx = PermissionContext(mode=normalized)

    def check(self, action: str, params: dict | None = None) -> tuple[bool, str]:
        """判定单个动作是否放行 — 委托 PermissionContext.check_action（H1 单源）。"""
        return self._ctx.check_action(action, params)

    def __repr__(self) -> str:
        return f"ApprovalGuard(mode={self.mode!r})"


def load_approval_mode(config_path: Path | str | None = None) -> str:
    """从 config.yaml 读取 guard.approval_mode — H1 后转发到 permissions 单源。"""
    return _load_approval_mode(config_path)
