"""B: 跨端任务续接语义 e2e（2026-10-02）。

覆盖面：
  1. GET /sessions/{id}/task_state 任务状态投影（404 / 字段面 / mtime 乐观锁）
  2. POST /ask/stream + session_id：未知会话 fail-fast 404（provider 调用前拦截，
     测试不触真实模型）
  3. 契约：AskRequest.session_id 缺省 = 原一次性语义（零破坏回归锚）
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pytest


@pytest.mark.usefixtures("api_client")
class TestTaskStateProjection:
    def test_404_for_missing_session(self, api_client, api_key):
        r = api_client.get(
            "/sessions/no-such-session/task_state",
            headers={"X-API-Key": api_key},
        )
        assert r.status_code == 404

    def test_projection_fields(self, api_client, api_key, monkeypatch, tmp_path):
        from lingclaude.core.session import Session, SessionManager
        from lingclaude.core import session as _session_mod

        fake_dir = tmp_path / "sess"
        fake_dir.mkdir()
        monkeypatch.setattr(_session_mod, "_global_sessions_root", lambda: fake_dir)
        mgr = SessionManager()
        sess = Session(
            session_id="task-state-1",
            messages=("user: 帮我重构", "assistant: 已完成，改了 3 个文件"),
            input_tokens=100,
            output_tokens=50,
        )
        assert mgr.save(sess).is_ok

        r = api_client.get(
            "/sessions/task-state-1/task_state",
            headers={"X-API-Key": api_key},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == "task-state-1"
        assert data["rounds"] == 1
        assert "已完成" in data["last_assistant_preview"]
        assert data["input_tokens"] == 100
        assert data["last_write_unix"] > 0  # 乐观锁字段（mtime 可用）


class TestAskStreamResumeContract:
    def test_404_unknown_session_fail_fast(self, api_client, api_key):
        """未知 session_id 在模型调用前 fail-fast——不产生任何 provider 流量。"""
        r = api_client.post(
            "/ask/stream",
            json={"question": "hi", "session_id": "ghost-session"},
            headers={"X-API-Key": api_key},
        )
        assert r.status_code == 404

    def test_default_session_id_empty(self):
        """缺省 session_id = ""：原一次性语义零破坏（回归锚）。"""
        from lingclaude.api import AskRequest

        req = AskRequest(question="hi")
        assert req.session_id == ""
