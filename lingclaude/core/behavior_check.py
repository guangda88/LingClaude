"""T0 行为校验器 (BehaviorCheck) — 零推理成本

在进入 L5/R5/Z3 管道前, 先做纯模式匹配的轻量行为检查。
借鉴 AtomCode VerifyCadenceHook 的设计: 只枚举"什么不算", 不枚举"什么算"。

校验项:
  1. 编辑后验证: edit/write 后是否有 build/check/test (同 VerifyCadenceHook)
  2. 工具重复检测: 同一工具 + 同一参数连续 N 次
  3. 连续失败替代: bash 连续失败后是否换过命令
  4. 输出完整性: 空回答 / 极短回答 / 明显不完整信号
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

# G1 (2026-09-15): policy_loader.load 提到模块顶层 —— E13 策略外置引入的函数内
# import。policy_loader 是纯 core 模块（顶层仅标准库），无循环，可安全顶层导入。
from lingclaude.core.policy_loader import load

logger = logging.getLogger(__name__)


@dataclass
class BehaviorCheckResult:
    """行为校验结果"""
    passed: bool = True
    nudges: list[str] = field(default_factory=list)
    should_block: bool = False


# 只读/导航命令 — 不算验证 (黑名单优先)
READONLY_COMMANDS = frozenset({
    "ls", "cat", "pwd", "echo", "cd", "which", "whoami",
    "head", "tail", "find", "grep", "rg", "fd", "tree",
    "stat", "file", "printf", "clear", "date", "sleep", "type",
    "wc", "sort", "uniq", "awk", "sed", "cut", "diff",
    "less", "more", "tee", "basename", "dirname",
})

# 编辑类工具（E13: 外置到 policies/behavior_policy.yaml，此处为内置默认兜底）
EDIT_TOOLS = frozenset({"edit_file", "write_file", "edit", "write"})

# 连续重复工具阈值（E13: 外置，读失败回退此默认）
TOOL_REPEAT_LIMIT = 3

# 明显不完整信号 (黑名单: 枚举"什么不算完整")（E13: 外置，读失败回退此默认）
INCOMPLETE_SIGNALS = [
    r"^我不知道",
    r"^我无法",
    r"^我不确定",
    r"^抱歉，我",
    r"^根据以上[^，]*$",
    r"^如上所述[^，]*$",
    r"^$",
]

# 连续失败阈值（E13: 外置，读失败回退此默认）
CONSECUTIVE_FAIL_LIMIT = 2


def _load_behavior_policy() -> dict[str, Any]:
    """E13: 从 policies/behavior_policy.yaml 加载行为检查策略。

    读失败/缺失 → {}（调用方回退内置默认，graceful degrade）。
    通过 PolicyLoader 走 mtime watch 热更，改 YAML 不重启进程。
    """

    return load("behavior_policy")


def _get_tool_name(args: dict[str, Any] | None) -> str:
    """从 tool call 参数中提取工具名"""
    if args is None:
        return ""
    return args.get("tool_name") or args.get("name") or args.get("command", "").split()[0] if args.get("command") else ""


def _edit_tools_from_policy() -> frozenset[str]:
    """E13: 编辑类工具集合 — YAML 优先，读失败回退内置默认。"""
    policy = _load_behavior_policy()
    tools = policy.get("edit_tools")
    if isinstance(tools, list) and tools:
        return frozenset(tools)
    return EDIT_TOOLS


def _repeat_limit_from_policy() -> int:
    """E13: 连续重复阈值 — YAML 优先，读失败回退内置默认。"""
    policy = _load_behavior_policy()
    val = policy.get("tool_repeat_limit")
    if isinstance(val, int) and val > 0:
        return val
    return TOOL_REPEAT_LIMIT


def _fail_limit_from_policy() -> int:
    """E13: 连续失败阈值 — YAML 优先，读失败回退内置默认。"""
    policy = _load_behavior_policy()
    val = policy.get("consecutive_fail_limit")
    if isinstance(val, int) and val > 0:
        return val
    return CONSECUTIVE_FAIL_LIMIT


def _incomplete_signals_from_policy() -> list[str]:
    """E13: 不完整信号模式 — YAML 优先，读失败回退内置默认。"""
    policy = _load_behavior_policy()
    signals = policy.get("incomplete_signals")
    if isinstance(signals, list) and signals:
        return [str(s) for s in signals]
    return INCOMPLETE_SIGNALS


def check_edit_verify(tool_history: list[dict[str, Any]]) -> list[str]:
    """检查编辑后是否验证了 (模式匹配, 零推理)"""
    nudges = []
    last_edit_idx = -1
    has_verify = False
    edit_tools = _edit_tools_from_policy()

    for i, entry in enumerate(tool_history):
        name = entry.get("tool_name", "")
        if name in edit_tools:
            last_edit_idx = i
            has_verify = False
        elif name == "bash":
            cmd = entry.get("command", entry.get("arguments", {}).get("command", ""))
            segs = re.split(r'[|;&]', cmd)
            for seg in segs:
                head = seg.strip().split()[0] if seg.strip() else ""
                if head and head not in READONLY_COMMANDS:
                    has_verify = True

    if last_edit_idx >= 0 and not has_verify:
        nudges.append("检测到代码编辑后未运行验证命令 (build/check/test)。建议先验证再继续。")

    return nudges


def check_tool_repetition(tool_history: list[dict[str, Any]]) -> list[str]:
    """检查工具是否重复调用"""
    nudges = []
    limit = _repeat_limit_from_policy()
    recent = tool_history[-limit:] if len(tool_history) >= limit else []
    if len(recent) < limit:
        return nudges

    names = [e.get("tool_name", "") for e in recent]
    args = [e.get("arguments", {}) for e in recent]

    # 连续 N 次同一工具
    if len(set(names)) == 1 and names[0]:
        nudges.append(f"已连续 {limit} 次调用同一工具 ({names[0]})，可能陷入循环。")

    # 连续 N 次同一工具+同一参数
    if len(set(str(a) for a in args)) == 1 and names[0]:
        nudges.append(f"已连续 {limit} 次调用同一参数，建议尝试其他方法。")

    return nudges


def check_consecutive_failure(tool_history: list[dict[str, Any]]) -> list[str]:
    """检查连续失败后是否尝试了替代方案"""
    nudges = []
    limit = _fail_limit_from_policy()
    fails = []
    for entry in reversed(tool_history):
        if entry.get("is_error"):
            fails.append(entry)
        else:
            break
        if len(fails) >= limit:
            break

    if len(fails) >= limit:
        last_cmd = fails[0].get("command", "")
        prev_cmd = fails[-1].get("command", "")
        if last_cmd == prev_cmd:
            nudges.append(f"同一命令连续失败 {limit} 次 ({last_cmd[:40]})，建议尝试替代方案。")
        else:
            nudges.append(f"连续 {limit} 次操作失败，建议检查环境或换方法。")

    return nudges


def check_output_completeness(output: str) -> list[str]:
    """检查输出完整性 (黑名单: 枚举明显不完整信号)"""
    nudges = []
    if not output or not output.strip():
        nudges.append("输出为空。")
        return nudges

    for pattern in _incomplete_signals_from_policy():
        if re.search(pattern, output.strip()):
            nudges.append(f"输出包含不完整信号: 以「{pattern}」开头。")
            break

    # 极短输出 (< 10 字符且不是简单确认)
    if len(output.strip()) < 10 and not output.strip().rstrip("。！？.!?"):
        nudges.append("输出过短，可能未完整回答问题。")

    return nudges


def check(tool_history: list[dict[str, Any]], output: str = "") -> BehaviorCheckResult:
    """综合行为校验 (T0, 零推理成本)"""
    nudges = []

    nudges.extend(check_edit_verify(tool_history))
    nudges.extend(check_tool_repetition(tool_history))
    nudges.extend(check_consecutive_failure(tool_history))
    if output:
        nudges.extend(check_output_completeness(output))

    return BehaviorCheckResult(
        passed=len(nudges) == 0,
        nudges=nudges,
        should_block=any("循环" in n for n in nudges),
    )
