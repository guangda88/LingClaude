"""副作用幂等 + compact×checkpoint 交互测试 — R5 阶段1 剩余项。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from lingclaude.core.session_journal import SessionJournal


class TestToolSignatures:
    def test_tool_signatures(self, tmp_path: Path) -> None:
        j = SessionJournal("s1", journal_dir=tmp_path)
        j.append("tool_call", {"tool_call_id": "tc1", "name": "read", "arguments": '{"path": "a.py"}'})
        j.append("tool_call", {"tool_call_id": "tc2", "name": "write", "arguments": '{"path": "b.py", "content": "x"}'})
        j.append("tool_call", {"tool_call_id": "tc3", "name": "read", "arguments": '{"path": "a.py"}'})  # dup
        j.append("turn_start")  # no name
        sigs = j.tool_signatures()
        assert sigs == {("read", '{"path": "a.py"}'), ("write", '{"path": "b.py", "content": "x"}')}

    def test_tool_signatures_empty(self, tmp_path: Path) -> None:
        j = SessionJournal("empty", journal_dir=tmp_path)
        assert j.tool_signatures() == set()

    def test_tool_results_by_signature(self, tmp_path: Path) -> None:
        j = SessionJournal("s1", journal_dir=tmp_path)
        j.append("tool_call", {"tool_call_id": "tc1", "name": "read", "arguments": '{"path": "a.py"}'})
        j.append("tool_result", {"tool_call_id": "tc1", "output_preview": "data", "is_error": False})
        j.append("tool_call", {"tool_call_id": "tc2", "name": "bash", "arguments": '{"command": "ls"}'})
        j.append("tool_result", {"tool_call_id": "tc2", "output_preview": "files", "is_error": False})
        results = j.tool_results_by_signature()
        assert ("read", '{"path": "a.py"}') in results
        assert ("bash", '{"command": "ls"}') in results
        assert results[("read", '{"path": "a.py"}')][0]["output_preview"] == "data"


class _ResumeProvider:
    """模拟 resume 场景：第一轮返回工具调用，第二轮返回最终文本。"""

    def __init__(self) -> None:
        self.call_count = 0

    def complete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        from lingclaude.model.types import ModelResponse, ModelUsage
        self.call_count += 1
        # 检查是否收到 journal note
        for m in messages:
            if isinstance(m, dict):
                content = m.get("content", "")
            else:
                content = getattr(m, "content", "")
            if "已执行" in str(content):
                self.saw_note = True
        if self.call_count == 1:
            from lingclaude.model.types import ToolCall
            return Result.ok(ModelResponse(
                content="", model="test", usage=ModelUsage(),
                tool_calls=(ToolCall(id="resume_tc1", name="read", arguments='{"path": "x.py"}'),),
            ))
        return Result.ok(ModelResponse(
            content="resume done", model="test", usage=ModelUsage(),
        ))

    async def acomplete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        from lingclaude.model.types import ModelResponse, ModelUsage
        return Result.ok(ModelResponse(content="ok", model="test", usage=ModelUsage()))

    def count_tokens(self, text: str) -> int:
        return 0

    saw_note = False


class TestResumeIdempotency:
    def test_resume_injects_journal_note(self, tmp_path: Path, monkeypatch: Any) -> None:
        """resume 时如果 journal 有已执行的工具签名，注入提示给模型。"""
        monkeypatch.setattr("lingclaude.core.query_engine.CHECKPOINT_DIR", tmp_path / "cp")
        from lingclaude.core.query_engine import QueryEngine

        # 准备 checkpoint
        cp_dir = tmp_path / "cp"
        cp_dir.mkdir(parents=True)
        cp_data = {
            "session_id": "resume_test",
            "prompt": "read and analyze",
            "round_idx": 0,
            "used_tools": True,
            "total_input": 100,
            "total_output": 50,
            "messages": [
                {"role": "system", "content": "test"},
                {"role": "user", "content": "read and analyze"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "old_tc", "type": "function", "function": {"name": "read", "arguments": '{"path": "old.py"}'}}
                ]},
                {"role": "tool", "content": "old_data", "name": "read", "tool_call_id": "old_tc"},
            ],
            "conversation": [],
            "timestamp": "2026-01-01T00:00:00Z",
        }
        (cp_dir / "resume_test.json").write_text(json.dumps(cp_data, ensure_ascii=False))

        # 准备 journal：记录已执行的工具
        journal_dir = tmp_path / "journals"
        j = SessionJournal("resume_test", journal_dir=journal_dir)
        j.append("tool_call", {"tool_call_id": "old_tc", "name": "read", "arguments": '{"path": "old.py"}'})
        j.append("tool_result", {"tool_call_id": "old_tc", "output_preview": "old_data", "is_error": False})

        provider = _ResumeProvider()
        engine = QueryEngine(model_provider=provider)
        engine.session_id = "resume_test"
        engine._journal_dir = tmp_path / "journals"
        engine.set_runtime(MagicMock())

        result = engine.resume_interrupted()
        assert result.is_ok
        assert "resume done" in result.data
        # 验证模型收到了 journal 注入的 note
        assert provider.saw_note, "resume did not inject journal note to model"

    def test_resume_without_journal_no_note(self, tmp_path: Path, monkeypatch: Any) -> None:
        """journal 为空时 resume 不注入额外 note。"""
        monkeypatch.setattr("lingclaude.core.query_engine.CHECKPOINT_DIR", tmp_path / "cp")
        from lingclaude.core.query_engine import QueryEngine

        cp_dir = tmp_path / "cp"
        cp_dir.mkdir(parents=True)
        cp_data = {
            "session_id": "resume_nojournal",
            "prompt": "test",
            "round_idx": 0,
            "used_tools": False,
            "total_input": 0,
            "total_output": 0,
            "messages": [
                {"role": "user", "content": "test"},
            ],
            "conversation": [],
            "timestamp": "2026-01-01T00:00:00Z",
        }
        (cp_dir / "resume_nojournal.json").write_text(json.dumps(cp_data, ensure_ascii=False))

        provider = _ResumeProvider()
        engine = QueryEngine(model_provider=provider)
        engine.session_id = "resume_nojournal"
        engine._journal_dir = tmp_path / "journals"
        engine.set_runtime(MagicMock())

        result = engine.resume_interrupted()
        assert result.is_ok
        assert not provider.saw_note, "should not inject note when journal is empty"

    def test_resume_clears_journal_on_done(self, tmp_path: Path, monkeypatch: Any) -> None:
        """resume 正常完成后 journal 应被清除。"""
        monkeypatch.setattr("lingclaude.core.query_engine.CHECKPOINT_DIR", tmp_path / "cp")
        from lingclaude.core.query_engine import QueryEngine

        cp_dir = tmp_path / "cp"
        cp_dir.mkdir(parents=True)
        cp_data = {
            "session_id": "resume_clear",
            "prompt": "test",
            "round_idx": 0,
            "used_tools": False,
            "total_input": 0, "total_output": 0,
            "messages": [{"role": "user", "content": "test"}],
            "conversation": [],
            "timestamp": "2026-01-01T00:00:00Z",
        }
        (cp_dir / "resume_clear.json").write_text(json.dumps(cp_data, ensure_ascii=False))

        journal_dir = tmp_path / "journals"
        j = SessionJournal("resume_clear", journal_dir=journal_dir)
        j.append("tool_call", {"tool_call_id": "t1", "name": "read", "arguments": "{}"})

        engine = QueryEngine(model_provider=_ResumeProvider())
        engine.session_id = "resume_clear"
        engine._journal_dir = tmp_path / "journals"
        engine.set_runtime(MagicMock())

        result = engine.resume_interrupted()
        assert result.is_ok
        # journal 被清除
        j2 = SessionJournal("resume_clear", journal_dir=journal_dir)
        assert j2.load() == []
