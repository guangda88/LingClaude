"""工具授权四档策略引擎（灵元 R2 P0：守卫升级为策略对象）。

外部 Agent 联审结论（cc/codex/crush/opencode 四家独立提出）：
  裁决点钉在可观测、可记账的单次工具调用上，安全从软约束变硬约束。

架构：
  check_tool_call() 在 tool_call_start 事件时调用（PreToolUse hook），
  查 tool_auth_policy.yaml 档位矩阵，返回 Decision（tier + policy_id），
  裁决链入台账（审计从"记动作"升级为"记裁决链"）。

四档语义：
  auto        — 执行前无需用户确认（read-only 类，风险极低）
  pre_approve — 执行前需用户显式批准（可逆写，高风险写）
  ask         — 执行前询问用户意图（外部发送，数据出境）
  block       — 拒绝执行，交还用户决策（破坏性操作）
"""
from __future__ import annotations

import enum
import json as _json
import logging
import os as _os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lingclaude.core import policy_loader

logger = logging.getLogger(__name__)

# 策略名（与 PolicyLoader.get() 对应）
_POLICY_NAME = "tool_auth_policy"

# 缓存：策略数据（mtime watch 由 PolicyLoader.get() 负责）
_policy_cache: dict[str, Any] | None = None
_policy_cache_mtime: float = 0.0

# 台账路径（同 lc_mcp_guard/server.py 约定，审计结果落 arch_ledger）
from pathlib import Path
import os as _os

_LEDGER_DIR = Path(_os.environ.get("LC_ROOT", "/home/ai/lingclaude")) / "data" / "arch_ledger"


class Tier(enum.Enum):
    AUTO = "auto"
    PRE_APPROVE = "pre_approve"
    ASK = "ask"
    BLOCK = "block"


# 降级兜底（任何异常 → 静默回退到 ask，never block 误杀）
_DEFAULT_TIER = Tier.ASK


@dataclass
class Decision:
    tier: Tier
    tool_name: str
    policy_id: str = ""  # 台账用的策略版本锚（mtime + 档位哈希）
    reason: str = ""     # 可选，人工可读理由
    audit_written: bool = False


def _get_policy() -> dict[str, Any]:
    """取策略数据：PolicyLoader.get() 自动处理缓存+mtime watch（热更生效）。"""
    global _policy_cache
    try:
        raw = policy_loader.get(_POLICY_NAME)
        if raw:
            _policy_cache = raw
    except Exception:  # noqa: BLE001
        pass
    return _policy_cache or {}


def _build_policy_id(policy_data: dict[str, Any]) -> str:
    """为策略数据生成稳定 policy_id（台账版本锚）。"""
    try:
        compact = _json.dumps(policy_data, sort_keys=True, default=str)
        return str(abs(hash(compact)))[:12]
    except Exception:  # noqa: BLE001
        return str(int(time.time()))[:12]


def _match_tool(tool_patterns: list[str], tool_name: str) -> bool:
    """用 re.fullmatch 判断工具名是否命中 patterns 之一。"""
    for pat in tool_patterns:
        try:
            if re.fullmatch(pat, tool_name):
                return True
        except re.error:
            # 非正则字面量，精确匹配
            if pat == tool_name:
                return True
    return False


def _write_audit(tier: Tier, tool_name: str, tool_args: dict[str, Any], policy_id: str) -> None:
    """裁决链入台账（arch_ledger），失败静默，不阻塞工具执行。"""
    try:
        _LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "type": "tool_auth_decision",
            "id": str(uuid.uuid4())[:12],
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tier": tier.value,
            "tool_name": tool_name,
            "policy_id": policy_id,
            "args_keys": list(tool_args.keys()) if isinstance(tool_args, dict) else [],
        }
        fname = _LEDGER_DIR / f"tool_auth_{time.strftime('%Y%m%d')}.jsonl"
        with open(fname, "a", encoding="utf-8") as f:
            f.write(_json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass  # 台账写入失败绝不反噬工具执行


def check_tool_call(tool_name: str, tool_args: dict[str, Any] | None = None) -> Decision:
    """PreToolUse hook 入口：查档位矩阵，返回裁决结果。

    调用约定（repl_turn.py tool_call_start 事件处）：
      decision = check_tool_call(event.get("name", ""), event.get("arguments", {}))
      if decision.tier == Tier.BLOCK:
          return  # 静默跳过（工具被拒绝，model 端收到空结果）
      if decision.tier == Tier.PRE_APPROVE:
          _request_pre_approve(...)  # 阻塞等待用户确认
      if decision.tier == Tier.ASK:
          _request_user_confirm(...)  # 询问后决策

    参数：
      tool_name: 工具名（来自 tool_call_start.name）
      tool_args: 工具参数字典（来自 tool_call_start.arguments，JSON 解析后）
    返回：
      Decision(tier, tool_name, policy_id, reason, audit_written)
    """
    if tool_args is None:
        tool_args = {}

    try:
        policy = _get_policy()
        tiers = policy.get("tiers", {})
        policy_id = _build_policy_id(policy)

        # 按档位优先级查（A → P → Q → X）
        tier_order = [("auto", Tier.AUTO), ("pre_approve", Tier.PRE_APPROVE),
                      ("ask", Tier.ASK), ("block", Tier.BLOCK)]

        for tier_key, tier_enum in tier_order:
            tier_config = tiers.get(tier_key, {})
            tools_list = tier_config.get("tools", [])
            if _match_tool(tools_list, tool_name):
                # 写台账（audit_enabled 开关）
                audit_enabled = policy.get("audit_enabled", True)
                written = False
                if audit_enabled:
                    _write_audit(tier_enum, tool_name, tool_args, policy_id)
                    written = True
                return Decision(
                    tier=tier_enum,
                    tool_name=tool_name,
                    policy_id=policy_id,
                    reason=tier_config.get("description", ""),
                    audit_written=written,
                )

        # 未命中任何档位 → 默认识别为 ask（安全默认）
        policy_id = _build_policy_id(policy)
        _write_audit(_DEFAULT_TIER, tool_name, tool_args, policy_id)
        return Decision(
            tier=_DEFAULT_TIER,
            tool_name=tool_name,
            policy_id=policy_id,
            reason="未命中任何档位，安全默认降级到 ask",
            audit_written=True,
        )

    except Exception as e:  # noqa: BLE001
        # 任何解析/匹配异常 → 静默回退 ask，绝不 block 误杀
        logger.debug(f"tool_auth hook 回退 ask: {e}")
        return Decision(
            tier=_DEFAULT_TIER,
            tool_name=tool_name,
            policy_id="error",
            reason=f"hook 异常: {e}",
            audit_written=False,
        )


# ── 交互式确认（repl_turn.py 调用，以下是 stub，实际实现在 repl_turn.py）───

def request_pre_approve(tool_name: str, tool_args: dict[str, Any]) -> bool:
    """请求用户预批准。返回 True=批准，False=拒绝。

    实现约定：repl_turn.py 重载此函数，调用 TUI 确认框。
    stub 行为：直接拒绝（安全默认）。
    """
    return False


def request_user_confirm(tool_name: str, tool_args: dict[str, Any], question: str) -> str:
    """询问用户意图。返回 "yes"/"no"/"never"/"always"。

    实现约定：repl_turn.py 重载此函数，调用 TUI 确认框。
    stub 行为：拒绝。
    """
    return "no"
