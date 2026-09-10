"""空响应 token 耗尽守卫 — N5。

症状背景：一次 max_tokens=4096 的调用产出 0 个 text_delta，
usage.output_tokens ≈ 3900+。事件静默发生在流循环内部，
现有日志（INFO/PROGRESS）无从区分「慢」与「无效空转」。

守卫策略（在 CLI 层 _record_long_task_metrics 的收尾点触发）：
  - 条件：单轮 text_deltas == 0 且 单轮 output_tokens ≥ 0.95 * max_tokens
  - WARNING 起步，同会话连续 2 次命中升 ERROR
  - ERROR 级别同步发 LingBus 告警（best-effort，失败只落日志）

设计约束：守卫永不 raise —— 可观测性组件不得让 agent 回合失败。
"""
from __future__ import annotations

import logging
from typing import Any

from lingclaude.coordination.alert import send_lingbus_alert

_logger = logging.getLogger(__name__)

# 0.95 阈值：接近上限但给 provider 计数误差留余量
_THRESHOLD_RATIO = 0.95
# 连续命中 2 次升 ERROR
_ERROR_STREAK = 2

_LINGBUS_ALERT_SUBJECT = "[N5守卫] 空响应 token 耗尽"

# 会话级连续命中计数（每会话每进程一份；首键为 engine.session_id）
_streaks: dict[str, int] = {}


def reset_n5_guard_state() -> None:
    """清空连续命中计数（测试用 / 新会话可选调用）。"""
    _streaks.clear()


def check_token_exhaustion(
    *,
    session_id: str,
    text_deltas: int,
    turn_output_tokens: int,
    max_tokens: int,
    event: str = "turn_complete",
) -> str | None:
    """判定单轮是否为「空响应 + token 耗尽」，按需记 WARNING/ERROR。

    Args:
        session_id: 会话 ID，用于连续命中计数。
        text_deltas: 本轮观察到的 text_delta 事件数。
        turn_output_tokens: **本轮**（非累计）output token 数。
        max_tokens: 本轮调用的 max_tokens 上限。
        event: 事件名（仅用于日志与告警正文）。

    Returns:
        命中时返回告警级别 "warning"/"error"；未命中返回 None。
    """
    if max_tokens <= 0 or text_deltas != 0:
        _streaks.pop(session_id, None)
        return None
    if turn_output_tokens < max_tokens * _THRESHOLD_RATIO:
        _streaks.pop(session_id, None)
        return None

    streak = _streaks.get(session_id, 0) + 1
    _streaks[session_id] = streak
    if streak < _ERROR_STREAK:
        _logger.warning(
            "[N5守卫] 空响应 token 耗尽(WARNING %d/%d): session=%s event=%s "
            "text_deltas=0 output_tokens=%d max_tokens=%d",
            streak, _ERROR_STREAK, session_id, event,
            turn_output_tokens, max_tokens,
        )
        return "warning"

    detail = (
        f"空响应 token 耗尽: session={session_id} event={event} "
        f"text_deltas=0 output_tokens={turn_output_tokens} "
        f"max_tokens={max_tokens} 连续命中={streak}"
    )
    _logger.error("[N5守卫] %s", detail)
    _send_lingbus_alert(detail)
    return "error"


def _send_lingbus_alert(detail: str) -> None:
    """best-effort 发 LingBus 告警（公共通路）；失败只落日志，绝不 raise。"""
    send_lingbus_alert(_LINGBUS_ALERT_SUBJECT, detail)


def resolve_max_tokens(engine: Any) -> int:
    """从 engine 解析本轮 max_tokens；拿不到返回 0(守卫跳过)。

    等价于 provider 侧的取值链: cfg.max_tokens 或 config.max_tokens,
    兜底 4096(core/config.py 的默认值)。
    """
    cfg = getattr(engine, "config", None) or getattr(engine, "_config", None)
    if cfg is None:
        return 4096
    model_cfg = getattr(cfg, "model", None)
    if model_cfg is not None and getattr(model_cfg, "max_tokens", 0):
        return int(model_cfg.max_tokens)
    mt = getattr(cfg, "max_tokens", 0)
    return int(mt) if mt else 4096
