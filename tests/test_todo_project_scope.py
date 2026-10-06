"""todo 项目作用域隔离测试（2026-10-07 串台修复）。

根因：todos.db 落包仓库根 data/（全局单库）且 session_id 恒为 "default"，
A 目录创建的任务出现在 B 目录的 lc 任务面板。

修复语义：
- 未设 LINGCLAUDE_DATA_DIR：按 CWD 哈希分库（todos.<cwd_hash8>.db），
  同目录同库、异目录隔离；
- 显式 LINGCLAUDE_DATA_DIR：保持旧语义（显式全局共享，测试/容器自管隔离）。
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
    assert k1.startswith("todos.") and k1.endswith(".db")
    assert len(k1) == len("todos.") + 8 + 3


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
    assert store.db_path.name == "todos.db"
    assert store.db_path.parent == data_dir


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
    assert store.db_path.name == _resolve_todo_project_scope()
    assert store.db_path.name != "todos.db"
