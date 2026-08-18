"""LINGKERNEL_v1 D1 - SessionStore 测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.core.session import SessionManager
from lingclaude.core.session_store import CheckpointData, SessionStore


class FakeMsg:
    def __init__(self, role="user", content="hi"):
        self.role = role
        self.content = content

    def to_dict(self):
        return {"role": self.role, "content": self.content}


def _mk_store(tmp_path, session_id="s1"):
    sm = SessionManager(save_dir=tmp_path / "sessions")
    return SessionStore(sm, session_id, checkpoint_dir=tmp_path / "cp"), sm


def test_persist_and_load_session(tmp_path):
    store, _ = _mk_store(tmp_path)
    r = store.persist(["a", "b"], input_tokens=10, output_tokens=5)
    assert r.is_ok
    loaded = store.load("s1")
    assert loaded.is_ok
    assert list(loaded.data.messages) == ["a", "b"]
    assert loaded.data.input_tokens == 10


def test_checkpoint_roundtrip(tmp_path):
    store, _ = _mk_store(tmp_path)
    msgs = [FakeMsg("user", "q"), FakeMsg("assistant", "a")]
    cp = store.save_checkpoint(
        messages=msgs, round_idx=3, prompt="q", used_tools=True,
        total_input=100, total_output=50,
        conversation=[("user", "q"), ("assistant", "a")],
    )
    assert cp is not None and cp.exists()
    data = store.load_checkpoint()
    assert data is not None
    assert data.session_id == "s1"
    assert data.round_idx == 3
    assert data.prompt == "q"
    assert data.used_tools is True
    assert data.total_input == 100
    assert data.total_output == 50
    assert data.raw_messages == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ]
    assert data.saved_conversation == [["user", "q"], ["assistant", "a"]]


def test_checkpoint_none_when_absent(tmp_path):
    store, _ = _mk_store(tmp_path)
    assert store.load_checkpoint() is None
    assert store.has_checkpoint is False


def test_checkpoint_has_flag(tmp_path):
    store, _ = _mk_store(tmp_path)
    assert store.has_checkpoint is False
    store.save_checkpoint(
        messages=[], round_idx=0, prompt="p", used_tools=False,
        total_input=0, total_output=0, conversation=[],
    )
    assert store.has_checkpoint is True


def test_checkpoint_clear(tmp_path):
    store, _ = _mk_store(tmp_path)
    store.save_checkpoint(
        messages=[], round_idx=0, prompt="p", used_tools=False,
        total_input=0, total_output=0, conversation=[],
    )
    assert store.has_checkpoint is True
    store.clear_checkpoint()
    assert store.has_checkpoint is False


def test_checkpoint_session_id_mismatch_rejected(tmp_path):
    """checkpoint 文件里 session_id 不匹配 -> 返回 None (防串会话)。"""
    store, _ = _mk_store(tmp_path, session_id="s1")
    store.save_checkpoint(
        messages=[], round_idx=0, prompt="p", used_tools=False,
        total_input=0, total_output=0, conversation=[],
    )
    # 篡改
    cp = tmp_path / "cp" / "s1.json"
    data = json.loads(cp.read_text())
    data["session_id"] = "other"
    cp.write_text(json.dumps(data))
    assert store.load_checkpoint() is None


def test_save_checkpoint_failure_returns_none(tmp_path):
    """checkpoint 目录不可写 -> 返回 None 不抛异常 (best-effort)。"""
    import os
    store, _ = _mk_store(tmp_path, session_id="s1")
    ro = tmp_path / "ro_cp"
    ro.mkdir()
    os.chmod(ro, 0o500)
    try:
        store2 = SessionStore(store._sm, "s1", checkpoint_dir=ro)
        out = store2.save_checkpoint(
            messages=[FakeMsg()], round_idx=0, prompt="p", used_tools=False,
            total_input=0, total_output=0, conversation=[],
        )
        assert out is None
    finally:
        os.chmod(ro, 0o700)


def test_checkpoint_data_to_model_messages():
    """raw_messages (带 tool_calls) -> ModelMessage 列表。"""
    raw = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant", "content": "",
            "tool_calls": [
                {"function": {"id": "tc1", "name": "read", "arguments": "{\"path\":\"x\"}"}}
            ],
        },
        {"role": "tool", "content": "result", "name": "read", "tool_call_id": "tc1"},
    ]
    cd = CheckpointData(
        session_id="s", prompt="p", round_idx=0, used_tools=True,
        total_input=0, total_output=0, raw_messages=raw, saved_conversation=[],
    )
    msgs = cd.to_model_messages()
    assert len(msgs) == 3
    assert msgs[0].role.value == "user"
    assert msgs[1].tool_calls is not None
    assert msgs[1].tool_calls[0].name == "read"
    assert msgs[2].tool_call_id == "tc1"


def test_checkpoint_json_content_structure(tmp_path):
    """落盘 JSON 含 timestamp 字段 (审计可见)。"""
    store, _ = _mk_store(tmp_path)
    store.save_checkpoint(
        messages=[], round_idx=1, prompt="p", used_tools=False,
        total_input=1, total_output=2, conversation=[],
    )
    data = json.loads((tmp_path / "cp" / "s1.json").read_text())
    assert "timestamp" in data
    assert data["round_idx"] == 1
