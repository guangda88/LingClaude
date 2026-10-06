"""会话级隔离测试（2026-10-07）。

用户需求：任务清单、输入历史都应是会话级隔离 —— 同目录下 A 会话的任务
面板/输入历史不得泄露给 B 会话。

三层语义：
1. 身份单源：CodingRuntime.session_id 委托宿主 engine（set_runtime 绑定），
   /resume /clear L2 压缩换 id 后自动跟随；无宿主回落 config 字段或惰性随机。
2. TodoStore：session_id 支持 callable，动态解析 → 换会话后面板自动重绑。
3. 输入历史：SessionRoutedHistory 按 history.<session_id> 分文件，resume 后
   上键只见本会话输入；无会话源回落 base 文件（WebUI/CI 旧语义）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lingclaude.core.query_engine import QueryEngine
from lingclaude.engine.coding import CodingRuntime
from lingclaude.engine.todo import TodoItem, TodoStatus, TodoStore

try:  # PT 缺失时跳过 history 路由测试（fallback 语义已由单文件路径覆盖）
    from lingclaude.cli.interface import SessionRoutedHistory

    _HAS_PT = True
except Exception:  # noqa: BLE001
    _HAS_PT = False


# ── 1. 身份单源 ──────────────────────────────────────────────────────────


def test_runtime_follows_host_engine_session_id() -> None:
    engine = QueryEngine()
    runtime = CodingRuntime()
    # 绑定前：无宿主 → 惰性随机，实例内稳定（不与任何其他实例串台）
    first = runtime.session_id
    assert first and runtime.session_id == first
    # 绑定后：跟随宿主
    engine.set_runtime(runtime)
    assert runtime.session_id == engine.session_id


def test_runtime_follows_engine_after_resume_like_rebind() -> None:
    """/resume、load_session、L2 压缩都通过 engine.session_id 赋值换 id ——
    runtime（以及经它动态解析的 todo/审批）必须自动跟随。"""
    engine = QueryEngine()
    runtime = CodingRuntime()
    engine.set_runtime(runtime)
    old_sid = engine.session_id
    engine.session_id = "deadbeefcafebabe"  # session_persist.load_session 同款写法
    assert runtime.session_id == "deadbeefcafebabe"
    assert old_sid != "deadbeefcafebabe"


def test_runtime_setter_writes_through_to_host() -> None:
    """经 runtime 句柄写 session_id（l5_audit L2 压缩场景）写穿透宿主。"""
    engine = QueryEngine()
    runtime = CodingRuntime()
    engine.set_runtime(runtime)
    runtime.session_id = "aaaa1111bbbb2222"
    assert engine.session_id == "aaaa1111bbbb2222"


def test_runtime_fallback_isolated_per_instance() -> None:
    a = CodingRuntime()
    b = CodingRuntime()
    assert a.session_id and b.session_id
    assert a.session_id != b.session_id  # 独立装配路径互不串台


# ── 2. TodoStore 动态会话 ────────────────────────────────────────────────


def _item(content: str) -> TodoItem:
    import time

    now = time.time()
    return TodoItem(
        id=f"t-{content}",
        content=content,
        status=TodoStatus.PENDING,
        created_at=now,
        updated_at=now,
    )


def test_todo_store_callable_session_id_rebinds(tmp_path: Path) -> None:
    sid = {"v": "sessA"}
    store = TodoStore(tmp_path / "todos.db", session_id=lambda: sid["v"])
    store.add(_item("task-of-A"))
    assert [i.content for i in store.active_items()] == ["task-of-A"]
    # 换会话（/resume /clear）→ 旧任务不可见，新会话面板为空
    sid["v"] = "sessB"
    assert store.active_items() == []
    sid["v"] = "sessA"
    assert [i.content for i in store.active_items()] == ["task-of-A"]


def test_todo_store_static_session_id_backward_compat(tmp_path: Path) -> None:
    store = TodoStore(tmp_path / "todos.db", session_id="static")
    store.add(_item("x"))
    assert store.session_id == "static"


def test_todo_wiring_binds_runtime_dynamic_sid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """装配级：TodoStore 经 lambda 绑定 runtime.session_id，换会话自动重绑。"""
    from lingclaude.engine import coding_wiring
    from lingclaude.engine.coding_wiring import _initial_todo_store

    # 打桩 data 目录：真实 data/ 是生产目录（未设 env 时哈希库会落进去，
    # 2026-10-07 两次实测污染的教训）。
    monkeypatch.setattr(
        coding_wiring, "_resolve_data_dir", lambda ctx: tmp_path
    )
    engine = QueryEngine()
    runtime = CodingRuntime()
    engine.set_runtime(runtime)
    ctx = type("Ctx", (), {"runtime": runtime})()
    store = _initial_todo_store(ctx)
    store.add(_item("wired-A"))
    engine.session_id = "cafe1111cafe1111"
    assert store.active_items() == []
    engine.session_id = engine.session_id  # no-op
    engine.session_id = "cafe1111cafe1111"
    assert store.session_id == "cafe1111cafe1111"


# ── 3. 输入历史按会话分文件 ───────────────────────────────────────────────


@pytest.mark.skipif(not _HAS_PT, reason="prompt_toolkit 未安装")
class TestSessionRoutedHistory:
    def test_routed_per_session_file(self, tmp_path: Path) -> None:
        base = tmp_path / "history"
        sid = {"v": "sessA"}
        h = SessionRoutedHistory(str(base), lambda: sid["v"])
        h.store_string("from-A")
        assert h.current_file.name == "history.sessA"
        sid["v"] = "sessB"
        h.store_string("from-B")
        assert h.current_file.name == "history.sessB"
        assert "from-B" not in (tmp_path / "history.sessA").read_text()
        assert "from-A" not in (tmp_path / "history.sessB").read_text()

    def test_replay_only_current_session(self, tmp_path: Path) -> None:
        base = tmp_path / "history"
        sid = {"v": "sessA"}
        h = SessionRoutedHistory(str(base), lambda: sid["v"])
        h.store_string("from-A")
        sid["v"] = "sessB"
        h.store_string("from-B")
        sid["v"] = "sessA"
        assert list(h.load_history_strings()) == ["from-A"]
        sid["v"] = "sessB"
        assert list(h.load_history_strings()) == ["from-B"]

    def test_no_source_falls_back_to_base_file(self, tmp_path: Path) -> None:
        base = tmp_path / "history"
        h = SessionRoutedHistory(str(base), None)
        assert h.current_file == base
        h.store_string("legacy")
        assert "legacy" in base.read_text()

    def test_session_id_sanitized_into_filename(self, tmp_path: Path) -> None:
        base = tmp_path / "history"
        h = SessionRoutedHistory(str(base), lambda: "a/b:c")
        assert "/" not in h.current_file.name and ":" not in h.current_file.name

    def test_resolver_exception_falls_back(self, tmp_path: Path) -> None:
        base = tmp_path / "history"

        def boom() -> str:
            raise RuntimeError("engine gone")

        h = SessionRoutedHistory(str(base), boom)
        h.store_string("x")  # 不因会话解析失败而炸输入路径
        assert h.current_file == base
