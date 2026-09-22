# tests/test_rollout_rebuild.py
"""C 路线二期验收（2026-09-23）：快照派生化——事件流为真理、JSON 为缓存。

覆盖：
1. serialize_checkpoint_messages：脱敏（A1）+ to_dict 结构
2. inflight 主文件分支事件内嵌消息；带 tag 分支不内嵌（体积控制）
3. rebuild_checkpoint_from_events：从事件流重建 CheckpointData
4. checkpoint_clear 作废此前快照（正常收尾不得误复活）
5. load_checkpoint fallback：JSON 损坏/缺失时从事件流重建
6. 无事件时 fallback 返回 None（维持原「无 checkpoint」语义）
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingclaude.core.rollout import RolloutRecorder
from lingclaude.core.session_store import (
    CheckpointData,
    SessionStore,
    serialize_checkpoint_messages,
)


# ── 轻量 fake 体系 ──


class _Msg:
    def __init__(self, role: str, content: str):
        self._role = role
        self._content = content

    def to_dict(self):
        return {"role": self._role, "content": self._content}


class _FakeEngine:
    def __init__(self, session_id: str, tmp: Path | None = None):
        self.session_id = session_id
        self.git_branch = ""
        self._rollout_dir = tmp
        self._conversation: list[tuple[str, str]] = []


@pytest.fixture(autouse=True)
def _iso_rollout_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """全部测试统一把 ROLLOUT_DIR 指向 tmp（rebuild 读模块级常量，须同步 patch）。"""
    import lingclaude.core.rollout as ro_mod

    monkeypatch.setattr(ro_mod, "ROLLOUT_DIR", tmp_path / "rollouts")


def _make_store(tmp_path: Path, session_id: str = "s-rb") -> SessionStore:
    return SessionStore(
        session_manager=None,  # type: ignore[arg-type]
        session_id=session_id,
        checkpoint_dir=tmp_path / "checkpoints",
    )


def _recorder(tmp_path: Path, session_id: str) -> RolloutRecorder:
    rr = RolloutRecorder(session_id=session_id)
    rr.open_meta(branch="")
    return rr


# ── 1. 序列化唯一实现点 ──


def test_serialize_redacts_secret():
    secret = chr(115) + "k-" + "abcdefghij1234567890XY"
    msgs = [_Msg("user", f"my key {secret} please")]
    out = serialize_checkpoint_messages(msgs)
    assert secret not in out[0]["content"]
    assert "[REDACTED]" in out[0]["content"]
    assert out[0]["role"] == "user"


# ── 2. 事件内嵌策略 ──


def test_submission_inflight_event_embeds_messages(tmp_path, monkeypatch):
    """tag=None（inflight 主文件分支）→ 事件内嵌 messages。"""
    from lingclaude.core import submission as sub_mod
    from lingclaude.core.session_persist import SessionPersister
    import lingclaude.core.rollout as ro_mod

    eng = _PersisterEngine("s-embed", tmp_path)
    eng._session_persister = SessionPersister(eng)
    monkeypatch.setattr(ro_mod, "ROLLOUT_DIR", tmp_path / "rollouts")

    sub_mod.SubmissionMixin._save_checkpoint(
        eng,  # type: ignore[arg-type]
        messages=[_Msg("user", "hello"), _Msg("assistant", "hi")],
        round_idx=-1,
        prompt="hello",
        used_tools=False,
        total_input=10,
        total_output=5,
        tag=None,
    )
    files = sorted((tmp_path / "rollouts").glob("rollout-*.jsonl"))
    assert files, "inflight checkpoint 必须落 rollout 事件"
    events = RolloutRecorder.read_all(files[-1])
    cp = [e for e in events if e.get("event") == "checkpoint"]
    assert cp and "messages" in cp[-1]
    assert cp[-1]["tag"] is None
    assert len(cp[-1]["messages"]) == 2
    assert cp[-1]["snapshot_prompt"] == "hello"


def test_submission_tagged_event_not_embedded(tmp_path, monkeypatch):
    """带 tag 的历史版本 → 不内嵌消息（JSONL 体积控制）。"""
    from lingclaude.core import submission as sub_mod
    from lingclaude.core.session_persist import SessionPersister
    import lingclaude.core.rollout as ro_mod

    eng = _PersisterEngine("s-embed2", tmp_path)
    eng._session_persister = SessionPersister(eng)
    monkeypatch.setattr(ro_mod, "ROLLOUT_DIR", tmp_path / "rollouts")

    sub_mod.SubmissionMixin._save_checkpoint(
        eng,  # type: ignore[arg-type]
        messages=[_Msg("user", "x")],
        round_idx=3,
        prompt="x",
        used_tools=False,
        total_input=1,
        total_output=1,
        tag="round3",
    )
    files = sorted((tmp_path / "rollouts").glob("rollout-*.jsonl"))
    events = RolloutRecorder.read_all(files[-1])
    cp = [e for e in events if e.get("event") == "checkpoint"]
    assert cp and "messages" not in cp[-1]


# ── 3. 重建 ──


def test_rebuild_returns_latest_embedded_snapshot(tmp_path):
    sid = "s-rebuild"
    rr = _recorder(tmp_path, sid)
    rr.record("checkpoint", {
        "tag": None, "round_idx": -1, "used_tools": False,
        "total_input": 1, "total_output": 1, "message_count": 1,
        "messages": [{"role": "user", "content": "v1"}],
        "snapshot_prompt": "v1", "snapshot_conversation": [],
    })
    rr.record("checkpoint", {
        "tag": None, "round_idx": 2, "used_tools": True,
        "total_input": 9, "total_output": 8, "message_count": 2,
        "messages": [
            {"role": "user", "content": "v1"},
            {"role": "assistant", "content": "v2"},
        ],
        "snapshot_prompt": "v1",
        "snapshot_conversation": [["user", "v1"], ["assistant", "v2"]],
    })
    store = _make_store(tmp_path, sid)
    cd = store.rebuild_checkpoint_from_events()
    assert cd is not None
    assert cd.round_idx == 2
    assert len(cd.raw_messages) == 2
    assert cd.raw_messages[-1]["content"] == "v2"
    assert cd.saved_conversation == [["user", "v1"], ["assistant", "v2"]]


def test_rebuild_ignores_tagged_events(tmp_path):
    sid = "s-tagged"
    rr = _recorder(tmp_path, sid)
    rr.record("checkpoint", {
        "tag": "round1", "round_idx": 1, "used_tools": False,
        "total_input": 1, "total_output": 1, "message_count": 1,
        "messages": [{"role": "user", "content": "tagged"}],
    })
    store = _make_store(tmp_path, sid)
    assert store.rebuild_checkpoint_from_events() is None


def test_rebuild_none_when_no_events(tmp_path):
    _recorder(tmp_path, "s-empty")
    store = _make_store(tmp_path, "s-empty")
    assert store.rebuild_checkpoint_from_events() is None


def test_rebuild_none_when_no_rollout_dir(tmp_path):
    store = _make_store(tmp_path, "s-nodir")
    assert store.rebuild_checkpoint_from_events() is None


# ── 4. clear 作废 ──


def test_clear_invalidates_earlier_snapshots(tmp_path):
    sid = "s-clear"
    rr = _recorder(tmp_path, sid)
    rr.record("checkpoint", {
        "tag": None, "round_idx": -1, "used_tools": False,
        "total_input": 1, "total_output": 1, "message_count": 1,
        "messages": [{"role": "user", "content": "stale"}],
        "snapshot_prompt": "stale", "snapshot_conversation": [],
    })
    rr.record("checkpoint_clear", {})
    store = _make_store(tmp_path, sid)
    assert store.rebuild_checkpoint_from_events() is None


def test_clear_then_new_snapshot_recovers_latest(tmp_path):
    sid = "s-cycle"
    rr = _recorder(tmp_path, sid)
    rr.record("checkpoint", {
        "tag": None, "round_idx": -1, "used_tools": False,
        "total_input": 1, "total_output": 1, "message_count": 1,
        "messages": [{"role": "user", "content": "old"}],
        "snapshot_prompt": "old", "snapshot_conversation": [],
    })
    rr.record("checkpoint_clear", {})
    rr.record("checkpoint", {
        "tag": None, "round_idx": 5, "used_tools": True,
        "total_input": 3, "total_output": 4, "message_count": 1,
        "messages": [{"role": "user", "content": "new"}],
        "snapshot_prompt": "new", "snapshot_conversation": [["user", "new"]],
    })
    store = _make_store(tmp_path, sid)
    cd = store.rebuild_checkpoint_from_events()
    assert cd is not None and cd.round_idx == 5
    assert cd.raw_messages[0]["content"] == "new"


# ── 5. load_checkpoint fallback ──


class _PersisterEngine(_FakeEngine):
    def __init__(self, session_id: str, tmp: Path):
        super().__init__(session_id, tmp)
        self.session_store = SessionStore(
            session_manager=None,  # type: ignore[arg-type]
            session_id=session_id,
            checkpoint_dir=tmp / "checkpoints",
        )

    def _sync_session_store(self) -> None:
        self.session_store.session_id = self.session_id


def test_load_checkpoint_falls_back_to_events(tmp_path):
    from lingclaude.core.session_persist import SessionPersister

    sid = "s-fallback"
    _recorder(tmp_path, sid).record("checkpoint", {
        "tag": None, "round_idx": 7, "used_tools": False,
        "total_input": 2, "total_output": 3, "message_count": 1,
        "messages": [{"role": "user", "content": "from-events"}],
        "snapshot_prompt": "from-events",
        "snapshot_conversation": [["user", "from-events"]],
    })
    eng = _PersisterEngine(sid, tmp_path)  # 无任何 checkpoint JSON
    got = SessionPersister(eng).load_checkpoint()
    assert got is not None
    assert got["round_idx"] == 7
    assert got["messages"][0]["content"] == "from-events"


def test_load_checkpoint_prefers_json_over_events(tmp_path):
    from lingclaude.core.session_persist import SessionPersister

    sid = "s-json-first"
    rr = _recorder(tmp_path, sid)
    rr.record("checkpoint", {
        "tag": None, "round_idx": 1, "used_tools": False,
        "total_input": 1, "total_output": 1, "message_count": 1,
        "messages": [{"role": "user", "content": "from-events"}],
        "snapshot_prompt": "from-events", "snapshot_conversation": [],
    })
    eng = _PersisterEngine(sid, tmp_path)
    # 存一份「更新」的 JSON 快照
    eng.session_store.save_checkpoint(
        messages=[_Msg("user", "from-json")],
        round_idx=9, prompt="from-json", used_tools=False,
        total_input=1, total_output=1,
        conversation=[],
        tag=None,
    )
    got = SessionPersister(eng).load_checkpoint()
    assert got is not None and got["round_idx"] == 9


def test_load_checkpoint_none_when_nothing_available(tmp_path):
    from lingclaude.core.session_persist import SessionPersister

    sid = "s-nothing"
    _recorder(tmp_path, sid)  # 只有 meta，无 checkpoint 事件
    eng = _PersisterEngine(sid, tmp_path)
    assert SessionPersister(eng).load_checkpoint() is None
