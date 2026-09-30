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
        self._conversation: list[tuple[str, str]] = []


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


# ---------------------------------------------------------------------------
# 会话恢复角色配对回归（2026-09-29）
# ---------------------------------------------------------------------------
# 根因：压缩摘要落盘为首条，破坏严格成对假设 → 消息错位 + 尾条丢弃。
# 修复：首条摘要 → system；剩余成对；奇数尾条 → assistant（不丢弃）。
# ---------------------------------------------------------------------------


class TestConversationRoleRecovery:
    """load_session 后 _conversation 角色配对正确性。"""

    def test_odd_length_with_summary_first(self, tmp_path: Path) -> None:
        """奇数长度 + 首条摘要：首条 system，剩余成对，尾条不丢。"""
        messages = [
            "## 压缩摘要（前 5 轮对话）\n\n...",
            "用户问题 A",
            "助手回复 A",
            "用户问题 B",
            "助手回复 B",
            "用户问题 C",
        ]
        engine = _StubEngine(tmp_path, messages=messages)
        sp = SessionPersister(engine)
        sp.persist_session()  # 先落盘
        # 新 engine 重新加载
        engine2 = _StubEngine(tmp_path, messages=[], session_id="abc123")
        sp2 = SessionPersister(engine2)
        ok = sp2.load_session("abc123")
        assert ok
        conv = engine2._conversation
        assert conv[0] == ("system", messages[0])
        assert conv[1] == ("user", messages[1])
        assert conv[2] == ("assistant", messages[2])
        assert conv[3] == ("user", messages[3])
        assert conv[4] == ("assistant", messages[4])
        assert conv[5] == ("user", messages[5])
        assert conv[6] == ("assistant", "")  # 奇数尾条补空，不丢弃
        assert len(conv) == 7

    def test_odd_length_no_summary(self, tmp_path: Path) -> None:
        """奇数长度无摘要：首条 user，剩余成对，尾条补空。"""
        messages = [
            "用户问题 A",
            "助手回复 A",
            "用户问题 B",
        ]
        engine = _StubEngine(tmp_path, messages=messages)
        sp = SessionPersister(engine)
        sp.persist_session()  # 先落盘
        engine2 = _StubEngine(tmp_path, messages=[], session_id="abc123")
        sp2 = SessionPersister(engine2)
        ok = sp2.load_session("abc123")
        assert ok
        conv = engine2._conversation
        assert conv[0] == ("user", messages[0])
        assert conv[1] == ("assistant", messages[1])
        assert conv[2] == ("user", messages[2])
        assert conv[3] == ("assistant", "")  # 奇数尾条补空
        assert len(conv) == 4

    def test_even_length_preserved(self, tmp_path: Path) -> None:
        """偶数长度：严格成对，无补空。"""
        messages = [
            "## 压缩摘要（前 2 轮对话）\n\n...",
            "用户 A",
            "助手 A",
            "用户 B",
            "助手 B",
        ]
        engine = _StubEngine(tmp_path, messages=messages)
        sp = SessionPersister(engine)
        sp.persist_session()  # 先落盘
        engine2 = _StubEngine(tmp_path, messages=[], session_id="abc123")
        sp2 = SessionPersister(engine2)
        ok = sp2.load_session("abc123")
        assert ok
        conv = engine2._conversation
        assert conv[0] == ("system", messages[0])
        assert conv[1] == ("user", messages[1])
        assert conv[2] == ("assistant", messages[2])
        assert conv[3] == ("user", messages[3])
        assert conv[4] == ("assistant", messages[4])
        assert len(conv) == 5

    def test_empty_tail_dropped_before(self, tmp_path: Path) -> None:
        """旧版丢弃行为已修复：尾条 assistant 补空而非丢弃。"""
        # 22 条场景模拟（摘要 + 10 对 + 1 奇数尾条）
        messages = ["## 压缩摘要（前 10 轮对话）\n\n..."]
        for i in range(10):
            messages.append(f"用户 {i}")
            messages.append(f"助手 {i}")
        messages.append("用户 10（奇数尾条）")
        assert len(messages) == 22  # 1 + 10*2 + 1

        engine = _StubEngine(tmp_path, messages=messages)
        sp = SessionPersister(engine)
        sp.persist_session()  # 先落盘
        engine2 = _StubEngine(tmp_path, messages=[], session_id="abc123")
        sp2 = SessionPersister(engine2)
        ok = sp2.load_session("abc123")
        assert ok
        conv = engine2._conversation
        # 旧版：range(0, 21, 2) 丢弃 msgs[21]
        # 新版：尾条补空，不丢
        assert conv[-1] == ("assistant", "")  # 奇数尾条补空
        assert conv[-2] == ("user", messages[21])  # 最后一条 user 保留
