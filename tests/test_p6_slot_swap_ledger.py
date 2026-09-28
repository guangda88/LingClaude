"""P6 红→绿验收：slot_swap record 入 2T3A + 热更台账 query。

对应主方案 P6 验收：query(type=slot_swap) 返回热更历史。

- 改动前红：旧 _record_swap 用不存在的 lingclaude.lingmemory.get_memory + 错误 type，
  永远走降级分支；registry 无 slot_swap type。
- 改动后绿：swap 后经 LingMemory.query(type="slot_swap") 可持久查询（跨实例/重启）。
"""

from __future__ import annotations

import pytest

from lingclaude.core.slot import SlotManager, SwapRecord


@pytest.fixture()
def tmp_memory_db(tmp_path, monkeypatch):
    """隔离 2T3A：LingMemory 指向临时 db，不污染真库。"""
    from lingmemory.core import LingMemory

    monkeypatch.setattr(
        LingMemory, "__init__", lambda self, db_path=None: _orig_init(self, tmp_path / "mem.db")
    )


def _orig_init(self, db_path):
    import sqlite3
    from pathlib import Path

    from lingmemory.core import TypeRegistry

    self.db_path = Path(db_path)
    self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
    self.conn.row_factory = sqlite3.Row
    self.registry = TypeRegistry()
    self._fts = None
    self._events = None
    self.conn.execute(
        "CREATE TABLE IF NOT EXISTS records ("
        "id TEXT PRIMARY KEY, type TEXT, state TEXT, data TEXT,"
        " parent_id TEXT, created_by TEXT, created_at TEXT, updated_at TEXT)"
    )
    self.conn.commit()


class TestSlotSwapLedger:
    def test_swap_writes_queryable_slot_swap_record(self, tmp_path, monkeypatch) -> None:
        """swap 后 LingMemory.query(type='slot_swap') 返回该条热更历史。"""
        import sqlite3
        from pathlib import Path

        import lingmemory.core as lm_core
        from lingmemory.core import LingMemory, TypeRegistry

        def patched_init(self, db_path=None):
            self.db_path = Path(tmp_path / "mem.db")
            self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.registry = TypeRegistry()  # 真 registry（含新注册的 slot_swap）
            self._fts = None
            self._events = None
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS records ("
                "id TEXT PRIMARY KEY, type TEXT, state TEXT, data TEXT,"
                " parent_id TEXT, created_by TEXT, created_at TEXT, updated_at TEXT)"
            )
            self.conn.commit()

        monkeypatch.setattr(LingMemory, "__init__", patched_init)

        mgr = SlotManager()
        mgr.register("model_provider", initial=lambda: "v1")
        mgr.rebuild("model_provider", lambda: "v2", reason="config")

        # 持久 query（新实例 = 跨进程语义）
        result = LingMemory().query(type="slot_swap", data_filter={"slot": "model_provider"})
        items = result["items"]
        assert len(items) == 1, f"query(type=slot_swap) 应返回 1 条，实际 {items}"
        data = items[0]["data"]
        assert data["slot"] == "model_provider"
        assert data["epoch"] == 1
        assert data["reason"] == "config"
        assert data["type"] == "slot_swap"

    def test_query_swap_history_persistent(self, tmp_path, monkeypatch) -> None:
        """SlotManager.query_swap_history：2T3A 可查时返回持久记录。"""
        import sqlite3
        from pathlib import Path

        from lingmemory.core import LingMemory, TypeRegistry

        def patched_init(self, db_path=None):
            self.db_path = Path(tmp_path / "mem.db")
            self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.registry = TypeRegistry()
            self._fts = None
            self._events = None
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS records ("
                "id TEXT PRIMARY KEY, type TEXT, state TEXT, data TEXT,"
                " parent_id TEXT, created_by TEXT, created_at TEXT, updated_at TEXT)"
            )
            self.conn.commit()

        monkeypatch.setattr(LingMemory, "__init__", patched_init)

        mgr = SlotManager()
        mgr.register("todo_store", initial=lambda: "a")
        mgr.rebuild("todo_store", lambda: "b", reason="config")
        mgr.rebuild("todo_store", lambda: "c", reason="manual")

        hist = mgr.query_swap_history("todo_store")
        assert len(hist) == 2
        assert {h["reason"] for h in hist} == {"config", "manual"}
        assert all(h["type"] == "slot_swap" for h in hist)

    def test_query_swap_history_degrades_to_inprocess(self, monkeypatch) -> None:
        """lingmemory 缺席时降级进程内台账（L2 纪律：不炸）。"""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "lingmemory":
                raise ImportError("mocked absent")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        mgr = SlotManager()
        mgr.register("session_runtime", initial=lambda: 1)
        mgr.rebuild("session_runtime", lambda: 2, reason="manual")

        hist = mgr.query_swap_history()
        assert len(hist) == 1
        assert hist[0]["slot"] == "session_runtime"
        assert hist[0]["type"] == "slot_swap"
