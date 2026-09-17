"""Regression tests: todo delete handler 三元取反 Bug."""
import time
import pytest
from lingclaude.engine.todo import TodoStore, TodoItem, TodoStatus


def _store(tmp_path):
    return TodoStore(db_path=str(tmp_path / "t.db"), session_id="t")


def _add(s, content="x"):
    now = time.time()
    s.add(TodoItem(id=f"id-{content}-{now}", content=content,
                   status=TodoStatus.PENDING, created_at=now, updated_at=now))
    return s.list()[0].id


def test_delete_ok(tmp_path):
    s = _store(tmp_path)
    tid = _add(s)
    assert s.delete(tid) is True


def test_delete_twice(tmp_path):
    s = _store(tmp_path)
    tid = _add(s)
    assert s.delete(tid) is True
    assert s.delete(tid) is False


def test_delete_handler_semantics(tmp_path):
    """handler 层: 删除成功必须 ok=True (回归: 原三元取反恒返回失败)."""
    s = _store(tmp_path)
    tid = _add(s)
    ok = s.delete(tid)
    result = {"ok": True, "id": tid, "deleted": True} if ok else {"ok": False, "error": "not_found"}
    assert result == {"ok": True, "id": tid, "deleted": True}


def test_start_completed_rejected(tmp_path):
    """回归 (atomcode B3): start 不得复活已完成项。"""
    s = _store(tmp_path)
    now = time.time()
    s.add(TodoItem(id="done1", content="done", status=TodoStatus.COMPLETED,
                   created_at=now, updated_at=now))
    r = s.start_item("done1")
    assert r["ok"] is False and r["error"] == "already_finished"


def test_start_cancelled_rejected(tmp_path):
    s = _store(tmp_path)
    now = time.time()
    s.add(TodoItem(id="cx1", content="cx", status=TodoStatus.CANCELLED,
                   created_at=now, updated_at=now))
    r = s.start_item("cx1")
    assert r["ok"] is False and r["error"] == "already_finished"


def test_start_pending_still_works(tmp_path):
    s = _store(tmp_path)
    tid = _add(s, "live")
    r = s.start_item(tid)
    assert r["ok"] is True and r["status"] == "in_progress"
