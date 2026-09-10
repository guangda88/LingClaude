"""P3.3 五层记忆(L2 Experience) → 灵忆 双写桥测试

覆盖：
- 双写闭环：store → 灵忆 record 落库（working，layer_of_origin=experience）
- 单 record 语义：同 id 重复 store 不重复建
- 遗忘闭环：decay_all 出清 → transition(forget) → archived；未出清保持 working
- SQLite 主路同款闭环
- 开关关闭：不写灵忆、不建实例（默认行为与历史版本一致）
- 旁路纪律：sink 抛异常不影响主路返回值；sink 内再触 store 不递归
- 熔断：桥接器首错停摆，不再重复写
- wiring 装配：开关控制 sink 注入
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from lingclaude.core.layered_memory import (
    Experience,
    ExperienceStore,
    InMemoryExperienceStore,
)
from lingclaude.core.lingmemory_bridge import dualwrite_enabled
from lingclaude.core.lingmemory_experience_bridge import LingMemoryExperienceSink


def _mk_exp(**kw) -> Experience:
    defaults = dict(problem="测试问题", reflection="测试反思")
    defaults.update(kw)
    return Experience.create(**defaults)


@pytest.fixture()
def lm_pair(tmp_path, monkeypatch):
    """独立灵忆库 + 强制开双写开关（不碰生产库 lingmemory.db）"""
    monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
    db = tmp_path / "lingmemory_test.db"
    from lingmemory import init_db

    init_db(db)
    sink = LingMemoryExperienceSink(db_path=db)
    store = InMemoryExperienceStore(legacy_sink=sink)
    return store, sink, db


def _recs(lm, **kw):
    return lm.query(type="layered_memory_entry", **kw)["items"]


class TestDualWrite:
    def test_store_creates_working_record(self, lm_pair):
        store, sink, _ = lm_pair
        exp = _mk_exp()
        rid = store.store(exp)

        items = _recs(sink._lm)
        assert len(items) == 1
        rec = items[0]
        assert rec["state"] == "working"
        assert rec["data"]["experience_id"] == exp.id == rid
        assert rec["data"]["layer_of_origin"] == "experience"
        assert "测试问题" in rec["data"]["content"]
        assert rec["created_by"] == "lingclaude.layered_memory"

    def test_re_store_no_duplicate(self, lm_pair):
        store, sink, _ = lm_pair
        exp = _mk_exp()
        store.store(exp)
        store.store(exp)  # 重复 store（INSERT OR REPLACE 语义）
        assert len(_recs(sink._lm)) == 1

    def test_decay_forgotten_transitions_archived(self, lm_pair):
        store, sink, _ = lm_pair
        old = replace(
            _mk_exp(),
            last_recalled=datetime.now(timezone.utc) - timedelta(days=365),
        )
        fresh = _mk_exp()
        store.store(old)
        store.store(fresh)

        updated = store.decay_all()
        assert updated == 2

        states = {r["data"]["experience_id"]: r["state"]
                  for r in _recs(sink._lm)}
        assert states[old.id] == "archived"   # 出清 → forget
        assert states[fresh.id] == "working"  # 未出清保持

    def test_sqlite_store_same_closedloop(self, lm_pair, tmp_path):
        store, sink, _ = lm_pair
        sqlite_store = ExperienceStore(
            db_path=str(tmp_path / "exp.db"), legacy_sink=sink,
        )
        exp = _mk_exp()
        sqlite_store.store(exp)
        items = _recs(sink._lm)
        assert len(items) == 1
        assert items[0]["data"]["experience_id"] == exp.id


class TestSwitchOff:
    def test_disabled_by_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LINGCLAUDE_MEMORY_DUALWRITE", raising=False)
        assert dualwrite_enabled() is False
        sink = LingMemoryExperienceSink(db_path=tmp_path / "x.db")
        store = InMemoryExperienceStore(legacy_sink=sink)
        exp = _mk_exp()
        assert store.store(exp) == exp.id  # 主路照常
        assert sink._lm is None            # 灵忆从未被触

    def test_main_path_record_present_without_lm(self, monkeypatch):
        monkeypatch.delenv("LINGCLAUDE_MEMORY_DUALWRITE", raising=False)
        store = InMemoryExperienceStore()
        exp = _mk_exp()
        store.store(exp)
        assert store.get_stats()["total_experiences"] == 1


class TestBypassDiscipline:
    def test_broken_sink_does_not_break_main_path(self):
        class BombSink:
            def on_store(self, exp_dict):
                raise RuntimeError("bomb")

        store = InMemoryExperienceStore(legacy_sink=BombSink())
        exp = _mk_exp()
        assert store.store(exp) == exp.id  # 主路不受影响
        assert store.get_stats()["total_experiences"] == 1

    def test_bridge_fuse_on_failure(self, lm_pair, monkeypatch):
        store, sink, _ = lm_pair

        def _boom(exp_dict):
            raise ValueError("灵忆写失败")

        monkeypatch.setattr(sink, "_lm_create", _boom)
        store.store(_mk_exp())
        assert sink._broken is True   # 熔断
        assert store.store(_mk_exp()) is not None  # 主路继续
        assert sink._lm is None       # 熔断后不再触灵忆

    def test_recursive_store_guard(self, lm_pair):
        """sink 内再触发同一 store 不递归（_in_sink_emit 卫兵）"""

        class ReentrantSink:
            _entered = False

            def on_store(self, exp_dict):
                if not ReentrantSink._entered:
                    ReentrantSink._entered = True
                    nested.store(_mk_exp(problem="嵌套写入"))

        nested = InMemoryExperienceStore(legacy_sink=ReentrantSink())
        nested.store(_mk_exp())  # 不应 RecursionError
        assert nested.get_stats()["total_experiences"] == 2  # 嵌套那笔进了主路


class TestWiringIntegration:
    def test_make_layered_memory_attaches_sink_when_enabled(
        self, monkeypatch, tmp_path,
    ):
        from lingclaude.core.wiring import _make_layered_memory

        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
        lm = _make_layered_memory(object())
        assert isinstance(lm.experience, InMemoryExperienceStore)
        assert lm.experience._legacy_sink is not None
        assert isinstance(lm.experience._legacy_sink, LingMemoryExperienceSink)

    def test_make_layered_memory_no_sink_when_disabled(self, monkeypatch):
        from lingclaude.core.wiring import _make_layered_memory

        monkeypatch.delenv("LINGCLAUDE_MEMORY_DUALWRITE", raising=False)
        lm = _make_layered_memory(object())
        assert lm.experience._legacy_sink is None
