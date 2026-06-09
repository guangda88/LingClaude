"""Tests for lingmessage protocol enhancement — M1灵信协议强化"""
from __future__ import annotations

from unittest.mock import MagicMock

from lingclaude.core.query_engine import QueryEngine


class TestProtocolNotifications:

    def test_notify_completion_with_mailbox(self):
        engine = QueryEngine()
        mailbox = MagicMock()
        engine.init_mailbox(mailbox)

        engine.notify_completion("M0安全基础", "硬中断+验证关卡已完成")
        mailbox.open_thread.assert_called_once()
        call_kwargs = mailbox.open_thread.call_args[1]
        assert "M0安全基础" in call_kwargs["subject"]
        assert call_kwargs["sender"] == "LINGCLAUDE"

    def test_notify_completion_without_mailbox(self):
        engine = QueryEngine()
        engine.notify_completion("test", "test body")

    def test_notify_risk_with_mailbox(self):
        engine = QueryEngine()
        mailbox = MagicMock()
        engine.init_mailbox(mailbox)

        engine.notify_risk("硬中断触发", "连续失败3次", severity="critical")
        mailbox.open_thread.assert_called_once()
        call_kwargs = mailbox.open_thread.call_args[1]
        assert "CRITICAL" in call_kwargs["subject"]
        assert "硬中断触发" in call_kwargs["topic"]

    def test_notify_risk_without_mailbox(self):
        engine = QueryEngine()
        engine.notify_risk("test", "test details")

    def test_notify_vote_with_mailbox(self):
        engine = QueryEngine()
        mailbox = MagicMock()
        engine.init_mailbox(mailbox)

        engine.notify_vote("GPU调度策略", ["方案A: 固定窗口", "方案B: 动态优先级"], deadline_hours=24)
        mailbox.open_thread.assert_called_once()
        call_kwargs = mailbox.open_thread.call_args[1]
        assert "投票" in call_kwargs["subject"]
        body = call_kwargs["body"]
        assert "方案A" in body
        assert "24" in body

    def test_notify_vote_without_mailbox(self):
        engine = QueryEngine()
        engine.notify_vote("test", ["a", "b"])

    def test_notify_completion_mailbox_error_handled(self):
        engine = QueryEngine()
        mailbox = MagicMock()
        mailbox.open_thread.side_effect = ConnectionError("test error")
        engine.init_mailbox(mailbox)

        engine.notify_completion("test", "body")

    def test_hard_interrupt_triggers_risk_notification(self):
        engine = QueryEngine()
        mailbox = MagicMock()
        engine.init_mailbox(mailbox)

        provider = MagicMock()
        provider.complete.return_value = MagicMock(
            is_error=True,
            error="model failure",
        )
        engine._provider = provider

        output = engine._call_model("test prompt")
        assert "[硬中断]" in output

        engine.submit("trigger hard interrupt")
        mailbox.open_thread.assert_called()
        call_kwargs = mailbox.open_thread.call_args[1]
        assert "风险预警" in call_kwargs["topic"]
