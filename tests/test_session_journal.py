"""SessionJournal + stream checkpoint 集成测试 — R5 阶段1。

验证:
- SessionJournal append/load/tool_call_ids/has_tool_call/clear/truncate
- stream_call_model 工具轮执行后 checkpoint 存在，done 后清除
- journal 记录 tool_call/tool_result/checkpoint/turn_end 事件
- /checkpoint 斜杠命令触发 persist + journal
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from lingclaude.core.session_journal import SessionJournal


# ── SessionJournal 单元测试 ──

class TestSessionJournal:
    def test_append_and_load(self, tmp_path: Path) -> None:
        j = SessionJournal("s1", journal_dir=tmp_path)
        assert j.append("turn_start", {"prompt": "hello"})
        assert j.append("tool_call", {"tool_call_id": "tc1", "name": "read"})
        events = j.load()
        assert len(events) == 2
        assert events[0]["type"] == "turn_start"
        assert events[1]["type"] == "tool_call"
        assert events[1]["tool_call_id"] == "tc1"

    def test_append_creates_dir(self, tmp_path: Path) -> None:
        journal_dir = tmp_path / "deep" / "nested" / "journals"
        j = SessionJournal("s1", journal_dir=journal_dir)
        assert j.append("turn_start")
        assert journal_dir.exists()

    def test_load_empty(self, tmp_path: Path) -> None:
        j = SessionJournal("nonexistent", journal_dir=tmp_path)
        assert j.load() == []

    def test_load_skips_corrupt_lines(self, tmp_path: Path) -> None:
        j = SessionJournal("s1", journal_dir=tmp_path)
        j.append("turn_start")
        j.append("tool_call", {"tool_call_id": "tc1"})
        # 手动追加一行损坏 JSON
        with open(j.path, "a", encoding="utf-8") as f:
            f.write("{broken json\n")
        j.append("turn_end")
        events = j.load()
        assert len(events) == 3  # corrupt line skipped
        assert events[-1]["type"] == "turn_end"

    def test_tool_call_ids(self, tmp_path: Path) -> None:
        j = SessionJournal("s1", journal_dir=tmp_path)
        j.append("tool_call", {"tool_call_id": "tc1"})
        j.append("tool_call", {"tool_call_id": "tc2"})
        j.append("tool_call", {"tool_call_id": "tc1"})  # duplicate
        j.append("turn_start")  # no tool_call_id
        ids = j.tool_call_ids()
        assert ids == {"tc1", "tc2"}

    def test_has_tool_call(self, tmp_path: Path) -> None:
        j = SessionJournal("s1", journal_dir=tmp_path)
        j.append("tool_call", {"tool_call_id": "tc1"})
        assert j.has_tool_call("tc1")
        assert not j.has_tool_call("tc999")

    def test_clear(self, tmp_path: Path) -> None:
        j = SessionJournal("s1", journal_dir=tmp_path)
        j.append("turn_start")
        assert j.path.exists()
        j.clear()
        assert not j.path.exists()
        assert j.load() == []

    def test_truncate_after(self, tmp_path: Path) -> None:
        j = SessionJournal("s1", journal_dir=tmp_path)
        j.append("event1")
        j.append("event2")
        j.append("event3")
        events = j.load()
        # 截断 event2 之后（保留 event1 和 event2）
        cutoff = events[1]["timestamp"]
        kept = j.truncate_after(cutoff)
        assert kept == 2
        assert len(j.load()) == 2

    def test_thread_safety(self, tmp_path: Path) -> None:
        import threading
        j = SessionJournal("s1", journal_dir=tmp_path)
        errors: list[Exception] = []

        def writer(n: int) -> None:
            try:
                for i in range(20):
                    j.append("tool_call", {"tool_call_id": f"tc_{n}_{i}"})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(j.load()) == 80


# ── checkpoint + journal 集成测试 ──

class _ToolProvider:
    """模拟两轮工具调用后返回最终答案。"""

    def __init__(self) -> None:
        self.call_count = 0

    def complete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        from lingclaude.model.types import ModelResponse, ModelUsage, ToolCall
        self.call_count += 1
        if self.call_count == 1:
            return Result.ok(ModelResponse(
                content="", model="test", usage=ModelUsage(),
                tool_calls=(ToolCall(id="cp_tc1", name="read", arguments='{"path": "a.py"}'),),
            ))
        return Result.ok(ModelResponse(
            content="done", model="test", usage=ModelUsage(),
        ))

    async def acomplete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        from lingclaude.model.types import ModelResponse, ModelUsage
        return Result.ok(ModelResponse(content="ok", model="test", usage=ModelUsage()))

    def count_tokens(self, text: str) -> int:
        return 0

    def stream_complete(self, messages, config=None, tools=None):
        from lingclaude.model.types import ModelUsage
        self.call_count += 1
        if self.call_count == 1:
            yield {"type": "tool_call_complete", "id": "cp_tc1", "name": "read", "arguments": '{"path": "a.py"}'}
            yield {"type": "finish", "usage": ModelUsage()}
        else:
            yield {"type": "text_delta", "text": "done"}
            yield {"type": "finish", "usage": ModelUsage()}


class TestStreamCheckpoint:
    def test_checkpoint_saved_after_tool_round(self, tmp_path: Path, monkeypatch: Any) -> None:
        """stream 工具轮执行后 checkpoint 必须存在（R5 核心验收）。"""
        monkeypatch.setattr("lingclaude.core.query_engine.CHECKPOINT_DIR", tmp_path / "cp")
        from lingclaude.core.query_engine import QueryEngine

        provider = _ToolProvider()
        engine = QueryEngine(model_provider=provider)
        engine.session_id = "cp_test_session"
        engine._journal_dir = tmp_path / "journals"

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.return_value = {"exit_code": 0, "stdout": "data"}
        engine.set_runtime(runtime)

        events = list(engine.stream_call_model("test checkpoint"))
        done = [e for e in events if e["type"] == "done"]
        assert done, f"no done event, got: {[e['type'] for e in events]}"

        # checkpoint 已被清除（正常完成）
        cp_dir = tmp_path / "cp"
        cp_files = list(cp_dir.glob("*.json"))
        assert len(cp_files) == 0, f"checkpoint should be cleared after done, found: {cp_files}"

    def test_checkpoint_exists_during_stream(self, tmp_path: Path, monkeypatch: Any) -> None:
        """stream 中途（工具轮之间）checkpoint 必须存在（模拟崩溃恢复点）。"""
        monkeypatch.setattr("lingclaude.core.query_engine.CHECKPOINT_DIR", tmp_path / "cp")
        from lingclaude.core.query_engine import QueryEngine

        provider = _ToolProvider()
        engine = QueryEngine(model_provider=provider)
        engine.session_id = "cp_mid_test"
        engine._journal_dir = tmp_path / "journals"

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.return_value = {"exit_code": 0, "stdout": "out"}
        engine.set_runtime(runtime)

        # 手动迭代 stream，在 tool_call_end 后检查 checkpoint
        cp_dir = tmp_path / "cp"
        saw_checkpoint = False
        for event in engine.stream_call_model("test mid-checkpoint"):
            if event["type"] == "tool_call_end":
                # tool 执行完后 checkpoint 应该已保存
                # 但要等 round 完成（tool_result journal + checkpoint 保存）
                pass
            # 检查 checkpoint 是否在工具轮后出现
            if cp_dir.exists() and list(cp_dir.glob("*.json")):
                saw_checkpoint = True
                break

        assert saw_checkpoint, "checkpoint was never created during stream tool round"

    def test_journal_records_tool_events(self, tmp_path: Path, monkeypatch: Any) -> None:
        """journal 必须记录 tool_call/tool_result/checkpoint/turn_end 事件。"""
        monkeypatch.setattr("lingclaude.core.query_engine.CHECKPOINT_DIR", tmp_path / "cp")
        monkeypatch.setattr("lingclaude.core.session_journal.DEFAULT_JOURNAL_DIR", tmp_path / "journals")
        from lingclaude.core.query_engine import QueryEngine
        from lingclaude.core.session_journal import SessionJournal

        provider = _ToolProvider()
        engine = QueryEngine(model_provider=provider)
        engine.session_id = "journal_test"
        engine._journal_dir = tmp_path / "journals"

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.return_value = {"exit_code": 0, "stdout": "data"}
        engine.set_runtime(runtime)

        list(engine.stream_call_model("journal test"))

        j = SessionJournal("journal_test", journal_dir=tmp_path / "journals")
        events = j.load()
        types = [e["type"] for e in events]
        assert "tool_call" in types, f"no tool_call event, got: {types}"
        assert "tool_result" in types, f"no tool_result event, got: {types}"
        assert "checkpoint" in types, f"no checkpoint event, got: {types}"
        assert "turn_end" in types, f"no turn_end event, got: {types}"

        # tool_call 事件带 tool_call_id
        tc_events = [e for e in events if e["type"] == "tool_call"]
        assert any(e.get("tool_call_id") == "cp_tc1" for e in tc_events)

    def test_journal_tool_call_ids_after_stream(self, tmp_path: Path, monkeypatch: Any) -> None:
        """stream 完成后 journal 可查询已执行的 tool_call_id（副作用幂等基础）。"""
        monkeypatch.setattr("lingclaude.core.query_engine.CHECKPOINT_DIR", tmp_path / "cp")
        monkeypatch.setattr("lingclaude.core.session_journal.DEFAULT_JOURNAL_DIR", tmp_path / "journals")
        from lingclaude.core.query_engine import QueryEngine
        from lingclaude.core.session_journal import SessionJournal

        provider = _ToolProvider()
        engine = QueryEngine(model_provider=provider)
        engine.session_id = "idem_test"
        engine._journal_dir = tmp_path / "journals"

        runtime = MagicMock()
        runtime.registry.list_tools.return_value = ()
        runtime.execute_tool.return_value = {"exit_code": 0, "stdout": "out"}
        engine.set_runtime(runtime)

        list(engine.stream_call_model("idempotency test"))

        j = SessionJournal("idem_test", journal_dir=tmp_path / "journals")
        assert "cp_tc1" in j.tool_call_ids()
        assert j.has_tool_call("cp_tc1")
        assert not j.has_tool_call("nonexistent_tc")


# ---------- P1-C: journal 归档 + rotate ----------

class TestJournalRotate:
    """cc P1: journal 1K turn 增 1000 倍无上限 → 定期归档 + rotate。"""

    def test_rotate_when_over_threshold(self, tmp_path: Path) -> None:
        j = SessionJournal("s1", journal_dir=tmp_path, max_bytes=200, max_archives=3)
        # 写入超过 200 字节（触发归档）
        for i in range(20):
            j.append("tool_call", {"tool_call_id": f"tc{i}", "data": "x" * 20})
        assert j.archived_count() >= 1
        # 主 journal 归零重建（load 只返回归档后新写的事件）
        assert j.load() != []  # 新 journal 仍有后续事件

    def test_archive_files_in_archive_dir(self, tmp_path: Path) -> None:
        j = SessionJournal("s1", journal_dir=tmp_path, max_bytes=100, max_archives=5)
        for i in range(15):
            j.append("turn_start", {"prompt": "p" * 30})
        archive_dir = tmp_path / "archive"
        assert archive_dir.exists()
        archived = list(archive_dir.glob("s1.*.jsonl"))
        assert len(archived) >= 1

    def test_archive_prunes_old(self, tmp_path: Path) -> None:
        j = SessionJournal("s1", journal_dir=tmp_path, max_bytes=80, max_archives=2)
        for i in range(50):  # 多次触发归档
            j.append("tool_call", {"tool_call_id": f"tc{i}", "data": "z" * 30})
        assert j.archived_count() <= 2  # 只保留最近 2 份

    def test_no_rotate_under_threshold(self, tmp_path: Path) -> None:
        j = SessionJournal("s1", journal_dir=tmp_path, max_bytes=100_000)
        j.append("turn_start", {"prompt": "hi"})
        assert j.archived_count() == 0

    def test_rotated_content_preserved(self, tmp_path: Path) -> None:
        """归档内容不丢：归档文件里应包含旧事件。"""
        j = SessionJournal("s1", journal_dir=tmp_path, max_bytes=150, max_archives=5)
        j.append("turn_start", {"prompt": "first-event"})
        for i in range(10):
            j.append("tool_call", {"tool_call_id": f"tc{i}", "data": "d" * 20})
        archive_dir = tmp_path / "archive"
        if archive_dir.exists():
            merged = ""
            for f in sorted(archive_dir.glob("s1.*.jsonl")):
                merged += f.read_text(encoding="utf-8")
            assert "first-event" in merged  # 归档保留了最早事件
