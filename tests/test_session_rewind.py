"""P1 rewind (2026-09-12): 快照回滚测试。

覆盖:
- SessionStore 多版本 checkpoint（tag 保存 / list / load_by_tag）
- SessionPersister.rewind_to 恢复 engine 状态
- QueryEngine.list_checkpoints / rewind_to 入口
- CLI /rewind 命令分派
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.core.session_store import SessionStore, CheckpointData
from lingclaude.core.session import SessionManager


@pytest.fixture
def cp_dir(tmp_path):
    d = tmp_path / "checkpoints"
    d.mkdir()
    return d


def _mk_store(cp_dir, session_id="sess1"):
    sm = SessionManager(save_dir=cp_dir.parent)
    return SessionStore(sm, session_id, checkpoint_dir=cp_dir)


def _mk_msg(role, content, tool_call_id=None):
    from lingclaude.model.types import ModelMessage, MessageRole
    return ModelMessage(role=MessageRole(role), content=content, tool_call_id=tool_call_id)


class TestSessionStoreMultiVersion:
    def test_save_with_tag_creates_versioned_file(self, cp_dir):
        s = _mk_store(cp_dir)
        p = s.save_checkpoint(
            messages=[_mk_msg("user", "hi")],
            round_idx=0, prompt="hi", used_tools=False,
            total_input=10, total_output=5,
            conversation=[("user", "hi")], tag="round0",
        )
        assert p is not None
        assert p.name == "sess1@round0.json"
        assert (cp_dir / "sess1@round0.json").exists()

    def test_save_no_tag_keeps_latest_compat(self, cp_dir):
        s = _mk_store(cp_dir)
        p = s.save_checkpoint(
            messages=[_mk_msg("user", "hi")],
            round_idx=0, prompt="hi", used_tools=False,
            total_input=0, total_output=0,
            conversation=[], tag=None,
        )
        assert p.name == "sess1.json"

    def test_list_checkpoints_sorted_latest_first(self, cp_dir):
        s = _mk_store(cp_dir)
        for i in range(3):
            s.save_checkpoint(
                messages=[_mk_msg("user", f"m{i}")],
                round_idx=i, prompt=f"p{i}", used_tools=False,
                total_input=0, total_output=0,
                conversation=[], tag=f"round{i}",
            )
        cps = s.list_checkpoints()
        assert len(cps) == 3
        # 最新 tag 在前（round2 最新）
        assert cps[0]["tag"] == "round2"
        assert cps[0]["round_idx"] == 2
        assert cps[2]["tag"] == "round0"

    def test_load_by_tag(self, cp_dir):
        s = _mk_store(cp_dir)
        s.save_checkpoint(
            messages=[_mk_msg("user", "v1")],
            round_idx=1, prompt="p1", used_tools=False,
            total_input=0, total_output=0,
            conversation=[], tag="round1",
        )
        cd = s.load_checkpoint_by_tag("round1")
        assert cd is not None
        assert cd.round_idx == 1
        assert len(cd.raw_messages) == 1
        assert s.load_checkpoint_by_tag("nonexistent") is None

    def test_rewind_preserves_other_versions(self, cp_dir):
        s = _mk_store(cp_dir)
        s.save_checkpoint(messages=[_mk_msg("user", "a")], round_idx=0, prompt="a",
                          used_tools=False, total_input=0, total_output=0,
                          conversation=[], tag="round0")
        s.save_checkpoint(messages=[_mk_msg("user", "b")], round_idx=1, prompt="b",
                          used_tools=False, total_input=0, total_output=0,
                          conversation=[], tag="round1")
        assert len(s.list_checkpoints()) == 2
        cd = s.load_checkpoint_by_tag("round0")
        assert cd is not None
        assert len(s.list_checkpoints()) == 2  # 加载不改文件


class TestRewindEngine:
    def test_rewind_to_restores_engine_state(self, tmp_path):
        """SessionPersister.rewind_to 恢复 engine._messages/_conversation/_transcript。"""
        from lingclaude.core.session_persist import SessionPersister

        class _FakeEngine:
            def __init__(self, cp_dir):
                self.session_id = "sess1"
                self._conversation = []
                self._transcript = []
                self._messages = []
                self._active_checkpoint = None
                self._journal_cache = None
                self.session_store = _mk_store(cp_dir)
                self._session_persister = SessionPersister(self)

            def _sync_session_store(self):
                self.session_store.session_id = self.session_id

            def _get_journal(self):
                class _J:
                    def clear(self):
                        return True
                return _J()

        cp_dir = tmp_path / "checkpoints"
        cp_dir.mkdir()
        eng = _FakeEngine(cp_dir)
        sp = eng._session_persister

        # 保存两个版本（engine._conversation 会随保存写入 checkpoint）
        eng._conversation = [("user", "第一轮")]
        sp.save_checkpoint(
            [_mk_msg("user", "第一轮")], 0, "q1", False, 10, 5, tag="round0",
        )
        eng._conversation = [("user", "第二轮"), ("assistant", "回答")]
        sp.save_checkpoint(
            [_mk_msg("user", "第二轮"), _mk_msg("assistant", "回答")], 1, "q2", True, 20, 10, tag="round1",
        )

        # 回滚到 round0
        ok = sp.rewind_to("round0")
        assert ok is True
        assert len(eng._messages) == 1
        assert eng._messages[0].content == "第一轮"
        # conversation 恢复
        assert eng._conversation == [("user", "第一轮")]

    def test_rewind_to_unknown_tag_fails(self, tmp_path):
        from lingclaude.core.session_persist import SessionPersister
        cp_dir = tmp_path / "checkpoints"
        cp_dir.mkdir()

        class _FakeEngine:
            def __init__(self):
                self.session_id = "sess1"
                self._conversation = []
                self._transcript = []
                self._messages = []
                self.session_store = _mk_store(cp_dir)
                self._session_persister = SessionPersister(self)

            def _sync_session_store(self):
                self.session_store.session_id = self.session_id

        eng = _FakeEngine()
        assert eng._session_persister.rewind_to("nope") is False


class TestCliRewind:
    def test_rewind_command_dispatch(self):
        from lingclaude.cli.commands import SlashCommandProcessor

        class _FakeStatus:
            def set_task(self, *a, **k):
                pass

        class _FakeEngine:
            def __init__(self):
                self._checkpoints = [
                    {"tag": "round1", "round_idx": 1, "message_count": 5, "timestamp": "t2"},
                    {"tag": "round0", "round_idx": 0, "message_count": 2, "timestamp": "t1"},
                ]
                self.rewound = None

            def list_checkpoints(self):
                return self._checkpoints

            def rewind_to(self, tag):
                self.rewound = tag
                from lingclaude.core.types import Result
                return Result.ok(f"ok:{tag}")

        proc = SlashCommandProcessor(_FakeEngine(), _FakeStatus())
        assert proc.handle("/rewind round0") is True
        assert proc.engine.rewound == "round0"

    def test_rewind_by_index(self):
        from lingclaude.cli.commands import SlashCommandProcessor

        class _FakeStatus:
            def set_task(self, *a, **k):
                pass

        class _FakeEngine:
            def __init__(self):
                self._checkpoints = [
                    {"tag": "round1", "round_idx": 1, "message_count": 5, "timestamp": "t2"},
                    {"tag": "round0", "round_idx": 0, "message_count": 2, "timestamp": "t1"},
                ]
                self.rewound = None

            def list_checkpoints(self):
                return self._checkpoints

            def rewind_to(self, tag):
                self.rewound = tag
                from lingclaude.core.types import Result
                return Result.ok(f"ok:{tag}")

        proc = SlashCommandProcessor(_FakeEngine(), _FakeStatus())
        assert proc.handle("/rewind 1") is True
        assert proc.engine.rewound == "round0"  # 序号 1 → 第二个快照

    def test_rewind_no_arg_lists(self, capsys):
        from lingclaude.cli.commands import SlashCommandProcessor

        class _FakeStatus:
            def set_task(self, *a, **k):
                pass

        class _FakeEngine:
            def list_checkpoints(self):
                return [{"tag": "round0", "round_idx": 0, "message_count": 2, "timestamp": "t"}]

            def rewind_to(self, tag):
                raise AssertionError("不应调用")

        proc = SlashCommandProcessor(_FakeEngine(), _FakeStatus())
        assert proc.handle("/rewind") is True
        out = capsys.readouterr().out
        assert "round0" in out
