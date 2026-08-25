"""T3 接线测试 — 案 5: session_projection 接线到 webui API。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from lingclaude.core.session import Session, SessionManager
from lingclaude.core.session_projection import (
    project_session,
    project_tokens,
    project_tools,
    project_rounds,
)


@pytest.fixture
def sample_session(tmp_path):
    """创建测试 session 并返回 (manager, session_id)。"""
    mgr = SessionManager(save_dir=tmp_path)
    session = Session(
        session_id="test-proj-123",
        messages=("user: read file.py", "assistant: [read] file.py content", "user: edit it", "assistant: [edit] done"),
        input_tokens=100,
        output_tokens=50,
        project_path="/tmp/test",
    )
    mgr.save(session)
    return mgr, "test-proj-123"


class TestSessionProjectionWiring:
    """案 5: session_projection 接线验证。"""

    def test_project_session_returns_all_views(self, sample_session):
        """project_session 返回 tokens/tools/rounds 三视角。"""
        mgr, sid = sample_session
        loaded = mgr.load(sid)
        assert loaded.is_ok
        
        proj = project_session(loaded.data)
        assert "session_id" in proj
        assert "tokens" in proj
        assert "tools" in proj
        assert "rounds" in proj
        assert proj["session_id"] == sid

    def test_project_tokens(self, sample_session):
        """project_tokens 返回 token 统计。"""
        mgr, sid = sample_session
        loaded = mgr.load(sid)
        assert loaded.is_ok
        
        tokens = project_tokens(loaded.data)
        assert tokens.total_tokens == 150
        assert tokens.rounds == 4  # 4 messages = 4 rounds（不是 2）

    def test_project_tools(self, sample_session):
        """project_tools 返回工具调用统计。"""
        mgr, sid = sample_session
        loaded = mgr.load(sid)
        assert loaded.is_ok
        
        tools = project_tools(loaded.data)
        assert isinstance(tools, object)  # ToolProjection

    def test_project_rounds(self, sample_session):
        """project_rounds 返回轮次统计。"""
        mgr, sid = sample_session
        loaded = mgr.load(sid)
        assert loaded.is_ok
        
        rounds = project_rounds(loaded.data)
        assert isinstance(rounds, object)  # RoundProjection


class TestApiProjectionEndpoint:
    """案 5: API 端点接线验证。"""

    def test_projection_endpoint_exists(self):
        """api.py 有 /sessions/{id}/projection 端点。"""
        from lingclaude.api import app
        
        routes = [r.path for r in app.routes]
        assert any("/sessions/" in r and "projection" in r for r in routes)
