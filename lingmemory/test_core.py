# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵忆 (lingmemory) — 薄主干架构测试

验证：
1. 主干三操作可用：create / transition / query
2. Type Registry 校验生效（非法type/非法转换/缺字段）
3. 树形结构（parent_id）可用
4. 事件历史完整
5. 31缺口的典型场景能在薄主干上跑通
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from lingmemory.core import LingMemory, TypeRegistry, init_db
from lingmemory.fts import FTSSync
from lingmemory.events import EventLog


@pytest.fixture
def lm(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    mem = LingMemory(db_path)
    mem.use_fts(FTSSync(mem.conn)).use_events(EventLog(mem.conn))
    yield mem
    mem.close()


class TestCreate:
    """create 操作"""

    def test_create_task(self, lm):
        rid = lm.create(
            type="task",
            data={"goal": "审计灵康编排层", "boundary": "仅代码审计"},
            created_by="lingclaude",
        )
        record = lm.get(rid)
        assert record["type"] == "task"
        assert record["state"] == "created"
        assert record["data"]["goal"] == "审计灵康编排层"

    def test_create_unknown_type_rejected(self, lm):
        with pytest.raises(ValueError, match="unknown type"):
            lm.create(type="nonexistent", data={})

    def test_create_missing_required_field_rejected(self, lm):
        with pytest.raises(ValueError, match="missing required field"):
            lm.create(type="task", data={})  # goal is required

    def test_create_invalid_enum_rejected(self, lm):
        with pytest.raises(ValueError, match="not in"):
            lm.create(
                type="info",
                data={
                    "content": "test",
                    "info_type": "invalid_type",  # not in enum
                    "written_by": "test",
                    "written_at": "2026-06-15",
                },
            )

    def test_create_with_parent(self, lm):
        task_id = lm.create(type="task", data={"goal": "test"}, created_by="test")
        todo_id = lm.create(
            type="todo",
            data={"title": "step 1", "order_idx": 0},
            parent_id=task_id,
            created_by="test",
        )
        children = lm.get_children(task_id)
        assert len(children) == 1
        assert children[0]["id"] == todo_id


class TestTransition:
    """transition 操作"""

    def test_task_lifecycle(self, lm):
        rid = lm.create(type="task", data={"goal": "test"}, created_by="test")

        assert lm.transition(rid, "start", actor="test") == "active"
        assert lm.transition(rid, "complete", actor="test") == "done"
        assert lm.transition(rid, "archive", actor="test") == "archived"

    def test_illegal_transition_rejected(self, lm):
        rid = lm.create(type="task", data={"goal": "test"}, created_by="test")
        # created -> done is illegal (must go through active)
        with pytest.raises(ValueError, match="illegal transition"):
            lm.transition(rid, "complete")

    def test_session_pause_resume(self, lm):
        task_id = lm.create(type="task", data={"goal": "test"}, created_by="test")
        sid = lm.create(
            type="session",
            data={"owner": "lingclaude"},
            parent_id=task_id,
            created_by="lingclaude",
        )
        lm.transition(sid, "activate", actor="lingclaude")
        lm.transition(sid, "sleep", actor="daemon")
        assert lm.get(sid)["state"] == "sleeping"
        lm.transition(sid, "wake", actor="daemon")
        assert lm.get(sid)["state"] == "active"

    def test_wildcard_transition(self, lm):
        """session 的 end 是通配符转换（任何状态→ended）"""
        task_id = lm.create(type="task", data={"goal": "test"}, created_by="test")
        sid = lm.create(
            type="session",
            data={"owner": "test"},
            parent_id=task_id,
            created_by="test",
        )
        lm.transition(sid, "activate")
        # end should work from active
        assert lm.transition(sid, "end") == "ended"

    def test_split_event(self, lm):
        """拆分：旧task进入split状态，event.data记录子task"""
        old_id = lm.create(
            type="task", data={"goal": "大任务"}, created_by="test"
        )
        lm.transition(old_id, "start")

        child1 = lm.create(type="task", data={"goal": "子任务1"}, parent_id=old_id, created_by="test")
        child2 = lm.create(type="task", data={"goal": "子任务2"}, parent_id=old_id, created_by="test")

        lm.transition(
            old_id,
            "split",
            actor="test",
            data={"reason": "token_overflow", "child_ids": [child1, child2]},
        )
        assert lm.get(old_id)["state"] == "split"

        events = lm.get_events(old_id)
        split_event = [e for e in events if e["event_type"] == "split"][0]
        assert split_event["data"]["child_ids"] == [child1, child2]

    def test_handoff_event(self, lm):
        """移交：session换owner"""
        task_id = lm.create(type="task", data={"goal": "test"}, created_by="lingclaude")
        sid = lm.create(
            type="session",
            data={"owner": "lingclaude"},
            parent_id=task_id,
            created_by="lingclaude",
        )
        lm.transition(sid, "activate")
        lm.transition(
            sid,
            "end",
            actor="lingclaude",
            data={"reason": "handoff to lingflow_plus"},
        )
        # new session under same task with new owner
        sid2 = lm.create(
            type="session",
            data={"owner": "lingflow_plus"},
            parent_id=task_id,
            created_by="lingflow_plus",
        )
        assert lm.get(sid2)["data"]["owner"] == "lingflow_plus"


class TestQuery:
    """query 操作"""

    def test_query_by_type(self, lm):
        lm.create(type="task", data={"goal": "t1"}, created_by="test")
        lm.create(type="task", data={"goal": "t2"}, created_by="test")
        lm.create(type="info", data={"content": "c1", "info_type": "conclusion", "written_by": "t", "written_at": "now"}, created_by="test")

        tasks = lm.query(type="task")
        assert len(tasks["items"]) == 2

        infos = lm.query(type="info")
        assert len(infos["items"]) == 1

    def test_query_by_state(self, lm):
        rid = lm.create(type="task", data={"goal": "t1"}, created_by="test")
        lm.transition(rid, "start")

        created_tasks = lm.query(type="task", state="created")
        active_tasks = lm.query(type="task", state="active")
        assert len(created_tasks["items"]) == 0
        assert len(active_tasks["items"]) == 1

    def test_query_pagination(self, lm):
        for i in range(25):
            lm.create(type="task", data={"goal": f"t{i}"}, created_by="test")

        page1 = lm.query(type="task", limit=10)
        assert len(page1["items"]) == 10
        assert page1["next_cursor"] is not None

        page2 = lm.query(type="task", limit=10, cursor=page1["next_cursor"])
        assert len(page2["items"]) == 10

        page3 = lm.query(type="task", limit=10, cursor=page2["next_cursor"])
        assert len(page3["items"]) == 5
        assert page3["next_cursor"] is None

    def test_query_by_parent(self, lm):
        parent = lm.create(type="task", data={"goal": "parent"}, created_by="test")
        lm.create(type="todo", data={"title": "a", "order_idx": 0}, parent_id=parent, created_by="test")
        lm.create(type="todo", data={"title": "b", "order_idx": 1}, parent_id=parent, created_by="test")
        lm.create(type="todo", data={"title": "c", "order_idx": 2}, parent_id=parent, created_by="test")

        result = lm.query(parent_id=parent, type="todo")
        assert len(result["items"]) == 3


class TestEvents:
    """事件历史完整性"""

    def test_event_chain(self, lm):
        rid = lm.create(type="task", data={"goal": "test"}, created_by="test")
        lm.transition(rid, "start")
        lm.transition(rid, "complete")
        lm.transition(rid, "archive")

        events = lm.get_events(rid)
        assert len(events) == 4  # create + start + complete + archive

        assert events[0]["event_type"] == "create"
        assert events[0]["from_state"] is None
        assert events[0]["to_state"] == "created"

        assert events[1]["event_type"] == "start"
        assert events[1]["from_state"] == "created"
        assert events[1]["to_state"] == "active"

        assert events[3]["event_type"] == "archive"
        assert events[3]["from_state"] == "done"
        assert events[3]["to_state"] == "archived"

    def test_event_data_preserved(self, lm):
        rid = lm.create(type="task", data={"goal": "test"}, created_by="test")
        lm.transition(rid, "start")
        lm.transition(
            rid,
            "complete",
            actor="test",
            data={"conclusion": "审计完成，无Critical"},
        )

        events = lm.get_events(rid)
        complete_event = [e for e in events if e["event_type"] == "complete"][0]
        assert complete_event["data"]["conclusion"] == "审计完成，无Critical"


class TestGapScenarios:
    """31缺口的典型场景验证——薄主干能否消化"""

    def test_gap1_truncation_strategy(self, lm):
        """缺口1：统一消息截断策略 → info.data里存截断标记"""
        rid = lm.create(
            type="info",
            data={
                "content": "结论正文（截断后）",
                "info_type": "conclusion",
                "written_by": "lingclaude",
                "written_at": "2026-06-15",
                "truncated": True,
                "original_size": 62000,
                "truncated_to": 500,
            },
            created_by="lingclaude",
        )
        record = lm.get(rid)
        assert record["data"]["truncated"] is True
        assert record["data"]["original_size"] == 62000

    def test_gap5_cold_hot_storage(self, lm):
        """缺口5：冷热分层 → 主干只有SQLite，热=进程内存，冷=SQLite"""
        # 创建即持久化（冷存储），内存缓存由上层实现
        rid = lm.create(type="task", data={"goal": "test"}, created_by="test")
        assert lm.get(rid) is not None  # 可从冷存储读回

    def test_gap8_identity_types(self, lm):
        """缺口8：身份类型 → session.data.owner 自由字段"""
        member_session = lm.create(
            type="session",
            data={"owner": "lingclaude", "identity_type": "member"},
            created_by="lingclaude",
        )
        assert lm.get(member_session)["data"]["identity_type"] == "member"

    def test_gap14_anti_override(self, lm):
        """缺口14：防越权 → info.visibility控制"""
        rid = lm.create(
            type="info",
            data={
                "content": "审计证据",
                "info_type": "conclusion",
                "visibility": "governance",
                "retain": True,
                "written_by": "lingclaude",
                "written_at": "2026-06-15",
            },
            created_by="lingclaude",
        )
        record = lm.get(rid)
        assert record["data"]["visibility"] == "governance"
        assert record["data"]["retain"] is True

    def test_gap15_lifecycle_states(self, lm):
        """缺口15：会话生命周期 → session状态机已覆盖"""
        task_id = lm.create(type="task", data={"goal": "test"}, created_by="test")
        sid = lm.create(type="session", data={"owner": "t"}, parent_id=task_id, created_by="t")
        # 完整生命周期：created→active→sleeping→active→interrupted→active→ended
        lm.transition(sid, "activate")
        lm.transition(sid, "sleep")
        lm.transition(sid, "wake")
        lm.transition(sid, "interrupt")
        lm.transition(sid, "recover")
        lm.transition(sid, "end")
        assert lm.get(sid)["state"] == "ended"

    def test_gap17_on_demand_lifecycle(self, lm):
        """缺口17：on-demand简化生命周期"""
        # Type Registry 的 states_on_demand 变体
        on_demand_states = lm.registry.get_states("session", variant="on_demand")
        assert on_demand_states == ["created", "active", "ended"]
        assert "sleeping" not in on_demand_states

    def test_gap20_security_compliance(self, lm):
        """缺口20：安全合规 → info.retain + visibility + written_by"""
        rid = lm.create(
            type="info",
            data={
                "content": "安全审计记录",
                "info_type": "conclusion",
                "visibility": "governance",
                "retain": True,
                "is_conclusion": True,
                "written_by": "lingclaude",
                "written_at": "2026-06-15",
            },
            created_by="lingclaude",
        )
        record = lm.get(rid)
        assert record["data"]["retain"] is True
        assert record["data"]["written_by"] == "lingclaude"

    def test_gap25_quota(self, lm):
        """缺口25：资源配额 → quota type"""
        rid = lm.create(
            type="quota",
            data={
                "limit": 150000000,
                "window": "5h",
                "used": 0,
                "scope": "global",
            },
            created_by="system",
        )
        record = lm.get(rid)
        assert record["data"]["limit"] == 150000000
        assert record["data"]["scope"] == "global"

    def test_gap29_performance(self, lm):
        """缺口29：性能 → 游标分页 + 索引"""
        for i in range(50):
            lm.create(type="task", data={"goal": f"t{i}"}, created_by="test")

        page = lm.query(type="task", limit=10)
        assert len(page["items"]) == 10
        assert page["next_cursor"] is not None

    def test_gap31_deployment(self, lm):
        """缺口31：部署方案 → init_db即可"""
        # init_db 在 fixture 中已调用
        assert lm.get(lm.create(type="task", data={"goal": "x"}, created_by="test")) is not None

    def test_publishable_state_machine(self, lm):
        """灵扬产出状态机：draft→review→approved→published"""
        rid = lm.create(
            type="info",
            data={
                "content": "文章草稿",
                "info_type": "conclusion",
                "written_by": "lingyang",
                "written_at": "2026-06-15",
            },
            created_by="lingyang",
        )
        # 手动设置为 draft 状态（模拟产出物变体）
        lm.conn.execute("UPDATE records SET state = 'draft' WHERE id = ?", (rid,))
        lm.conn.commit()

        lm.transition(rid, "submit")
        assert lm.get(rid)["state"] == "review"

        lm.transition(rid, "approve")
        assert lm.get(rid)["state"] == "approved"

        lm.transition(rid, "publish")
        assert lm.get(rid)["state"] == "published"


class TestRegistry:
    """Type Registry 校验"""

    def test_registry_loads(self):
        reg = TypeRegistry()
        assert reg.exists("task")
        assert reg.exists("session")
        assert reg.exists("info")

    def test_default_states(self):
        reg = TypeRegistry()
        assert reg.get_default_state("task") == "created"
        assert reg.get_default_state("session") == "created"
        assert reg.get_default_state("info") == "active"

    def test_on_demand_variant(self):
        reg = TypeRegistry()
        full = reg.get_states("session")
        on_demand = reg.get_states("session", variant="on_demand")
        assert "sleeping" in full
        assert "sleeping" not in on_demand
        assert len(on_demand) < len(full)
