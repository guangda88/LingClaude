"""VerifyCadenceHook — 编辑后行为校验节奏 hook (T0 零推理)。

2026-09-18 P2，对标 atomcode VerifyCadenceHook（见
~/docs/atomcode_to_lingclan_insights.md）：

  1. 校验节奏：edit/write 系工具成功后未运行验证（pytest/编译/构建）→
     在后续工具调用时注入一次性 nudge 提醒
  2. 死循环检测：同工具+同参数在滑动窗口内重复 ≥ 阈值 → 注入循环告警

红线（atomcode 文档核心观点）：全部纯字符串匹配，零模型调用——
模式匹配能解决的不用模型。

接线方式（engine/coding.py execute_tool）：
    nudge = hook.observe_call(name, kwargs)               # 执行前
    ... 执行 ...
    nudge = hook.observe_result(name, kwargs, error) or nudge  # 执行后
    if nudge:
        result.setdefault("verify_nudge", nudge)

nudge 挂到工具结果 dict 上（模型可见），与 denial_circuit_breaker 同款消费模式。
"""

from __future__ import annotations

import json
import os
from collections import deque

# 写向工具（成功 = 产生 pending 验证义务）
VERIFY_TOOLS: frozenset[str] = frozenset({
    "edit", "write", "file_create", "file_insert", "file_delete_lines",
})

# bash 命令含以下信号 → 视为一次验证行为（清空 pending）
VERIFY_SIGNAL_PATTERNS: tuple[str, ...] = (
    "pytest", "unittest",
    "compileall",
    "make test", "make check", "npm test", "cargo test", "go test",
    "ruff", "flake8", "mypy", "pyright", "tsc --noemit",
    "python -c", "python3 -c",  # 脚本式验证（import/编译检查）
)

# 死循环检测窗口与阈值（窗口私有：仅模块内部使用，公开即触发 wiring_gate 死代码守卫）
_DEDUP_WINDOW = 8
DEDUP_THRESHOLD = 3


def _is_verify_command(command: str) -> bool:
    lowered = command.lower()
    return any(p in lowered for p in VERIFY_SIGNAL_PATTERNS)


class VerifyCadenceHook:
    """编辑后未验证 nudge + 重复调用检测（纯字符串匹配，零推理）。

    状态机：
      _pending_edits      成功编辑累计数（验证成功清零）
      _nudged_for_pending 每批 pending 只提醒一次（防刷屏）
      _recent             滑动窗口内的调用 key（死循环检测）
    """

    def __init__(self, enabled: bool | None = None) -> None:
        if enabled is None:
            env = os.environ.get("LINGCLAUDE_VERIFY_CADENCE", "1")
            enabled = env.strip().lower() not in ("0", "false", "no", "off")
        self.enabled = enabled
        self._pending_edits = 0
        self._nudged_for_pending = False
        self._recent: deque[str] = deque(maxlen=_DEDUP_WINDOW)
        self._loop_nudged_key: str | None = None

    # ── 观察点 1：工具执行前 ──────────────────────────────────────────
    def observe_call(self, tool_name: str, kwargs: dict) -> str | None:
        """返回 nudge 文本；None = 不提醒。副作用：更新死循环窗口。"""
        if not self.enabled:
            return None
        key = self._call_key(tool_name, kwargs)

        # 死循环检测：窗口内同 key 已出现 ≥ 阈值-1 次，本次即触发
        count = sum(1 for k in self._recent if k == key)
        self._recent.append(key)
        if count + 1 >= DEDUP_THRESHOLD and self._loop_nudged_key != key:
            self._loop_nudged_key = key
            return (
                f"[verify_cadence] 检测到重复调用: {tool_name} 同参数已在最近 "
                f"{_DEDUP_WINDOW} 次调用中出现 {count + 1} 次 — 疑似死循环。"
                f"请更换方法或向用户求助。"
            )
        if self._loop_nudged_key == key:
            return None  # 同一循环只提醒一次

        # nudge：有 pending 编辑且当前调用不是写向工具 → 提醒一次
        # （验证命令当次不提醒；清零在 observe_result 里判定）
        if (
            self._pending_edits > 0
            and not self._nudged_for_pending
            and tool_name not in VERIFY_TOOLS
        ):
            self._nudged_for_pending = True
            return (
                f"[verify_cadence] 你有 {self._pending_edits} 次编辑尚未运行验证"
                f"（pytest/编译/构建）。建议先验证再继续，避免错误累积。"
            )
        return None

    # ── 观察点 2：工具执行后 ──────────────────────────────────────────
    def observe_result(self, tool_name: str, kwargs: dict, error: object) -> str | None:
        """执行后记账。返回 nudge 或 None（当前总是 None，留扩展口）。"""
        if not self.enabled:
            return None
        succeeded = error is None or error == ""
        if tool_name in VERIFY_TOOLS:
            if succeeded:
                # 成功编辑 → 新 pending 批次（重新获得提醒资格）
                self._pending_edits += 1
                self._nudged_for_pending = False
            return None
        if tool_name == "bash" and succeeded:
            # 只有验证型命令成功才清 pending（ls/echo 不清）；
            # 验证命令失败（exit!=0）走 error 分支不清零——失败说明验证
            # 未通过，错误信息模型已可见，保持 pending 让节奏继续。
            command = str(kwargs.get("command", ""))
            if _is_verify_command(command) and self._pending_edits > 0:
                self._pending_edits = 0
                self._nudged_for_pending = False
        return None

    @staticmethod
    def _call_key(tool_name: str, kwargs: dict) -> str:
        try:
            payload = json.dumps(kwargs, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001 — 序列化失败退化为 repr
            payload = repr(kwargs)
        return f"{tool_name}:{payload[:200]}"

    def reset(self) -> None:
        """会话切换/测试隔离用。"""
        self._pending_edits = 0
        self._nudged_for_pending = False
        self._recent.clear()
        self._loop_nudged_key = None
