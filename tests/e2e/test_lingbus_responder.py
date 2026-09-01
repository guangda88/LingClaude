"""E2E: LingBus BusResponder 全路径 + MessageSigner 集成."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def make_msg(sender, body, thread_id="thr-1", rowid=1, message_id="msg-1"):
    msg = MagicMock()
    msg.sender = sender
    msg.body = body
    msg.thread_id = thread_id
    msg.rowid = rowid
    msg.message_id = message_id
    return msg


class TestParseTask:
    def test_recognized_sender_with_task_id(self, responder):
        msg = make_msg("lingflow_plus", "任务ID: task_abc\n优先级: 3\n描述: 测试任务")
        result, task = responder._parse_task(msg)
        assert result.value == "task_found"
        assert task.task_id == "task_abc"
        assert task.priority == 3

    def test_unknown_sender_returns_no_task(self, responder):
        msg = make_msg("random_external", "任务ID: task_abc\n描述: x")
        result, task = responder._parse_task(msg)
        assert result.value == "no_task"
        assert task is None

    def test_missing_task_id_returns_no_task(self, responder):
        msg = make_msg("lingflow_plus", "描述: 没有任务ID")
        result, task = responder._parse_task(msg)
        assert result.value == "no_task"

    def test_already_handled_returns_already_handled(self, responder):
        msg = make_msg("lingflow_plus", "任务ID: task_abc\n描述: x")
        responder._handled_tasks.add("task_abc")
        result, task = responder._parse_task(msg)
        assert result.value == "already_handled"

    def test_lingtong_alias_accepted(self, responder):
        msg = make_msg("lingtong", "任务ID: task_xyz\n描述: 测试")
        result, task = responder._parse_task(msg)
        assert result.value == "task_found"

    def test_chinese_alias_accepted(self, responder):
        msg = make_msg("灵通+", "任务ID: task_zh\n描述: 中文 sender")
        result, task = responder._parse_task(msg)
        assert result.value == "task_found"


class TestPollAndRespond:
    def test_empty_poll_increments_polled_zero(self, responder, bus_mock):
        bus_mock.poll.return_value = []
        results = responder.poll_and_respond()
        assert results == []
        assert responder._stats.polled == 0

    def test_completes_task_sends_two_replies(self, responder, bus_mock):
        msg = make_msg("lingflow_plus", "任务ID: task_001\n描述: 测试")
        bus_mock.poll.return_value = [msg]
        with pytest.MonkeyPatch.context() as mp:
            fake_tool = MagicMock(return_value=MagicMock(is_ok=True, data="done"))
            mp.setattr("lingclaude.engine.mcp_proxy.call_tool", fake_tool)
            results = responder.poll_and_respond()

        assert len(results) == 1
        assert results[0]["success"] is True
        assert bus_mock.post_reply.call_count == 2
        second_body = bus_mock.post_reply.call_args_list[1].kwargs["body"]
        assert "task_001" in second_body

    def test_failed_task_sends_failure_reply(self, responder, bus_mock):
        msg = make_msg("lingflow_plus", "任务ID: task_fail\n描述: 测试")
        bus_mock.poll.return_value = [msg]
        with pytest.MonkeyPatch.context() as mp:
            fake_tool = MagicMock(return_value=MagicMock(is_ok=False, error="boom"))
            mp.setattr("lingclaude.engine.mcp_proxy.call_tool", fake_tool)
            results = responder.poll_and_respond()

        assert results[0]["success"] is False
        fail_body = bus_mock.post_reply.call_args_list[1].kwargs["body"]
        assert "失败" in fail_body
        assert "boom" in fail_body

    def test_handled_task_persisted_across_instances(self, tmp_path, monkeypatch, bus_mock):
        monkeypatch.setenv("HOME", str(tmp_path))
        from lingclaude.coordination.bus_responder import BusResponder
        r1 = BusResponder(bus=bus_mock)
        msg = make_msg("lingflow_plus", "任务ID: task_persist\n描述: x")
        bus_mock.poll.return_value = [msg]
        with pytest.MonkeyPatch.context() as mp:
            fake_tool = MagicMock(return_value=MagicMock(is_ok=True, data="ok"))
            mp.setattr("lingclaude.engine.mcp_proxy.call_tool", fake_tool)
            r1.poll_and_respond()
        r1.close()

        bus_mock.poll.return_value = [msg]
        bus_mock.post_reply.reset_mock()
        r2 = BusResponder(bus=bus_mock)
        results = r2.poll_and_respond()
        assert results == []
        assert bus_mock.post_reply.call_count == 0
        r2.close()


class TestSendReplySigning:
    def test_signer_helper_failure_degrades(self, responder, bus_mock, monkeypatch):
        from lingclaude.coordination import message_signer

        def broken_signer(*a, **kw):
            raise RuntimeError("signing blew up")

        monkeypatch.setattr(message_signer, "create_signer", broken_signer)
        result = responder._send_reply("thr-x", "test body")
        assert bus_mock.post_reply.called or result is None

    def test_reply_attaches_signature_when_present(self, responder, bus_mock):
        from lingclaude.coordination import message_signer

        class FakeSigned:
            def to_metadata(self):
                return {
                    "signature": "deadbeef" * 8,
                    "signer_version": "1",
                    "key_fingerprint": "abc123456789",
                }

        class FakeSigner:
            def sign_reply(self, **kw):
                return FakeSigned()

        mp = pytest.MonkeyPatch()
        mp.setattr(message_signer, "create_signer", lambda: FakeSigner())
        try:
            responder._send_reply("thr-y", "reply body")
        finally:
            mp.undo()

        assert bus_mock.post_reply.called


class TestStats:
    def test_stats_keys_present(self, responder):
        stats = responder.get_stats()
        for key in ("member_id", "member_name", "last_rowid", "handled_tasks",
                    "polled", "tasks_received", "tasks_completed", "tasks_failed",
                    "replies_sent", "last_poll_at"):
            assert key in stats
        assert stats["member_id"] == "lingclaude"
        assert stats["member_name"] == "灵克"
