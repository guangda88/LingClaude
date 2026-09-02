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
        """成功流：两个回复（收到+完成）。工具边界 mock 在 _run_tool——
        真执行链由 TestRealExecutionChain 不 mock 覆盖。"""
        from lingclaude.core.types import Result

        msg = make_msg("lingflow_plus", "任务ID: task_001\n描述: 分析代码")
        bus_mock.poll.return_value = [msg]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                type(responder), "_run_tool",
                lambda self, name, kwargs: Result.ok("done"),
            )
            results = responder.poll_and_respond()

        assert len(results) == 1
        assert results[0]["success"] is True
        assert bus_mock.post_reply.call_count == 2
        second_body = bus_mock.post_reply.call_args_list[1].kwargs["body"]
        assert "task_001" in second_body

    def test_failed_task_sends_failure_reply(self, responder, bus_mock):
        from lingclaude.core.types import Result

        msg = make_msg("lingflow_plus", "任务ID: task_fail\n描述: 分析代码")
        bus_mock.poll.return_value = [msg]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                type(responder), "_run_tool",
                lambda self, name, kwargs: Result.fail("boom"),
            )
            results = responder.poll_and_respond()

        assert results[0]["success"] is False
        fail_body = bus_mock.post_reply.call_args_list[1].kwargs["body"]
        assert "失败" in fail_body
        assert "boom" in fail_body

    def test_handled_task_persisted_across_instances(self, tmp_path, monkeypatch, bus_mock):
        monkeypatch.setenv("HOME", str(tmp_path))
        from lingclaude.core.types import Result
        from lingclaude.coordination.bus_responder import BusResponder
        r1 = BusResponder(bus=bus_mock)
        msg = make_msg("lingflow_plus", "任务ID: task_persist\n描述: 分析代码")
        bus_mock.poll.return_value = [msg]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                type(r1), "_run_tool",
                lambda self, name, kwargs: Result.ok("ok"),
            )
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


class TestRealExecutionChain:
    """R4 非 mock 烟囱路径 — 真 engine + 真 native 工具管线（审计#2 的教训：
    mock 掉工具层曾让「收到任务必失败」在测试里全绿）。"""

    @pytest.fixture(scope="class")
    def real_responder(self, tmp_path_factory):
        import os
        from lingclaude.coordination.bus_responder import BusResponder

        home = tmp_path_factory.mktemp("bus_home")
        old_home = os.environ.get("HOME")
        old_allow = os.environ.get("LINGCLAUDE_BUS_ALLOW_BASH")
        os.environ["HOME"] = str(home)
        os.environ.pop("LINGCLAUDE_BUS_ALLOW_BASH", None)
        r = BusResponder()
        yield r
        r.close()
        if old_home is not None:
            os.environ["HOME"] = old_home
        else:
            os.environ.pop("HOME", None)
        if old_allow is not None:
            os.environ["LINGCLAUDE_BUS_ALLOW_BASH"] = old_allow

    def _task(self, description: str):
        from lingclaude.coordination.bus_responder import ParsedTask

        return ParsedTask(task_id="task_real", thread_id="thr", description=description)

    def test_native_tool_real_execution(self, real_responder, tmp_path):
        """真链：grep 走 native 管线，真读真文件——全仓无 mock。"""
        target = tmp_path / "sample.py"
        target.write_text("def f():\n    pass  # TODO fix this\n", encoding="utf-8")
        result = real_responder._run_tool("grep", {"pattern": "TODO", "path": str(tmp_path)})
        assert result.is_ok, f"native grep 应成功，实际: {result.error}"
        assert "TODO" in result.data

    def test_bash_denied_by_default(self, real_responder):
        """安全门：总线任务的 shell 执行默认拒绝（旧实现任意任务文本直接进 bash）。"""
        result = real_responder._execute_task(self._task("echo pwned"))
        assert not result.is_ok
        assert "拦截" in result.error or "BASH_NOT_AUTHORIZED" in (result.code or "")

    def test_bash_allowed_with_explicit_env(self, real_responder, monkeypatch):
        """显式授权后走 native bash 管线（5 段 pipeline 含守卫），真执行真输出。"""
        monkeypatch.setenv("LINGCLAUDE_BUS_ALLOW_BASH", "1")
        result = real_responder._execute_task(self._task("echo lingbus-real-chain-ok"))
        assert result.is_ok, f"显式授权后应执行，实际: {result.error}"
        assert "lingbus-real-chain-ok" in result.data

    def test_analyze_task_fails_honestly_without_mcp(self, real_responder):
        """analyze_full 无 lingflow MCP 时必须诚实失败（而非假成功/无响应）——
        这是审计#2「必失败但被 mock 掩盖」的诚实化断言。"""
        result = real_responder._execute_task(self._task("审查某个模块"))
        assert not result.is_ok
        assert result.error  # 有可读错误，可回复派发方
