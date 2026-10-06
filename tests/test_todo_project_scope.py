"""todo 项目作用域隔离测试（2026-10-07 串台修复；v2 oc 式会话文件）。

根因：todos 落包仓库根 data/（全局单库）且 session_id 恒为 "default"，
A 目录创建的任务出现在 B 目录的 lc 任务面板。

修复语义（v2，2026-10-07 重构「文件即会话」）：
- 未设 LINGCLAUDE_DATA_DIR：按 CWD 哈希分容器目录（todos.<cwd_hash8>/），
  容器内 <session_id>.json 一会话一文件，同目录同容器、异目录隔离；
- 显式 LINGCLAUDE_DATA_DIR：容器 todos/（跨项目共享，会话仍分文件，
  测试/容器自管隔离）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lingclaude.engine.coding_wiring import (
    _initial_todo_store,
    _resolve_todo_project_scope,
)
from lingclaude.engine.todo import TodoItem, TodoStatus, TodoStore


def _make_ctx(cwd: Path | None = None) -> object:
    """最小装配上下文：仅需 ctx.runtime.config.session_id 属性。"""

    class _Cfg:
        session_id = "default"

    class _Rt:
        config = _Cfg()

    class _Ctx:
        runtime = _Rt()

    # CodingRuntime 装配发生在真实进程 cwd 下；测试用 monkeypatch.chdir
    # 让 Path.cwd() 返回目标目录。
    return _Ctx()


def _item(content: str) -> TodoItem:
    import time

    now = time.time()
    return TodoItem(
        id=f"t-{content}-{now}",
        content=content,
        status=TodoStatus.PENDING,
        created_at=now,
        updated_at=now,
    )


def test_scope_key_stable_within_same_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同目录重复调用 → 同一库文件名（同目录同库）。"""
    monkeypatch.chdir(tmp_path)
    k1 = _resolve_todo_project_scope()
    k2 = _resolve_todo_project_scope()
    assert k1 == k2
    assert k1.startswith("todos.") and not k1.endswith(".db")
    assert len(k1) == len("todos.") + 8


def test_scope_key_differs_across_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不同目录 → 不同库文件（异目录隔离）。

    _resolve_data_dir 打桩到 tmp_path：真实 data/ 是生产目录，未设 env 时
    哈希库会落进去（2026-10-07 实测污染 9 个 db 的教训）。
    """
    a = tmp_path / "proj-a"
    b = tmp_path / "proj-b"
    a.mkdir()
    b.mkdir()
    monkeypatch.setattr(
        "lingclaude.engine.coding_wiring._resolve_data_dir",
        lambda ctx: tmp_path / "isolated-data",
    )

    monkeypatch.chdir(a)
    ka = _resolve_todo_project_scope()
    monkeypatch.chdir(b)
    kb = _resolve_todo_project_scope()
    assert ka != kb

    # 端到端：A 目录写的任务 B 目录看不到（真实面板数据链）
    monkeypatch.chdir(a)
    ctx = _make_ctx(a)
    store_a = _initial_todo_store(ctx)  # type: ignore[arg-type]
    store_a.add(_item("A 独有任务"))

    monkeypatch.chdir(b)
    store_b = _initial_todo_store(ctx)  # type: ignore[arg-type]
    assert store_b.active_items() == []
    assert store_b.db_path != store_a.db_path


def test_env_data_dir_keeps_shared_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """显式 LINGCLAUDE_DATA_DIR → 库文件名恒 todos.db（旧共享语义不变）。"""
    data_dir = tmp_path / "shared-data"
    workdir = tmp_path / "any-dir"
    workdir.mkdir(parents=True)
    monkeypatch.setenv("LINGCLAUDE_DATA_DIR", str(data_dir))
    monkeypatch.chdir(workdir)

    ctx = _make_ctx()
    store = _initial_todo_store(ctx)  # type: ignore[arg-type]
    assert store.db_path.name == "default.json"  # <sid>.json（oc 式）
    assert store.db_path.parent == data_dir / "todos"


def test_no_env_hashes_cwd_into_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未设 env → db 名含 CWD 哈希（data_dir 打桩，不写生产 data/）。"""
    monkeypatch.delenv("LINGCLAUDE_DATA_DIR", raising=False)
    workdir = tmp_path / "proj"
    workdir.mkdir(parents=True)
    monkeypatch.chdir(workdir)
    monkeypatch.setattr(
        "lingclaude.engine.coding_wiring._resolve_data_dir",
        lambda ctx: tmp_path / "isolated-data",
    )
    ctx = _make_ctx()
    store = _initial_todo_store(ctx)  # type: ignore[arg-type]
    scope = _resolve_todo_project_scope()
    assert store.db_path.parent.name == scope  # todos.<hash8>/ 容器
    assert store.db_path.name == "default.json"


# ---------------------------------------------------------------------------
# v2（2026-10-07）：oc 式「文件即会话」——容器目录 + <sid>.json + 惰性 GC + 迁移
# ---------------------------------------------------------------------------
import json as _json
import os as _os

from lingclaude.engine import todo as _todo_mod
from lingclaude.engine.todo import migrate_legacy_sqlite_dbs


class TestOcSessionFileMode:
    def test_one_file_per_session(self, tmp_path: Path) -> None:
        """目录模式：不同 session_id → 不同会话文件，数据互不可见。"""
        root = tmp_path / "todos.h1h1h1h1"
        sid: dict[str, str] = {"v": "sess-A"}
        store = TodoStore(root, session_id=lambda: sid["v"])
        store.add(_item("A 会话的任务"))

        sid["v"] = "sess-B"
        assert store.active_items() == []
        assert store.db_path.name == "sess-B.json"
        assert not (root / "sess-B.json").exists()  # 惰性写：纯读不落盘

        sid["v"] = "sess-A"  # 模拟 resume 换回
        assert [i.content for i in store.active_items()] == ["A 会话的任务"]
        assert (root / "sess-A.json").exists()

        sid["v"] = "sess-B"
        store.add(_item("B 会话的任务"))
        assert (root / "sess-B.json").exists()  # 写后才产文件
        sid["v"] = "sess-A"
        assert [i.content for i in store.active_items()] == ["A 会话的任务"]

    def test_lazy_gc_keeps_recent_sessions(self, tmp_path, monkeypatch) -> None:
        """写入惰性 GC：超出上限的旧会话文件被淘汰（mtime LRU）。"""
        monkeypatch.setattr(_todo_mod, "MAX_SESSION_FILES", 3)
        root = tmp_path / "todos.g"
        store = TodoStore(root, session_id="static")

        def _touch(sid: str, mtime: float) -> None:
            _os.utime(root / f"{sid}.json", (mtime, mtime))

        for n in range(5):
            store._session_id = f"s{n}"
            store.add(_item(f"n{n}"))
            _touch(f"s{n}", 1000 + n)  # mtime 递增：s0 最旧
        # 逐次 add 的 GC 每写必跑：s0(s3 写入时)、s1(s4 写入时)、
        # s2(s5 写入时) 依次被淘汰，始终 ≤ MAX_SESSION_FILES

        store._session_id = "s5"
        store.add(_item("新会话写入触发 GC"))
        names = sorted(p.name for p in root.glob("*.json"))
        assert names == ["s3.json", "s4.json", "s5.json"]  # 最旧的 s0/s1/s2 已淘汰

    def test_safe_sid_blocks_traversal(self, tmp_path: Path) -> None:
        """恶意 sid（../穿越/非法字符）被清洗，文件落容器内。"""
        root = tmp_path / "todos.t"
        store = TodoStore(root, session_id="../../escape")
        store.add(_item("x"))
        assert store.db_path.parent == root
        assert store.db_path.name.endswith(".json")
        assert not (tmp_path / "escape").exists()


class TestLegacyMigration:
    @staticmethod
    def _make_legacy_db(db: Path, rows: list[tuple]) -> None:
        import sqlite3

        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE todos (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,"
            "content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',"
            "priority INTEGER NOT NULL DEFAULT 0, tags TEXT NOT NULL DEFAULT '[]',"
            "parent_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO todos VALUES (?,?,?,?,?,?,?,?,?)", rows
        )
        conn.commit()
        conn.close()

    def test_migration_splits_by_session(self, tmp_path: Path) -> None:
        """旧 sqlite 库 → 按 session 拆会话文件 + 库改名 .migrated。"""
        root = tmp_path / "todos.m"
        root.mkdir()
        legacy = tmp_path / "todos.abc12345.db"
        now = 1000.0
        self._make_legacy_db(
            legacy,
            [
                ("t1", "default", "全局默认桶任务", "pending", 0, "[]", None, now, now),
                ("t2", "old-sess", "旧会话任务", "in_progress", 1, '["x"]', None, now, now),
            ],
        )
        n = migrate_legacy_sqlite_dbs(root, [legacy])
        assert n == 2
        assert not legacy.exists()
        assert Path(str(legacy) + ".migrated").exists()

        d = _json.loads((root / "default.json").read_text(encoding="utf-8"))
        o = _json.loads((root / "old-sess.json").read_text(encoding="utf-8"))
        assert d["default"][0]["content"] == "全局默认桶任务"
        assert o["old-sess"][0]["status"] == "in_progress"

        # 迁移产物可被新 store 直接读
        store = TodoStore(root, session_id="old-sess")
        assert [i.content for i in store.active_items()] == ["旧会话任务"]

    def test_migration_missing_db_noop(self, tmp_path: Path) -> None:
        assert migrate_legacy_sqlite_dbs(tmp_path, [tmp_path / "ghost.db"]) == 0

    def test_wiring_migrates_legacy_dir_db(self, tmp_path, monkeypatch) -> None:
        """装配链端到端：本目录旧哈希库被惰性迁入容器目录。"""
        from lingclaude.engine.coding_wiring import (
            _initial_todo_store,
            _resolve_todo_project_scope,
        )

        workdir = tmp_path / "proj"
        workdir.mkdir()
        data = tmp_path / "d"
        data.mkdir()
        monkeypatch.setattr(
            "lingclaude.engine.coding_wiring._resolve_data_dir",
            lambda ctx: data,
        )
        monkeypatch.chdir(workdir)
        scope = _resolve_todo_project_scope()
        legacy = data / f"{scope}.db"
        self._make_legacy_db(
            legacy, [("t1", "default", "待迁移任务", "pending", 0, "[]", None, 1.0, 1.0)]
        )

        store = _initial_todo_store(_make_ctx())  # type: ignore[arg-type]
        assert (data / scope).is_dir()
        assert not legacy.exists()  # 已改名 .migrated
        assert [i.content for i in store.active_items()] == ["待迁移任务"]
