from __future__ import annotations

"""Tests for lingclaude.coordination — BusResponder and role separation."""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lingclaude.coordination.bus_responder import (
    BusResponder,
    ParsedTask,
    ResponseStats,
    TaskParseResult,
    create_responder,
)
from lingclaude.core.role_separation import (
    AgentRoles,
    RoleConflictChecker,
    RoleType,
    create_lingclaude_role_separation,
)


# ── BusResponder tests ──


class TestBusResponder:
    def _make_message(
        self,
        body: str,
        sender: str = "lingflow_plus",
        thread_id: str = "thr_123",
        message_id: str = "msg_456",
        rowid: int = 1,
    ) -> MagicMock:
        msg = MagicMock()
        msg.body = body
        msg.sender = sender
        msg.thread_id = thread_id
        msg.message_id = message_id
        msg.rowid = rowid
        return msg

    def test_parse_task_from_lingflow_plus(self):
        responder = BusResponder.__new__(BusResponder)
        responder._handled_tasks = set()
        msg = self._make_message(
            body="📋 任务派发\n任务ID: task_abc123\n优先级: 3\n描述: 重构认证模块\n请回复",
        )
        result, task = responder._parse_task(msg)
        assert result == TaskParseResult.TASK_FOUND
        assert task is not None
        assert task.task_id == "task_abc123"
        assert task.description == "重构认证模块"
        assert task.priority == 3

    def test_parse_task_wrong_sender(self):
        responder = BusResponder.__new__(BusResponder)
        responder._handled_tasks = set()
        msg = self._make_message(body="some message", sender="random_agent")
        result, task = responder._parse_task(msg)
        assert result == TaskParseResult.NO_TASK

    def test_parse_task_already_handled(self):
        responder = BusResponder.__new__(BusResponder)
        responder._handled_tasks = {"task_xyz"}
        msg = self._make_message(body="📋 任务派发\n任务ID: task_xyz\n描述: test")
        result, task = responder._parse_task(msg)
        assert result == TaskParseResult.ALREADY_HANDLED

    def test_parse_task_no_task_id(self):
        responder = BusResponder.__new__(BusResponder)
        responder._handled_tasks = set()
        msg = self._make_message(body="just a regular message")
        result, task = responder._parse_task(msg)
        assert result == TaskParseResult.NO_TASK

    def test_parse_task_default_priority(self):
        responder = BusResponder.__new__(BusResponder)
        responder._handled_tasks = set()
        msg = self._make_message(
            body="📋 任务派发\n任务ID: task_def456\n描述: some task",
        )
        result, task = responder._parse_task(msg)
        assert result == TaskParseResult.TASK_FOUND
        assert task.priority == 5

    def test_get_stats(self):
        responder = BusResponder.__new__(BusResponder)
        responder._stats = ResponseStats(
            polled=10,
            tasks_received=3,
            tasks_completed=2,
            tasks_failed=1,
        )
        responder._last_rowid = 42
        responder._handled_tasks = {"t1", "t2", "t3"}
        stats = responder.get_stats()
        assert stats["polled"] == 10
        assert stats["tasks_received"] == 3
        assert stats["tasks_completed"] == 2
        assert stats["tasks_failed"] == 1
        assert stats["handled_tasks"] == 3

    def test_poll_and_respond_with_mock_bus(self, tmp_path: Path):
        state_file = tmp_path / "bus_state.json"
        bus = MagicMock()
        task_msg = self._make_message(
            body="📋 任务派发\n任务ID: task_mock001\n优先级: 5\n描述: echo test\n请回复",
            rowid=10,
        )
        bus.poll.return_value = [task_msg]
        bus.post_reply.return_value = "reply_123"

        responder = BusResponder(bus=bus)
        responder._state_file = state_file

        with patch.object(responder, "_execute_task") as mock_exec:
            mock_result = MagicMock()
            mock_result.is_ok = True
            mock_result.data = "test output"
            mock_exec.return_value = mock_result

            results = responder.poll_and_respond()

        assert len(results) == 1
        assert results[0]["task_id"] == "task_mock001"
        assert results[0]["success"] is True
        assert responder._last_rowid == 10
        assert responder._stats.tasks_received == 1
        assert responder._stats.tasks_completed == 1
        assert bus.post_reply.call_count == 2  # ack + completion

    def test_poll_and_respond_no_messages(self, tmp_path: Path):
        state_file = tmp_path / "bus_state.json"
        bus = MagicMock()
        bus.poll.return_value = []

        responder = BusResponder(bus=bus)
        responder._state_file = state_file

        results = responder.poll_and_respond()
        assert results == []
        assert responder._stats.polled == 0


class TestCreateResponder:
    def test_factory(self):
        responder = create_responder()
        assert isinstance(responder, BusResponder)


# ── Role Separation activation tests ──


class TestRoleSeparationActivation:
    def test_lingclaude_participant_only(self):
        checker = create_lingclaude_role_separation()
        lingclaude = next(
            a for a in checker.agent_roles if a.agent_id == "lingclaude"
        )
        assert lingclaude.enabled is True
        assert lingclaude.has_role(RoleType.PARTICIPANT)
        assert not lingclaude.has_role(RoleType.RULE_MAKER)
        assert not lingclaude.has_role(RoleType.REFEREE)
        assert not lingclaude.has_role(RoleType.SCORE_KEEPER)

    def test_lingtong_referee(self):
        checker = create_lingclaude_role_separation()
        lingtong = next(
            a for a in checker.agent_roles if a.agent_id == "lingtong"
        )
        assert lingtong.enabled is True
        assert lingtong.has_role(RoleType.REFEREE)

    def test_lingyan_rule_maker(self):
        checker = create_lingclaude_role_separation()
        lingyan = next(
            a for a in checker.agent_roles if a.agent_id == "lingyan"
        )
        assert lingyan.enabled is True
        assert lingyan.has_role(RoleType.RULE_MAKER)

    def test_lingzhi_score_keeper(self):
        checker = create_lingclaude_role_separation()
        lingzhi = next(
            a for a in checker.agent_roles if a.agent_id == "lingzhi"
        )
        assert lingzhi.enabled is True
        assert lingzhi.has_role(RoleType.SCORE_KEEPER)

    def test_no_conflicts(self):
        checker = create_lingclaude_role_separation()
        conflicts = checker.check_conflicts()
        assert len(conflicts) == 0

    def test_four_agents_registered(self):
        checker = create_lingclaude_role_separation()
        assert len(checker.agent_roles) == 4
        agent_ids = {a.agent_id for a in checker.agent_roles}
        assert agent_ids == {"lingclaude", "lingtong", "lingyan", "lingzhi"}

    def test_all_enabled(self):
        checker = create_lingclaude_role_separation()
        assert all(a.enabled for a in checker.agent_roles)

    def test_lingclaude_can_contribute(self):
        checker = create_lingclaude_role_separation()
        result = checker.validate_action("lingclaude", "contribute_code")
        assert result["allowed"] is True

    def test_lingclaude_cannot_evaluate(self):
        checker = create_lingclaude_role_separation()
        result = checker.validate_action("lingclaude", "evaluate_member")
        assert result["allowed"] is False
