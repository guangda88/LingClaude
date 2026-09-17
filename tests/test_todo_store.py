"""TodoStore 测试 —— 2026-09-17 Bug A/B 修复防回归。

Bug A: _connect 无 row_factory，_row_to_item / _release_in_progress 对
       r["id"] 的字典式访问打在默认元组行上 → TypeError
       ("tuple indices must be integers or slices, not str")。
Bug B: 连接缓存在实例属性 self._conn，工具执行器每轮换线程调用同一 store
       → 跨线程复用被 sqlite3 check_same_thread=True 拒绝
       ("SQLite objects created in a thread can only be used in that same thread")。
修复（engine/todo.py）: threading.local 每线程一条连接 + row_factory=sqlite3.Row
       + connect(timeout=5.0)。b8f9df4 入库。

本文件覆盖修复时手工验证的 4 个场景 + 状态纪律 + 会话隔离 + handler 层。
"""

from __future__ import annotations

import threading

from lingclaude.engine.todo import TodoItem, TodoStatus, TodoStore, make_handlers


def make_item(content: str, priority: int = 0) -> TodoItem:
    now = 1000.0
    return TodoItem(
        id=content[:8],  # 测试内 content 唯一 → id 确定性且不冲突
        content=content,
        status=TodoStatus.PENDING,
        created_at=now,
        updated_at=now,
        priority=priority,
    )


# ---- Bug B: 跨线程访问（修复前崩溃路径）--------------------------


def test_add_from_worker_thread(tmp_path):
    """子线程 add（= 修复前 ProgrammingError 崩溃路径）+ 主线程读回一致。"""
    store = TodoStore(tmp_path / "t.db", session_id="s1")
    errs: list[Exception] = []

    def worker():
        try:
            store.add(make_item("跨线程任务"))
        except Exception as e:  # pragma: no cover - 仅回归时触发
            errs.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert not errs, f"worker thread raised: {errs!r}"
    item = store.get("跨线程任务")
    assert item is not None
    assert item.content == "跨线程任务"
    assert item.status == TodoStatus.PENDING


def test_concurrent_writes_no_loss(tmp_path):
    """4 线程 × 20 条并发写：全部落库，无丢失、无异常（timeout=5 兜锁等待）。"""
    store = TodoStore(tmp_path / "t.db", session_id="s1")
    errs: list[Exception] = []
    lock = threading.Lock()

    def worker(n: int):
        for i in range(20):
            try:
                store.add(make_item(f"t{n}-{i}"))
            except Exception as e:
                with lock:
                    errs.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errs, f"concurrent writes raised: {errs[:3]!r}"
    assert len(store.list()) == 80


def test_cross_thread_status_discipline(tmp_path):
    """线程 A add+start，另一线程 start 第二项 → A 的 in_progress 被退回 pending。

    模拟真实场景：工具执行器每轮可能在不同线程调用同一 store。
    """
    store = TodoStore(tmp_path / "t.db", session_id="s1")
    for c in ("任务A", "任务B"):
        store.add(make_item(c))

    r1 = store.start_item("任务A")
    assert r1["ok"] and r1["released"] == []

    results: dict = {}

    def switch():
        results["r2"] = store.start_item("任务B")

    t = threading.Thread(target=switch)
    t.start()
    t.join()

    r2 = results["r2"]
    assert r2["ok"]
    assert r2["released"] == ["任务A"]
    assert store.get("任务A").status == TodoStatus.PENDING
    assert store.get("任务B").status == TodoStatus.IN_PROGRESS


# ---- Bug A: Row 工厂（字典式行访问）------------------------------


def test_row_factory_dict_access(tmp_path):
    """_row_to_item 的 r['id'] 字典式访问依赖 Row 工厂（Bug A 回归）。

    get() 走 _row_to_item；start_item() 走 _release_in_progress——两条
    字典式访问路径都要过。
    """
    store = TodoStore(tmp_path / "t.db", session_id="s1")
    store.add(make_item("字典访问"))
    item = store.get("字典访问")
    assert item is not None
    assert item.tags == []
    assert item.priority == 0
    r = store.start_item("字典访问")
    assert r["ok"] is True


# ---- 状态纪律（对标 AtomCode todowrite）--------------------------


def test_single_in_progress_discipline(tmp_path):
    """恰好一个 in_progress：start 新项时旧 in_progress 退回 pending（非 completed）。"""
    store = TodoStore(tmp_path / "t.db", session_id="s1")
    for c in ("a", "b", "c"):
        store.add(make_item(c))

    assert store.start_item("a")["released"] == []
    assert store.start_item("b")["released"] == ["a"]
    assert store.start_item("c")["released"] == ["b"]

    statuses = {i.id: i.status for i in store.list()}
    assert statuses["a"] == TodoStatus.PENDING  # 被中断 → 回 pending
    assert statuses["b"] == TodoStatus.PENDING
    assert statuses["c"] == TodoStatus.IN_PROGRESS


def test_start_missing_item(tmp_path):
    store = TodoStore(tmp_path / "t.db", session_id="s1")
    r = store.start_item("ghost")
    assert r == {"ok": False, "id": "ghost", "error": "not_found"}


def test_active_items_excludes_done(tmp_path):
    """待办视图只含 pending + in_progress。"""
    store = TodoStore(tmp_path / "t.db", session_id="s1")
    store.add(make_item("done项"))
    store.add(make_item("cancel项"))
    store.add(make_item("active项"))
    store.update_status("done项", TodoStatus.COMPLETED)
    store.update_status("cancel项", TodoStatus.CANCELLED)
    active = store.active_items()
    assert [i.id for i in active] == ["active项"]


# ---- 会话隔离 / CRUD ---------------------------------------------


def test_session_isolation(tmp_path):
    """同库不同 session_id 互不可见。"""
    db = tmp_path / "t.db"
    s1 = TodoStore(db, session_id="s1")
    s2 = TodoStore(db, session_id="s2")
    s1.add(make_item("s1专属"))
    assert s1.get("s1专属") is not None
    assert s2.get("s1专属") is None
    assert s2.list() == []
    # s2 建 item 不影响 s1 计数
    s2.add(make_item("s2专属"))
    assert len(s1.list()) == 1
    assert len(s2.list()) == 1


def test_update_complete_cancel_delete(tmp_path):
    store = TodoStore(tmp_path / "t.db", session_id="s1")
    store.add(make_item("x"))
    assert store.update_status("x", TodoStatus.COMPLETED) is True
    assert store.get("x").status == TodoStatus.COMPLETED
    store.add(make_item("y"))
    assert store.delete("y") is True
    assert store.delete("y") is False  # 二次删除 → False
    assert store.update_status("ghost", TodoStatus.CANCELLED) is False


def test_priority_ordering(tmp_path):
    """list 按 priority DESC, created_at ASC 排序。"""
    store = TodoStore(tmp_path / "t.db", session_id="s1")
    store.add(make_item("低", priority=1))
    store.add(make_item("高", priority=9))
    store.add(make_item("中", priority=5))
    assert [i.content for i in store.list()] == ["高", "中", "低"]


# ---- handler 层（make_handlers 注册给 CodingRuntime 的一层）-------


def test_handlers_roundtrip(tmp_path):
    store = TodoStore(tmp_path / "t.db", session_id="s1")
    h = make_handlers(store)

    created = h["create"](content="handler任务", priority=3)
    assert created["ok"] is True
    tid = created["todo"]["id"]

    started = h["start"](id=tid)
    assert started["ok"] and started["status"] == "in_progress"

    listed = h["list"](status="in_progress")
    assert listed["count"] == 1 and listed["todos"][0]["id"] == tid

    done = h["complete"](id=tid)
    assert done["ok"]

    got = h["get"](id=tid)
    assert got["todo"]["status"] == "completed"

    # 不存在的 id：start/complete/get 都返回 not_found
    assert h["start"](id="ghost") == {"ok": False, "error": "not_found"}
    assert h["complete"](id="ghost") == {"ok": False, "error": "not_found"}
    assert h["get"](id="ghost") == {"ok": False, "error": "not_found"}
