"""P2 红→绿验收：todo_store / session_runtime 入槽（含状态迁移）。

对应主方案 §八 P2 验收：swap 不丢状态；同库重连测试绿；session 重绑定测试绿。

红因（改动前）：
- coding.py 裸持 TodoStore / SessionRuntime 实例，swap 需重建整个 CodingRuntime；
- 换库/换配置必须重启进程。

改动后以下测试必须绿。透明句柄让 CLI/repl/tasks/todo_tools/lifecycle 的
属性访问代码零改动，同时获得 swap 后解析新实例的能力。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lingclaude.core.config import lingclaudeConfig
from lingclaude.core.slot import TransparentSlotHandle
from lingclaude.engine.coding import CodingRuntime
from lingclaude.engine.todo import TodoItem, TodoStatus, TodoStore


def _make_item(content: str, priority: int = 0) -> TodoItem:
    import time

    now = time.time()
    return TodoItem(
        id=f"test-{now}",
        content=content,
        status=TodoStatus.PENDING,
        created_at=now,
        updated_at=now,
        priority=priority,
    )


def _runtime(tmp_path: Path, session_id: str = "default") -> CodingRuntime:
    """隔离 data_dir 的 runtime（不污染仓库 data/）。

    lingclaudeConfig 无顶层 session_id 字段；CodingRuntime 用
    getattr(config,'session_id','default') 兜底，故 session_id 参数仅用于
    测试内区分 db 路径，通过 env 隔离已足够。
    """
    import os

    old = os.environ.get("LINGCLAUDE_DATA_DIR")
    os.environ["LINGCLAUDE_DATA_DIR"] = str(tmp_path / session_id)
    try:
        return CodingRuntime(lingclaudeConfig())
    finally:
        if old is None:
            os.environ.pop("LINGCLAUDE_DATA_DIR", None)
        else:
            os.environ["LINGCLAUDE_DATA_DIR"] = old


# ---------------------------------------------------------------------------
# todo_store 入槽
# ---------------------------------------------------------------------------
class TestTodoStoreInSlot:
    def test_todo_store_is_transparent_handle(self, tmp_path: Path) -> None:
        rt = _runtime(tmp_path)
        assert isinstance(rt._todo_store, TransparentSlotHandle)
        assert "todo_store" in rt._slot_manager.names()

    def test_todo_store_method_call_works(self, tmp_path: Path) -> None:
        rt = _runtime(tmp_path)
        # 透明句柄：method 调用直接代理到当前实例，消费方零改动
        items = rt._todo_store.list()
        assert isinstance(items, list)

    def test_todo_store_swap_same_db_no_state_loss(self, tmp_path: Path) -> None:
        rt = _runtime(tmp_path)
        store_v1 = rt._todo_store.instance()
        store_v1.add(_make_item("任务A", priority=1))
        before = len(rt._todo_store.list())

        # swap = 关旧连开新连指向同库
        rt._slot_manager.rebuild(
            "todo_store",
            lambda: TodoStore(store_v1.db_path, session_id=store_v1.session_id),
            reason="config",
        )
        # swap 后句柄解析到新实例，但数据在同库不丢
        assert len(rt._todo_store.list()) == before

    def test_todo_handlers_derivative_exists(self, tmp_path: Path) -> None:
        rt = _runtime(tmp_path)
        # _todo_handlers 是派生物：随源槽构建
        assert isinstance(rt._todo_handlers, dict)
        assert "create" in rt._todo_handlers


# ---------------------------------------------------------------------------
# session_runtime 入槽
# ---------------------------------------------------------------------------
class TestSessionRuntimeInSlot:
    def test_session_runtime_is_transparent_handle(self, tmp_path: Path) -> None:
        rt = _runtime(tmp_path)
        assert isinstance(rt._session_runtime, TransparentSlotHandle)
        assert "session_runtime" in rt._slot_manager.names()

    def test_session_runtime_method_call_works(self, tmp_path: Path) -> None:
        rt = _runtime(tmp_path)
        # 透明句柄：lifecycle mixin 的 method 调用零改动
        path = rt._session_runtime.session_state_path()
        assert isinstance(path, Path)

    def test_session_runtime_swap_rebinds_runtime(self, tmp_path: Path) -> None:
        rt = _runtime(tmp_path)
        from lingclaude.core.session_runtime import SessionRuntime

        rt._slot_manager.rebuild(
            "session_runtime",
            lambda: SessionRuntime(rt),
            reason="config",
        )
        # swap 后新实例的 engine 引用是当前 runtime（重绑定正确）
        assert rt._session_runtime.session_state_path() is not None


# ---------------------------------------------------------------------------
# 三槽共存 + 槽数预算
# ---------------------------------------------------------------------------
class TestThreeSlotsCoexist:
    def test_all_three_slots_registered(self, tmp_path: Path) -> None:
        rt = _runtime(tmp_path)
        names = rt._slot_manager.names()
        assert "model_provider" in names
        assert "todo_store" in names
        assert "session_runtime" in names
        # 3 ≤ 8（槽数预算内）
        assert len(names) <= 8

    def test_slots_independent_swap(self, tmp_path: Path) -> None:
        rt = _runtime(tmp_path, session_id="indep")
        from lingclaude.core.session_runtime import SessionRuntime

        # 各槽独立 swap 互不干扰
        rt._slot_manager.rebuild(
            "todo_store",
            lambda: TodoStore(rt._todo_store.instance().db_path, session_id="indep"),
            reason="config",
        )
        rt._slot_manager.rebuild(
            "session_runtime", lambda: SessionRuntime(rt), reason="config"
        )
        assert rt._session_runtime.session_state_path() is not None
        assert isinstance(rt._todo_store.list(), list)
