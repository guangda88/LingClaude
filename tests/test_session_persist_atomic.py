"""会话持久化加固测试：原子写 + 空会话防误写（7c643abc 0 字节事故回归）。"""
from __future__ import annotations

import json
from pathlib import Path

from lingclaude.core.models import UsageSummary
from lingclaude.core.session import SessionManager
from lingclaude.core.session_persist import SessionPersister


class _StubEngine:
    """最小 engine 鸭子类型——SessionPersister 只摸这几个属性。"""

    def __init__(self, tmp_path: Path, messages: list[str], session_id: str = "abc123") -> None:
        self.session_manager = SessionManager(save_dir=tmp_path / "sessions")
        self.session_id = session_id
        self._messages = messages
        self._usage = UsageSummary()
        self._transcript = list(messages)


class TestAtomicSave:
    def test_save_writes_valid_json_no_tmp_left(self, tmp_path: Path) -> None:
        sm = SessionManager(save_dir=tmp_path / "sessions")
        session = sm.create(messages=("q", "a"))
        result = sm.save(session)
        assert result.is_ok
        path = result.data
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["messages"] == ["q", "a"]
        assert not list(path.parent.glob("*.tmp"))

    def test_overwrite_existing_keeps_valid(self, tmp_path: Path) -> None:
        sm = SessionManager(save_dir=tmp_path / "sessions")
        s1 = sm.create(messages=("q1", "a1"))
        sm.save(s1)
        import dataclasses

        s2 = sm.create(messages=("q2", "a2"))
        s2 = dataclasses.replace(s2, session_id=s1.session_id)
        sm.save(s2)
        path = tmp_path / "sessions" / f"{s1.session_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["messages"] == ["q2", "a2"]


class TestEmptySessionSkip:
    def test_empty_messages_never_overwrite_archive(self, tmp_path: Path) -> None:
        """load 后没对话就退出 → 空消息不得覆盖既有存档（0 字节事故回归）。"""
        sm = SessionManager(save_dir=tmp_path / "sessions")
        archive = tmp_path / "sessions" / "abc123.json"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(json.dumps({
            "session_id": "abc123",
            "messages": ["历史q", "历史a"],
            "input_tokens": 10,
            "output_tokens": 20,
            "created_at": "2026-09-06",
            "project_path": "",
            "project_name": "",
        }), encoding="utf-8")

        engine = _StubEngine(tmp_path, messages=[])
        sp = SessionPersister(engine)
        result = sp.persist_session()
        assert result.is_error
        assert "跳过" in result.error
        # 存档原封未动
        data = json.loads(archive.read_text(encoding="utf-8"))
        assert data["messages"] == ["历史q", "历史a"]

    def test_nonempty_messages_still_saves(self, tmp_path: Path) -> None:
        engine = _StubEngine(tmp_path, messages=["q", "a"])
        sp = SessionPersister(engine)
        result = sp.persist_session()
        assert result.is_ok

    def test_state_store_seam_save_load(self, tmp_path: Path) -> None:
        """J4 接缝：session save 走 StateStore（record_type=session），跨实例可读。"""
        from lingclaude.core.state_store import JsonFileBackend, StateStore

        save_dir = tmp_path / "sessions"
        store = StateStore(root=save_dir)
        sm = SessionManager(save_dir=save_dir, state_store=store)

        sess = sm.create(messages=("hello", "world"), input_tokens=3, output_tokens=5)
        result = sm.save(sess)
        assert result.is_ok

        # StateStore 落盘（record_type="session", key=session_id）
        backend = JsonFileBackend(root=save_dir)
        data = backend.load("session", sess.session_id, save_dir)
        assert data is not None
        assert data["messages"] == ["hello", "world"]  # JSON 无 tuple，落盘为 list

        # 跨实例：从 StateStore 恢复
        sm2 = SessionManager(save_dir=save_dir, state_store=store)
        loaded = sm2.load(sess.session_id)
        assert loaded.is_ok
        assert loaded.data.session_id == sess.session_id
        assert loaded.data.messages == ("hello", "world")

    def test_state_store_seam_fallback_to_file(self, tmp_path: Path) -> None:
        """J4 接缝：StateStore 不可用时，文件仓库仍可用（导出视图兜底）。"""
        class _BrokenStore:
            def save(self, *a, **k):
                raise RuntimeError("store down")

        save_dir = tmp_path / "sessions"
        sm = SessionManager(save_dir=save_dir, state_store=_BrokenStore())
        sess = sm.create(messages=("a", "b"), input_tokens=1, output_tokens=1)
        result = sm.save(sess)
        # 文件导出视图仍写成功
        assert result.is_ok
        assert (save_dir / f"{sess.session_id}.json").exists()
        # 跨实例可读（走文件）
        sm2 = SessionManager(save_dir=save_dir, state_store=_BrokenStore())
        loaded = sm2.load(sess.session_id)
        assert loaded.is_ok
        assert loaded.data.messages == ("a", "b")
