"""R3-B 恢复引导钩子测试（panel_20261008_budget 定案）。

覆盖：
1. 遗言轮 handover_lastrites.md → 注入 + 消费归档（.consumed）
2. 二次 resume 不重复注入（幂等）
3. RESUME_HINT.txt（编排器默认落点）→ 注入且不删除（编排器所有权）
4. LINGCLAUDE_RESUME_HINT env 优先于默认落点
5. 无引导物 → 零干扰（不注入、不报错）
6. 来源优先级：lastrites > RESUME_HINT
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from lingclaude.core.session_journal import SessionJournal


def _write_checkpoint(tmp_path: Path, session_id: str) -> None:
    cp_dir = tmp_path / "cp"
    cp_dir.mkdir(parents=True, exist_ok=True)
    cp_data = {
        "session_id": session_id,
        "prompt": "continue task",
        "round_idx": 0,
        "used_tools": False,
        "total_input": 0,
        "total_output": 0,
        "messages": [{"role": "user", "content": "continue task"}],
        "conversation": [],
        "timestamp": "2026-01-01T00:00:00Z",
    }
    (cp_dir / f"{session_id}.json").write_text(json.dumps(cp_data, ensure_ascii=False))


class _GuidanceProvider:
    """捕获收到的 messages，第一轮回文本（无工具调用，单轮完成）。"""

    def __init__(self) -> None:
        self.call_count = 0
        self.seen: list[str] = []

    def complete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        from lingclaude.model.types import ModelResponse, ModelUsage
        self.call_count += 1
        for m in messages:
            content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
            self.seen.append(str(content))
        return Result.ok(ModelResponse(content="resumed", model="test", usage=ModelUsage()))

    async def acomplete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        from lingclaude.model.types import ModelResponse, ModelUsage
        return Result.ok(ModelResponse(content="ok", model="test", usage=ModelUsage()))

    def count_tokens(self, text: str) -> int:
        return 0


def _make_engine(tmp_path: Path, monkeypatch: Any, session_id: str):
    monkeypatch.setattr("lingclaude.core.query_engine.CHECKPOINT_DIR", tmp_path / "cp")
    _write_checkpoint(tmp_path, session_id)
    from lingclaude.core.query_engine import QueryEngine
    provider = _GuidanceProvider()
    engine = QueryEngine(model_provider=provider)
    engine.session_id = session_id
    engine._journal_dir = tmp_path / "journals"
    engine.set_runtime(MagicMock())
    return engine, provider


class TestR3BResumeGuidance:
    def test_lastrites_injected_and_archived(self, tmp_path: Path, monkeypatch: Any) -> None:
        state = tmp_path / "relay"
        state.mkdir()
        (state / "handover_lastrites.md").write_text(
            "# 遗言交接\n\n任务卡在步骤3，从这里继续。", encoding="utf-8")
        monkeypatch.setenv("LINGCLAUDE_RELAY_STATE_DIR", str(state))
        engine, provider = _make_engine(tmp_path, monkeypatch, "s_lastrites")

        result = engine.resume_interrupted()
        assert result.is_ok
        joined = "\n".join(provider.seen)
        assert "遗言交接" in joined
        assert "接力恢复引导" in joined
        # 消费归档：原文件不在，.consumed 在
        assert not (state / "handover_lastrites.md").exists()
        assert (state / "handover_lastrites.md.consumed").exists()

    def test_second_resume_no_reinject(self, tmp_path: Path, monkeypatch: Any) -> None:
        state = tmp_path / "relay"
        state.mkdir()
        (state / "handover_lastrites.md").write_text("only once", encoding="utf-8")
        monkeypatch.setenv("LINGCLAUDE_RELAY_STATE_DIR", str(state))
        engine, provider = _make_engine(tmp_path, monkeypatch, "s_twice")

        first = engine.resume_interrupted()
        assert first.is_ok
        # 恢复后无 checkpoint，第二次调用直接 no_checkpoint——
        # 幂等性的实质验证是 .consumed 已归档（文件层面不可能重复注入）
        assert not (state / "handover_lastrites.md").exists()
        assert (state / "handover_lastrites.md.consumed").exists()
        # _collect_resume_guidance 对 .consumed 文件不返回文本
        text, _src = type(engine)._collect_resume_guidance("s_twice")
        assert text == ""

    def test_resume_hint_default_path_readonly(self, tmp_path: Path, monkeypatch: Any) -> None:
        state = tmp_path / "relay"
        state.mkdir()
        (state / "RESUME_HINT.txt").write_text("从 todo#2 继续", encoding="utf-8")
        monkeypatch.setenv("LINGCLAUDE_RELAY_STATE_DIR", str(state))
        engine, provider = _make_engine(tmp_path, monkeypatch, "s_hint")

        result = engine.resume_interrupted()
        assert result.is_ok
        joined = "\n".join(provider.seen)
        assert "从 todo#2 继续" in joined
        assert "接力恢复引导" in joined
        # 编排器所有权：文件必须原样保留（不删不改名）
        assert (state / "RESUME_HINT.txt").exists()

    def test_env_hint_takes_precedence(self, tmp_path: Path, monkeypatch: Any) -> None:
        state = tmp_path / "relay"
        state.mkdir()
        (state / "RESUME_HINT.txt").write_text("file hint", encoding="utf-8")
        monkeypatch.setenv("LINGCLAUDE_RELAY_STATE_DIR", str(state))
        monkeypatch.setenv("LINGCLAUDE_RESUME_HINT", "env hint wins")
        engine, provider = _make_engine(tmp_path, monkeypatch, "s_env")

        result = engine.resume_interrupted()
        assert result.is_ok
        joined = "\n".join(provider.seen)
        assert "env hint wins" in joined
        assert "file hint" not in joined

    def test_no_guidance_no_interference(self, tmp_path: Path, monkeypatch: Any) -> None:
        state = tmp_path / "relay"
        state.mkdir()  # 空目录
        monkeypatch.setenv("LINGCLAUDE_RELAY_STATE_DIR", str(state))
        engine, provider = _make_engine(tmp_path, monkeypatch, "s_bare")

        result = engine.resume_interrupted()
        assert result.is_ok
        assert "resumed" in result.data
        # 无引导物：任何消息都不含引导标记
        assert all("接力恢复引导" not in m for m in provider.seen)

    def test_priority_lastrites_over_hint(self, tmp_path: Path, monkeypatch: Any) -> None:
        state = tmp_path / "relay"
        state.mkdir()
        (state / "handover_lastrites.md").write_text("lastrites first", encoding="utf-8")
        (state / "RESUME_HINT.txt").write_text("hint second", encoding="utf-8")
        monkeypatch.setenv("LINGCLAUDE_RELAY_STATE_DIR", str(state))
        engine, provider = _make_engine(tmp_path, monkeypatch, "s_prio")

        result = engine.resume_interrupted()
        assert result.is_ok
        joined = "\n".join(provider.seen)
        assert "lastrites first" in joined
        assert "hint second" not in joined  # 只注入优先级最高的一份
