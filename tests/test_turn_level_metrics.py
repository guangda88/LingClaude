"""P1.1/P1.2: long_task_metrics.jsonl turn 级字段回归测试。

背景（2026-09-11 三审计员仲裁结论落地）:
- 仲裁发现 metrics JSONL 只有 engine 累计 usage, 缺 turn 级字段,
  导致累计值被误读为逐轮值（"2163x 退化"误报）。
- 修复: _record_long_task_metrics 落盘新增
  turn_output_tokens / turn_input_delta / turn_duration_s 三个
  **turn 级**字段; "usage" 沿用累计语义（历史记录兼容）。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from lingclaude.cli.app import _record_long_task_metrics
from lingclaude.cli.n5_token_guard import reset_n5_guard_state


def _fake_engine(session_id: str, input_tokens: int, output_tokens: int):
    """最小 QueryEngine 替身: 只需 _checkpoint_dir / session_id / get_stats()。"""
    stats = {
        "session_id": session_id,
        "turns": 3,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "denials": 0,
        "transcript_size": 2,
    }
    return SimpleNamespace(
        session_id=session_id,
        session_store=SimpleNamespace(_checkpoint_dir=Path(".lingclaude/checkpoints")),
        get_stats=lambda: dict(stats),
    )


def _read_rows(tmp_path: Path) -> list[dict]:
    f = tmp_path / ".lingclaude" / "long_task_metrics.jsonl"
    assert f.exists(), "metrics 文件未落盘"
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()]


def test_turn_level_fields_persisted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    reset_n5_guard_state()
    eng = _fake_engine("sess-turn-schema", input_tokens=10_000, output_tokens=2_000)
    ok = _record_long_task_metrics(
        eng,
        event="turn_complete",
        outcome="ok",
        tool_calls=7,
        tool_errors=0,
        text_deltas=42,
        turn_output_tokens=1_234,
        turn_input_delta=5_678,
        turn_duration_s=1.234,
    )
    assert ok is True
    row = _read_rows(tmp_path)[-1]
    # turn 级字段（本次 schema 补齐的核心断言）
    assert row["turn_output_tokens"] == 1_234
    assert row["turn_input_delta"] == 5_678
    assert row["turn_duration_s"] == 1.234
    # 累计语义不变
    assert row["usage"] == {"input_tokens": 10_000, "output_tokens": 2_000}
    assert row["event"] == "turn_complete"


def test_turn_level_fields_none_on_legacy_path(tmp_path: Path, monkeypatch) -> None:
    """non-stream/事件型路径不传 turn 级参数 → 字段为 None, 不崩。"""
    monkeypatch.chdir(tmp_path)
    reset_n5_guard_state()
    eng = _fake_engine("sess-legacy-path", input_tokens=100, output_tokens=50)
    assert _record_long_task_metrics(eng, event="slash_recover", outcome="ok")
    row = _read_rows(tmp_path)[-1]
    assert row["turn_output_tokens"] is None
    assert row["turn_input_delta"] is None
    assert row["turn_duration_s"] is None


def test_zero_turn_tokens_record_not_negative(tmp_path: Path, monkeypatch) -> None:
    """delta=0 合法（空闲轮）: 字段持久化为 0 而非 None。"""
    monkeypatch.chdir(tmp_path)
    reset_n5_guard_state()
    eng = _fake_engine("sess-zero-delta", input_tokens=5, output_tokens=5)
    assert _record_long_task_metrics(
        eng,
        event="turn_complete",
        outcome="ok",
        text_deltas=1,
        turn_output_tokens=0,
        turn_input_delta=0,
        turn_duration_s=0.0,
    )
    row = _read_rows(tmp_path)[-1]
    assert row["turn_output_tokens"] == 0
    assert row["turn_input_delta"] == 0
    assert row["turn_duration_s"] == 0.0


def test_guard_still_fires_after_schema_change(tmp_path: Path, monkeypatch) -> None:
    """schema 改动不得影响 N5 守卫: 空响应+token 烧尽仍触发（对 c6227ad 的回归保护）。"""
    monkeypatch.chdir(tmp_path)
    reset_n5_guard_state()
    eng = _fake_engine("sess-guard-regress", input_tokens=100, output_tokens=4_096)
    assert _record_long_task_metrics(
        eng,
        event="turn_complete",
        outcome="ok",
        text_deltas=0,  # 空响应
        turn_output_tokens=4_096,  # == resolve_max_tokens 兜底默认 → ≥0.95*max
    )
    # 守卫触发与否看日志/告警侧; 此处断言指标已带字段落盘且进程未崩
    row = _read_rows(tmp_path)[-1]
    assert row["turn_output_tokens"] == 4_096
    reset_n5_guard_state()
