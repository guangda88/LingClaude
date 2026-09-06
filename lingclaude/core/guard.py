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

import json
from datetime import datetime, timezone
from pathlib import Path

# 合法模式
VALID_MODES: tuple[str, ...] = ("auto", "ask", "strict")

# ask 模式下待审批动作的落盘位置 (相对 CWD)
PENDING_LOG_PATH: Path = Path(".lingclaude") / "guard_pending.jsonl"

# config.yaml 中审批模式的读取路径
_CONFIG_MODE_PATH = ("guard", "approval_mode")

# 只读动作 — 三种模式下均自动放行 (与 permissions.READ_ONLY_TOOLS 对齐)
READ_ONLY_ACTIONS: frozenset[str] = frozenset({
    "read", "grep", "glob", "ls", "find", "head", "tail",
    "cat", "view", "search", "list", "stat", "wc",
})

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


class ApprovalGuard:
    """动作审批闸门。

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

    def check(self, action: str, params: dict | None = None) -> tuple[bool, str]:
        """判定单个动作是否放行。

        返回 (allowed, reason):
          (True,  REASON_READ_ONLY)      只读动作, 任意模式放行
          (True,  REASON_AUTO)           auto 模式放行
          (False, REASON_PENDING)        ask 模式写动作: 已落盘待审批(params 摘要一并记录)
          (False, REASON_NEED_APPROVAL)  strict 模式写动作: 需审批, 不落盘
          (False, REASON_DENIED)         命中硬拒绝名单
          (False, REASON_EMPTY)          空 action, fail-closed
        """
        if not action or not action.strip():
            return (False, REASON_EMPTY)

        lowered = action.strip().lower()

        if lowered in DENY_ACTIONS:
            return (False, REASON_DENIED)

        if lowered in READ_ONLY_ACTIONS:
            return (True, REASON_READ_ONLY)

        if self.mode == "auto":
            return (True, REASON_AUTO)

        # strict: 写动作需审批 (不落盘, 由管线自行处理)
        if self.mode == "strict":
            return (False, REASON_NEED_APPROVAL)

        # ask: 记录到待审批日志, 返回 pending approval
        self._log_pending(lowered, params)
        return (False, REASON_PENDING)

    def _log_pending(self, action: str, params: dict | None = None) -> None:
        """把待审批动作追加到 .lingclaude/guard_pending.jsonl。

        落盘失败不影响 check() 的判定结果 (只对日志 fail-open)。
        """
        try:
            PENDING_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "action": action,
                "mode": self.mode,
            }
            # 2026-09-05 事故复盘:pending 只记 action 名导致被拦参数不可考 —
            # params 摘要必须随记录落盘,审批人才有裁决依据。
            if params:
                record["params"] = params
            with PENDING_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def __repr__(self) -> str:
        return f"ApprovalGuard(mode={self.mode!r})"


def load_approval_mode(config_path: Path | str | None = None) -> str:
    """从 config.yaml 读取 guard.approval_mode,缺省 auto。

    读取失败一律回退 auto(fail-open 仅限配置缺失场景;一旦读到非法值,
    ApprovalGuard 构造会 fail-closed 抛错)。
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
