"""Tests for lingclaude.core.handover — Handover V2."""
from __future__ import annotations

import json
from pathlib import Path


from lingclaude.core.handover import (
    HANDOVER_VERSION,
    Checkpoint,
    HandoverReader,
    HandoverV2,
    HandoverWriter,
    InfrastructureEntry,
    TaskSource,
    TaskStatus,
)


class TestCheckpoint:
    def test_auto_timestamps(self):
        cp = Checkpoint(task_id="T1", description="test task")
        assert cp.created_at
        assert cp.updated_at
        assert cp.source == TaskSource.USER
        assert cp.status == TaskStatus.IN_DISCUSSION
        assert cp.user_confirmed is False

    def test_explicit_fields(self):
        cp = Checkpoint(
            task_id="T2",
            description="explicit",
            source=TaskSource.SELF,
            status=TaskStatus.IN_PROGRESS,
            user_raw="user raw text",
            ai_interpretation="ai understood X",
            user_confirmed=True,
        )
        assert cp.source == TaskSource.SELF
        assert cp.status == TaskStatus.IN_PROGRESS
        assert cp.user_raw == "user raw text"
        assert cp.user_confirmed is True


class TestHandoverV2:
    def test_empty_handover(self):
        h = HandoverV2(member_id="test")
        assert h.member_id == "test"
        assert h.user_tasks == []
        assert h.version == HANDOVER_VERSION
        assert h.timestamp

    def test_add_task(self):
        h = HandoverV2(member_id="test")
        cp = Checkpoint(task_id="T1", description="do something")
        h.add_task(cp)
        assert len(h.user_tasks) == 1
        assert h.user_tasks[0].task_id == "T1"

    def test_add_discussion(self):
        h = HandoverV2(member_id="test")
        h.add_discussion("handover design", "waiting for user decision")
        assert len(h.pending_discussions) == 1
        assert h.pending_discussions[0].status == TaskStatus.IN_DISCUSSION

    def test_complete_task(self):
        h = HandoverV2(member_id="test")
        h.add_task(Checkpoint(task_id="T1", description="task1"))
        result = h.complete_task("T1", output="done.md")
        assert result is True
        assert len(h.user_tasks) == 0
        assert len(h.recently_completed) == 1
        assert h.recently_completed[0].status == TaskStatus.COMPLETED
        assert h.recently_completed[0].output == "done.md"

    def test_complete_task_not_found(self):
        h = HandoverV2(member_id="test")
        result = h.complete_task("NONEXIST", output="x")
        assert result is False

    def test_block_task(self):
        h = HandoverV2(member_id="test")
        h.add_task(Checkpoint(task_id="T1", description="blocked task"))
        result = h.block_task("T1", reason="waiting SSH")
        assert result is True
        assert len(h.user_tasks) == 0
        assert len(h.blockers) == 1
        assert h.blockers[0].blocker_reason == "waiting SSH"

    def test_block_task_not_found(self):
        h = HandoverV2(member_id="test")
        result = h.block_task("NONEXIST", reason="x")
        assert result is False

    def test_to_json_roundtrip(self):
        h = HandoverV2(member_id="test")
        h.add_task(Checkpoint(task_id="T1", description="task1", user_raw="raw"))
        h.add_discussion("topic A", "context A")
        h.add_task(Checkpoint(task_id="T2", description="task2"))
        h.complete_task("T2", output="out.md")

        json_str = h.to_json()
        data = json.loads(json_str)
        assert data["version"] == HANDOVER_VERSION
        assert data["member_id"] == "test"
        assert len(data["user_tasks"]) == 1
        assert len(data["pending_discussions"]) == 1
        assert len(data["recently_completed"]) == 1

    def test_from_dict_roundtrip(self):
        h = HandoverV2(member_id="test")
        h.add_task(Checkpoint(task_id="T1", description="task1"))
        h.add_discussion("disc", "ctx")

        data = json.loads(h.to_json())
        h2 = HandoverV2.from_dict(data)
        assert h2.member_id == "test"
        assert len(h2.user_tasks) == 1
        assert h2.user_tasks[0].task_id == "T1"
        assert len(h2.pending_discussions) == 1

    def test_to_markdown_has_sections(self):
        h = HandoverV2(member_id="lingclaude")
        h.add_task(Checkpoint(task_id="T1", description="task1", user_raw="do it"))
        h.add_discussion("design", "needs review")
        h.add_task(Checkpoint(task_id="T2", description="done task"))
        h.complete_task("T2", output="result.md")

        md = h.to_markdown()
        assert "# lingclaude Handover" in md
        assert "进行中的用户任务" in md
        assert "待继续" in md
        assert "已完成" in md
        assert "T1" in md

    def test_to_markdown_empty(self):
        h = HandoverV2(member_id="test")
        md = h.to_markdown()
        assert "# test Handover" in md
        assert "进行中" not in md
        assert "待继续" not in md

    def test_to_markdown_with_infrastructure(self):
        h = HandoverV2(member_id="test")
        h.infrastructure.append(InfrastructureEntry(service="lingclaude", port=8765, status="up"))
        md = h.to_markdown()
        assert "基础设施" in md
        assert "8765" in md

    def test_to_markdown_with_blockers(self):
        h = HandoverV2(member_id="test")
        h.add_task(Checkpoint(task_id="T1", description="blocked"))
        h.block_task("T1", reason="need SSH access")
        md = h.to_markdown()
        assert "阻塞项" in md
        assert "need SSH access" in md

    def test_to_markdown_with_notes(self):
        h = HandoverV2(member_id="test", notes="something important")
        md = h.to_markdown()
        assert "备注" in md
        assert "something important" in md


class TestHandoverWriter:
    def test_create_and_write(self, tmp_path: Path):
        writer = HandoverWriter(tmp_path, member_id="test")
        h = writer.load_or_create()
        assert h.member_id == "test"

        writer.add_task("T1", "do something", user_raw="raw text")
        result = writer.write()
        assert result.is_ok

        json_path = tmp_path / "handover.json"
        md_path = tmp_path / "handover.md"
        assert json_path.exists()
        assert md_path.exists()

        data = json.loads(json_path.read_text())
        assert data["member_id"] == "test"
        assert len(data["user_tasks"]) == 1

        md = md_path.read_text()
        assert "test Handover" in md

    def test_load_existing(self, tmp_path: Path):
        writer1 = HandoverWriter(tmp_path, member_id="test")
        writer1.add_task("T1", "task one")
        writer1.write()

        writer2 = HandoverWriter(tmp_path, member_id="test")
        h = writer2.load_or_create()
        assert len(h.user_tasks) == 1
        assert h.user_tasks[0].task_id == "T1"

    def test_add_task_auto_writes(self, tmp_path: Path):
        writer = HandoverWriter(tmp_path, member_id="test")
        writer.add_task("T1", "auto write task", user_raw="raw")
        json_path = tmp_path / "handover.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text())
        assert len(data["user_tasks"]) == 1

    def test_confirm_task(self, tmp_path: Path):
        writer = HandoverWriter(tmp_path, member_id="test")
        writer.add_task("T1", "confirm me", user_raw="raw")
        assert writer.confirm_task("T1") is True
        assert writer.handover.user_tasks[0].user_confirmed is True
        assert writer.confirm_task("NONEXIST") is False

    def test_complete_task(self, tmp_path: Path):
        writer = HandoverWriter(tmp_path, member_id="test")
        writer.add_task("T1", "complete me")
        assert writer.complete_task("T1", output="done.md") is True
        assert len(writer.handover.user_tasks) == 0
        assert len(writer.handover.recently_completed) == 1
        assert writer.complete_task("NONEXIST") is False

    def test_block_task(self, tmp_path: Path):
        writer = HandoverWriter(tmp_path, member_id="test")
        writer.add_task("T1", "block me")
        assert writer.block_task("T1", reason="waiting") is True
        assert len(writer.handover.blockers) == 1
        assert writer.block_task("NONEXIST") is False

    def test_add_discussion(self, tmp_path: Path):
        writer = HandoverWriter(tmp_path, member_id="test")
        writer.add_discussion("topic A", "context A")
        assert len(writer.handover.pending_discussions) == 1
        json_path = tmp_path / "handover.json"
        assert json_path.exists()


class TestHandoverReader:
    def test_read_not_found(self, tmp_path: Path):
        reader = HandoverReader(tmp_path)
        result = reader.read()
        assert result.is_error
        assert result.code == "NOT_FOUND"

    def test_read_valid(self, tmp_path: Path):
        writer = HandoverWriter(tmp_path, member_id="test")
        writer.add_task("T1", "a task")
        writer.write()

        reader = HandoverReader(tmp_path)
        result = reader.read()
        assert result.is_ok
        assert result.data.member_id == "test"
        assert len(result.data.user_tasks) == 1

    def test_has_pending_tasks(self, tmp_path: Path):
        writer = HandoverWriter(tmp_path, member_id="test")
        writer.add_task("T1", "pending task")
        writer.write()

        reader = HandoverReader(tmp_path)
        assert reader.has_pending_tasks() is True

    def test_has_pending_tasks_empty(self, tmp_path: Path):
        writer = HandoverWriter(tmp_path, member_id="test")
        writer.write()

        reader = HandoverReader(tmp_path)
        assert reader.has_pending_tasks() is False

    def test_user_tasks_to_resume(self, tmp_path: Path):
        writer = HandoverWriter(tmp_path, member_id="test")
        writer.add_task("T1", "user task", source=TaskSource.USER)
        writer.add_task("T2", "self task", source=TaskSource.SELF)
        writer.write()

        reader = HandoverReader(tmp_path)
        tasks = reader.user_tasks_to_resume()
        assert len(tasks) == 1
        assert tasks[0].task_id == "T1"

    def test_incomplete_discussions(self, tmp_path: Path):
        writer = HandoverWriter(tmp_path, member_id="test")
        writer.add_discussion("open topic", "needs followup")
        writer.write()

        reader = HandoverReader(tmp_path)
        discs = reader.incomplete_discussions()
        assert len(discs) == 1
        assert discs[0].description == "open topic"

    def test_corrupt_json(self, tmp_path: Path):
        json_path = tmp_path / "handover.json"
        json_path.write_text("{bad json")
        reader = HandoverReader(tmp_path)
        result = reader.read()
        assert result.is_error
        assert result.code == "PARSE_ERROR"


class TestTaskSourceEnum:
    def test_values(self):
        assert TaskSource.USER.value == "user_directed"
        assert TaskSource.SELF.value == "self_generated"
        assert TaskSource.LINGBUS.value == "lingbus_originated"


class TestTaskStatusEnum:
    def test_values(self):
        assert TaskStatus.IN_DISCUSSION.value == "in_discussion"
        assert TaskStatus.IN_PROGRESS.value == "in_progress"
        assert TaskStatus.COMPLETED.value == "completed"
        assert TaskStatus.BLOCKED.value == "blocked"
