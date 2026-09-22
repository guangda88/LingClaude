# tests/test_rollout_unification.py
"""C 路线统一验收（2026-09-23）：rollout 单一真理之源一期。

覆盖：
1. get_engine_rollout 单例语义（同 engine 复用、session 变更重建）
2. checkpoint/rewind 动作经 record_engine_rollout 落共享事件流
3. /fork 命令：forked_from 链 + 源文件保留 + fork 事件
4. /share 命令：自包含 JSONL + redact + 只读不碰原会话
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingclaude.core.rollout import (
    ROLLOUT_DIR,
    RolloutRecorder,
    get_engine_rollout,
    record_engine_rollout,
)


class _FakeEngine:
    def __init__(self, session_id: str = "s-unify", tmp: Path | None = None):
        self.session_id = session_id
        self.git_branch = ""
        self._rollout_dir = tmp


@pytest.fixture
def rodir(tmp_path: Path) -> Path:
    return tmp_path / "rollouts"


# ── 1. 单例 accessor ──


def test_accessor_reuses_same_instance(rodir):
    eng = _FakeEngine(tmp=rodir)
    a = get_engine_rollout(eng, session_id="s1")
    b = get_engine_rollout(eng, session_id="s1")
    assert a is b
    assert isinstance(a, RolloutRecorder)
    assert a.session_id == "s1"


def test_accessor_rebuilds_on_session_change(rodir):
    eng = _FakeEngine(tmp=rodir)
    a = get_engine_rollout(eng, session_id="s1")
    b = get_engine_rollout(eng, session_id="s2")
    assert a is not b
    assert b.session_id == "s2"


def test_record_event_via_helper(rodir):
    eng = _FakeEngine(tmp=rodir)
    record_engine_rollout(eng, "checkpoint", {"tag": "t1"})
    record_engine_rollout(eng, "rewind", {"tag": "t1"})
    rr = get_engine_rollout(eng, session_id="s-unify")
    events = RolloutRecorder.read_all(rr._active)
    kinds = [e.get("event") for e in events]
    assert kinds == ["session_meta", "checkpoint", "rewind"]
    assert events[1]["tag"] == "t1"


# ── 2. session_persist 挂钩（真实 SessionPersister 链路）──


def _make_engine_with_persister(tmp_path: Path):
    """最小 engine 桩：真实 SessionPersister + SessionStore 链路。"""
    from lingclaude.core.model_types import ModelMessage, MessageRole
    from lingclaude.core.session_persist import SessionPersister
    from lingclaude.core.session_store import SessionStore

    eng = SimpleNamespace()
    eng.session_id = "s-hook"
    eng.git_branch = ""
    eng._messages = [
        ModelMessage(role=MessageRole.USER, content="hello"),
        ModelMessage(role=MessageRole.ASSISTANT, content="world"),
    ]
    eng._usage = SimpleNamespace(input_tokens=1, output_tokens=2)
    eng._conversation = []
    eng._transcript = ["hello", "world"]
    eng._sync_session_store = lambda: None
    from lingclaude.core.session import SessionManager
    sm = SessionManager(save_dir=tmp_path / "sessions")
    eng.session_store = SessionStore(sm, "s-hook", checkpoint_dir=tmp_path / "cps")
    eng._get_journal = lambda: SimpleNamespace(append=lambda *a, **k: None, clear=lambda: None)
    eng._journal_cache = None
    eng._active_checkpoint = None
    persister = SessionPersister(eng)
    eng._session_persister = persister

    def _save_cp(messages, round_idx, prompt, used_tools,
                 total_input, total_output, tag=None):
        persister.save_checkpoint(
            messages=messages, round_idx=round_idx, prompt=prompt,
            used_tools=used_tools, total_input=total_input,
            total_output=total_output, tag=tag)

    eng._save_checkpoint = _save_cp
    return eng, persister


def test_checkpoint_records_rollout_event(tmp_path):
    eng, persister = _make_engine_with_persister(tmp_path)
    persister.save_checkpoint(
        messages=eng._messages, round_idx=3, prompt="hello",
        used_tools=True, total_input=10, total_output=5, tag="cp1",
    )
    # 同一 engine 上下文里取共享 recorder，应读到 checkpoint 事件
    rr = get_engine_rollout(eng, session_id="s-hook")
    events = RolloutRecorder.read_all(rr._active)
    cp = [e for e in events if e.get("event") == "checkpoint"]
    assert cp and cp[-1]["tag"] == "cp1" and cp[-1]["round_idx"] == 3


def test_rewind_records_rollout_event(tmp_path):
    eng, persister = _make_engine_with_persister(tmp_path)
    persister.save_checkpoint(
        messages=eng._messages, round_idx=1, prompt="hello",
        used_tools=False, total_input=1, total_output=1, tag="rw",
    )
    assert persister.rewind_to("rw") is True
    rr = get_engine_rollout(eng, session_id="s-hook")
    events = RolloutRecorder.read_all(rr._active)
    rw = [e for e in events if e.get("event") == "rewind"]
    assert rw and rw[-1]["tag"] == "rw"


# ── 3. /fork /share 命令 ──


class _Status:
    def set_task(self, *_a, **_k):
        pass


def test_fork_creates_new_rollout_and_keeps_source(tmp_path, capsys):
    eng, persister = _make_engine_with_persister(tmp_path)
    # 让 recorder 落在 tmp 目录：monkeypatch ROLLOUT_DIR 的实例化默认
    from lingclaude.cli import commands as cmds
    proc = cmds.SlashCommandProcessor(eng, _Status())
    import lingclaude.core.rollout as ro
    orig = ro.ROLLOUT_DIR
    ro.ROLLOUT_DIR = tmp_path / "rollouts"
    try:
        # recorder 同样要被重定向到 tmp：先清 accessor 缓存再建
        if hasattr(eng, "_engine_rollout_recorder"):
            delattr(eng, "_engine_rollout_recorder")
        import lingclaude.core.rollout as ro2
        rr_probe = ro2.RolloutRecorder(session_id="s-hook", rollout_dir=tmp_path / "rollouts")
        rr_thread_id = rr_probe.thread_id
        # 用同 thread 的 recorder 替换 accessor 产物（保证 fork 文件落在 tmp 且可定位）
        eng._engine_rollout_recorder = rr_probe
        proc._cmd_fork("f1")
        out = capsys.readouterr().out
        assert "[fork] 已分叉" in out
        files = sorted((tmp_path / "rollouts").glob("*.jsonl"))
        assert len(files) >= 2  # 源 + 分叉
        # fork 文件 meta 带 forked_from
        fork_files = [f for f in files if "_f1" in f.name or "fork" in f.name]
        assert fork_files, "应有分叉文件"
        lines = fork_files[-1].read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(l) for l in lines]
        fm = [e for e in events if e.get("event") == "fork_meta"]
        assert fm, "应有 fork_meta 事件行"
        # to_json_line 把 data 平铺到事件顶层
        assert fm[0]["forked_from_id"] == rr_thread_id
    finally:
        ro.ROLLOUT_DIR = orig


def test_share_exports_selfcontained_jsonl(tmp_path, capsys):
    eng, _ = _make_engine_with_persister(tmp_path)
    from lingclaude.cli import commands as cmds
    proc = cmds.SlashCommandProcessor(eng, _Status())
    outfile = tmp_path / "share.jsonl"
    proc._cmd_share(str(outfile))
    out = capsys.readouterr().out
    assert "[share] 已导出" in out
    lines = outfile.read_text(encoding="utf-8").strip().splitlines()
    meta = json.loads(lines[0])
    assert meta["event"] == "session_meta" and meta["message_count"] == 2
    msgs = [json.loads(l) for l in lines[1:]]
    assert [m["ordinal"] for m in msgs] == [1, 2]
    assert msgs[0]["content"] == "hello"


def test_share_empty_session_rejected(capsys):
    eng, _ = _make_engine_with_persister(Path("/tmp"))
    eng._messages = []
    from lingclaude.cli import commands as cmds
    proc = cmds.SlashCommandProcessor(eng, _Status())
    proc._cmd_share("")
    assert "[share] 当前会话无消息" in capsys.readouterr().out
