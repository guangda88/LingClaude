"""R2 denial 结构化日志测试 — flywheel + journal 接线验证。"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lingclaude.core.models import PermissionDenial
from lingclaude.core.session_journal import SessionJournal


class TestDenialLogging:
    def _make_engine(self, tmp_path: Path, session_id: str = "denial_test") -> Any:
        from lingclaude.core.query_engine import QueryEngine
        engine = QueryEngine(model_provider=None)
        engine.session_id = session_id
        engine._journal_dir = tmp_path / "journals"
        return engine

    def test_submit_logs_denial_to_journal(self, tmp_path: Path) -> None:
        """submit() 收到 denied_tools 后，journal 必须记录 permission_denial 事件。"""
        engine = self._make_engine(tmp_path)
        denial = PermissionDenial(tool_name="bash", reason="blocked by deny_prefixes")
        result = engine.submit("test", denied_tools=(denial,))
        # provider=None 走 fallback 路径，但 denial 仍应被记录
        j = SessionJournal("denial_test", journal_dir=tmp_path / "journals")
        events = j.load()
        denial_events = [e for e in events if e["type"] == "permission_denial"]
        assert len(denial_events) == 1
        assert denial_events[0]["tool_name"] == "bash"
        assert "blocked" in denial_events[0]["reason"]

    def test_submit_logs_denial_to_flywheel(self, tmp_path: Path) -> None:
        """submit() 收到 denied_tools 后，flywheel 必须收到 permission_denial。"""
        engine = self._make_engine(tmp_path)
        denial = PermissionDenial(tool_name="write", reason="requires approval")
        with patch.object(engine, "_log_to_flywheel") as mock_log:
            engine.submit("test", denied_tools=(denial,))
            mock_log.assert_called_once()
            call_kwargs = mock_log.call_args
            assert call_kwargs.kwargs.get("pattern_type") == "permission_denial" or                    call_kwargs[1].get("pattern_type") == "permission_denial" or                    (call_kwargs.args and call_kwargs.args[0] == "permission_denial")

    def test_multiple_denials_all_logged(self, tmp_path: Path) -> None:
        """多个 denial 全部记录到 journal。"""
        engine = self._make_engine(tmp_path)
        denials = (
            PermissionDenial(tool_name="bash", reason="dangerous"),
            PermissionDenial(tool_name="write", reason="no permission"),
        )
        engine.submit("test", denied_tools=denials)
        j = SessionJournal("denial_test", journal_dir=tmp_path / "journals")
        events = j.load()
        denial_events = [e for e in events if e["type"] == "permission_denial"]
        assert len(denial_events) == 2
        names = {e["tool_name"] for e in denial_events}
        assert names == {"bash", "write"}

    def test_no_denials_no_journal_entry(self, tmp_path: Path) -> None:
        """无 denial 时不产生 journal 事件。"""
        engine = self._make_engine(tmp_path)
        engine.submit("normal prompt")
        j = SessionJournal("denial_test", journal_dir=tmp_path / "journals")
        events = j.load()
        denial_events = [e for e in events if e["type"] == "permission_denial"]
        assert len(denial_events) == 0

    def test_denial_logging_does_not_block(self, tmp_path: Path) -> None:
        """denial 日志 I/O 失败不阻塞 submit 主流程。"""
        engine = self._make_engine(tmp_path)
        denial = PermissionDenial(tool_name="bash", reason="test")
        # 让 journal append 失败（指向不存在且无法创建的目录）
        engine._journal_dir = Path("/proc/nonexistent/journals")
        with patch.object(engine, "_log_to_flywheel", side_effect=RuntimeError("flywheel down")):
            result = engine.submit("test", denied_tools=(denial,))
            # submit 应正常返回，不因日志失败而崩溃
            assert result is not None
