"""todo_write R4 重写回归（2026-10-08，OC/AC/CC 三方审计定案落地）。

覆盖：
  · store 原子替换（replace_all / merge_replace）—— OC 借鉴1「撕裂态」防回归
  · 稳定序号 id（alloc_seq_id / parse_seq）—— OC 借鉴3「uuid 重造断引用」防回归
  · PLAN 校验整条拒绝 + 基线保护 —— AC parse_todos 语义
  · 单工具双形态（action=add / update）—— AC 借鉴1
  · user 来源项覆写保护 —— OC 借鉴2b「三写者时序随机丢失」防回归
  · 权威回灌（todos 字段带 id）—— OC 借鉴4
  · 并发：模型全量覆写 vs 用户外部插入 —— OC 借鉴7 点名的原子性用例
"""

from __future__ import annotations

import threading

import pytest

from lingclaude.engine.todo import (
    TodoStatus,
    TodoStore,
    make_handlers,
    validate_plan,
)
from lingclaude.engine.tool_handlers.todo_tools import TodoToolsMixin


class Host(TodoToolsMixin):
    """最小宿主：只挂 todo 相关属性（handler 不依赖 coding.py）。"""

    def __init__(self, store: TodoStore) -> None:
        self._todo_store = store
        self._todo_handlers = make_handlers(store)


@pytest.fixture()
def env(tmp_path):
    store = TodoStore(tmp_path / "todos", session_id="s1")
    return Host(store), store


def _echo_map(result):
    assert result.error is None, f"unexpected err: {result.error}"
    return {t["id"]: t for t in result.data["todos"]}


# ---- store 层：原子替换 ----------------------------------------------------


def test_replace_all_atomic_roundtrip(tmp_path):
    store = TodoStore(tmp_path / "t.db", session_id="s1")
    store.add(_item("a", "t1"))
    store.replace_all([_item("b", "t2")])
    assert [i.content for i in store.list()] == ["b"]


def _item(content: str, id: str, source: str = "model"):
    from lingclaude.engine.todo import TodoItem

    return TodoItem(
        id=id, content=content, status=TodoStatus.PENDING,
        created_at=1000.0, updated_at=1000.0, source=source,
    )


def test_merge_replace_keeps_user_items(tmp_path):
    store = TodoStore(tmp_path / "t.db", session_id="s1")
    store.add(_item("用户项", "t1", source="user"))
    store.add(_item("模型项", "t2"))
    merged = store.merge_replace([_item("模型项", "t2")])
    assert sorted(i.content for i in merged) == ["模型项", "用户项"]
    assert [i.content for i in store.list()] == ["模型项", "用户项"]


def test_merge_replace_no_duplicate_inherited_ids(tmp_path):
    store = TodoStore(tmp_path / "t.db", session_id="s1")
    store.add(_item("被继承的user项", "t7", source="user"))
    merged = store.merge_replace([_item("被继承的user项", "t7", source="user")])
    assert len(merged) == 1 and len(store.list()) == 1


def test_seq_ids_monotonic_and_tolerate_legacy(tmp_path):
    store = TodoStore(tmp_path / "t.db", session_id="s1")
    store.add(_item("旧uuid项", "a1b2c3d4"))  # 旧 uuid8 形态
    ids = store.alloc_seq_id(3)
    assert ids == ["t1", "t2", "t3"]
    store.add(_item("序号项", "t3"))
    assert store.alloc_seq_id(2) == ["t4", "t5"]
    assert store.parse_seq("t12") == 12 and store.parse_seq("a1b2") is None


# ---- reducer：整条拒绝（AC parse_todos 语义）-------------------------------


def test_validate_plan_rejects_double_in_progress():
    norm, _, err = validate_plan(
        [{"content": "A", "status": "in_progress"},
         {"content": "B", "status": "in_progress"}],
        None,
    )
    assert err is not None and norm == []


def test_validate_plan_rejects_bad_status_and_dup():
    assert validate_plan([{"content": "C", "status": "done"}], None)[2]
    assert validate_plan(
        [{"content": "A", "status": "pending"},
         {"content": "a", "status": "pending"}], None)[2]


def test_validate_plan_active_id_wins_discipline():
    norm, exact, err = validate_plan(
        [{"content": "A", "status": "in_progress"},
         {"content": "B", "status": "pending"}],
        "B",
    )
    assert err is None and exact == "B"
    assert {t["content"]: t["status"] for t in norm} == {
        "A": "pending", "B": "in_progress",
    }


# ---- handler：PLAN / 增量 / 回灌 -------------------------------------------


def test_plan_stable_ids_across_replan(env):
    h, _ = env
    r1 = h._todo_write_handler(todos=[
        {"content": "实现 X", "status": "in_progress"},
        {"content": "测试 X", "status": "pending"},
    ], active_id="实现 X")
    old = {t["content"]: t["id"] for t in r1.data["todos"]}
    r2 = h._todo_write_handler(todos=[
        {"content": "实现 X", "status": "completed"},
        {"content": "新任务 Y", "status": "in_progress"},
    ], active_id="新任务 Y")
    new = {t["content"]: t["id"] for t in r2.data["todos"]}
    assert new["实现 X"] == old["实现 X"]          # 稳定 id
    assert "测试 X" not in new                      # 未列出 → 移除
    assert new["新任务 Y"].startswith("t")          # 新增 → 序号 id


def test_plan_invalid_leaves_baseline_untouched(env):
    h, store = env
    ok = h._todo_write_handler(todos=[{"content": "基线", "status": "pending"}])
    before = [(i.content, i.status) for i in store.list()]
    for bad in (
        {"todos": [{"content": "A", "status": "in_progress"},
                   {"content": "B", "status": "in_progress"}]},
        {"todos": [{"content": "C", "status": "done"}]},
        {"todos": [{"content": "", "status": "pending"}]},
        {"todos": [{"content": "D", "status": "pending"}], "active_id": "不存在"},
        {"todos": []},
    ):
        r = h._todo_write_handler(**bad)
        assert r.error is not None
    assert [(i.content, i.status) for i in store.list()] == before


def test_action_add_and_update(env):
    h, store = env
    h._todo_write_handler(todos=[
        {"content": "主任务", "status": "in_progress"},
    ], active_id="主任务")
    r_add = h._todo_write_handler(action="add", content="插入 Z")
    ids = {t["content"]: t["id"] for t in r_add.data["todos"]}
    assert "插入 Z" in ids and r_add.error is None

    r_upd = h._todo_write_handler(action="update", id=ids["插入 Z"], status="in_progress")
    st = {t["content"]: t["status"] for t in r_upd.data["todos"]}
    assert st["插入 Z"] == "in_progress" and st["主任务"] == "pending"  # 纪律①
    assert h._todo_write_handler(action="update", id="zzzz", status="completed").error
    assert h._todo_write_handler(action="update", id=ids["插入 Z"], status="hmm").error
    assert h._todo_write_handler(action="nope").error
    assert h._todo_write_handler(action="add", content="  ").error


def test_user_source_survives_model_full_replace(env):
    h, store = env
    h._todo_write_handler(todos=[{"content": "M", "status": "pending"}])
    r = h._todo_write_handler(action="add", content="用户插入 U")
    uid = next(t["id"] for t in r.data["todos"] if t["content"] == "用户插入 U")
    it = store.get(uid)
    it.source = "user"
    store.add(it)  # upsert 打标（_mark_user_source 同路径）
    h._todo_write_handler(todos=[{"content": "M", "status": "in_progress"},
                                 {"content": "M2", "status": "pending"}],
                          active_id="M")
    assert store.get(uid) is not None  # 未被静默清掉
    assert store.get(uid).source == "user"


def test_concurrent_full_replace_vs_user_insert_no_loss(env):
    """OC 借鉴7：线程 A 全量覆写 × 线程 B 用户插入 → user 项零丢失、无撕裂。"""
    h, store = env
    errs: list[Exception] = []

    def writer_a():
        try:
            for k in range(15):
                h._todo_write_handler(todos=[
                    {"content": f"模型 {k}", "status": "pending"},
                ])
        except Exception as e:  # pragma: no cover
            errs.append(e)

    def writer_b():
        try:
            for k in range(15):
                # R4b（20261008 裁决）：source 随 create 构造原子打标，
                # 消灭「锁外补标」TOCTOU 窗口（原红测暴露的设计缺陷）。
                h._todo_handlers["create"](content=f"用户 {k}", source="user")
        except Exception as e:  # pragma: no cover
            errs.append(e)

    ta, tb = threading.Thread(target=writer_a), threading.Thread(target=writer_b)
    ta.start(); tb.start(); ta.join(); tb.join()
    assert not errs
    finals = store.list()
    contents = {i.content for i in finals}
    # user 项全部存活（保护语义=累积保留），模型侧只留最后一轮
    assert "模型 14" in contents
    assert all(f"用户 {k}" in contents for k in range(15))
    assert all(i.id for i in finals)  # 无半成品行
