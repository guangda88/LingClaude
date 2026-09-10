"""N5 空响应 token 守卫测试 — 对齐 dfaab65 落地实现。

规格（lingclaude/cli/n5_token_guard.py）:
- text_deltas==0 且 turn_output_tokens ≥ 0.95*max_tokens → 首次 "warning"
- 同 session 连续 2 次 → "error" + LingBus 告警（经 _send_lingbus_alert）
- 健康轮/低于阈/零 max_tokens → None 且清空该 session 计数
- resolve_max_tokens: model.max_tokens 优先 → cfg.max_tokens → 兜底 4096
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from lingclaude.cli import n5_token_guard as guard
from lingclaude.cli.n5_token_guard import (
    check_token_exhaustion,
    resolve_max_tokens,
    reset_n5_guard_state,
)

_alerts: list[str] = []


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    reset_n5_guard_state()
    _alerts.clear()
    monkeypatch.setattr(guard, "_send_lingbus_alert", lambda detail: _alerts.append(detail))
    yield
    reset_n5_guard_state()


def _hit(session_id: str = "s1", out: int = 950) -> str | None:
    """0 delta + 贴上限命中（阈值 0.95*1000=950）。"""
    return check_token_exhaustion(
        session_id=session_id, text_deltas=0, turn_output_tokens=out, max_tokens=1000
    )


def test_healthy_turn_returns_none():
    assert check_token_exhaustion(
        session_id="s1", text_deltas=3, turn_output_tokens=4096, max_tokens=4096
    ) is None


def test_below_threshold_returns_none_and_resets():
    assert _hit(out=949) is None  # 949 < 950
    assert "s1" not in guard._streaks


def test_first_hit_is_warning_without_alert():
    assert _hit() == "warning"
    assert _alerts == []


def test_second_consecutive_hit_errors_and_alerts_once():
    assert _hit() == "warning"
    assert _hit(out=960) == "error"
    assert len(_alerts) == 1


def test_streak_broken_by_healthy_turn():
    assert _hit() == "warning"
    assert check_token_exhaustion(
        session_id="s1", text_deltas=1, turn_output_tokens=990, max_tokens=1000
    ) is None  # 有输出 → 计数清零
    assert _hit() == "warning"  # 重计，不连升


def test_zero_max_tokens_skips():
    assert check_token_exhaustion(
        session_id="s1", text_deltas=0, turn_output_tokens=10**9, max_tokens=0
    ) is None


def test_sessions_count_independently():
    assert _hit("a") == "warning"
    assert _hit("b") == "warning"  # b 首次，不受 a 影响
    assert _hit("a") == "error"  # a 连续第 2 次


def test_resolve_prefers_model_max_tokens():
    eng = SimpleNamespace(
        config=SimpleNamespace(model=SimpleNamespace(max_tokens=16384), max_tokens=99)
    )
    assert resolve_max_tokens(eng) == 16384


def test_resolve_falls_back_to_cfg_max_tokens():
    eng = SimpleNamespace(config=SimpleNamespace(model=None, max_tokens=8192))
    assert resolve_max_tokens(eng) == 8192


def test_resolve_default_4096_when_no_config():
    assert resolve_max_tokens(object()) == 4096
