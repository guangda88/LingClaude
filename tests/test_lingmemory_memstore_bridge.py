"""Tests for lingclaude.core.lingmemory_memstore_bridge — P3.3 三件之二 MemoryStore 双写灵忆.

覆盖：
- 双写闭环：put_episode/facet/facet_point/entity/edge → 灵忆 record 落库（live）
- edge 复合键：主路 f-string 与 bridge.edge_key 双锚一致
- 单 record 语义：同键重复 put 不重复建
- 遗忘闭环：on_forget → transition(merge) → merged_away（灵忆侧留史）
- 开关关闭：不写灵忆、不建实例（默认行为与历史版本一致）
- 旁路纪律：sink 抛异常不影响主路；sink 内再触 put 不递归
- 熔断：桥接器首错停摆，不再重复写
"""
from __future__ import annotations

import pytest

from lingclaude.core.memory_engine import (
    Edge,
    EdgeType,
    Entity,
    EntityType,
    Episode,
    Facet,
    FacetPoint,
    MemoryStore,
)
from lingclaude.core.lingmemory_bridge import dualwrite_enabled
from lingclaude.core.lingmemory_memstore_bridge import (
    LingMemoryStoreSink,
    edge_key,
)


@pytest.fixture()
def ms_pair(tmp_path, monkeypatch):
    """独立灵忆库 + 强制开双写开关（不碰生产库 lingmemory.db）"""
    monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
    db = tmp_path / "lingmemory_test.db"
    from lingmemory import init_db

    init_db(db)
    sink = LingMemoryStoreSink(db_path=db)
    store = MemoryStore(db_path=tmp_path / "memory.db", legacy_sink=sink)
    return store, sink, db


def _recs(lm, **kw):
    return lm.query(type="memory_store_entry", **kw)["items"]


def _mk_episode(**kw) -> Episode:
    defaults = dict(title="测试事故", body="测试正文", tags=["t1"])
    defaults.update(kw)
    return Episode(**defaults)


class TestDualWrite:
    def test_put_episode_creates_live_record(self, ms_pair):
        store, sink, _ = ms_pair
        ep = _mk_episode()
        store.put_episode(ep)

        items = _recs(sink._lm, data_filter={"store_name": "episodes"})
        assert len(items) == 1
        rec = items[0]
        assert rec["state"] == "live"
        assert rec["data"]["entry_key"] == ep.id
        assert rec["data"]["value"]["title"] == "测试事故"
        assert rec["data"]["writer"] == "lingclaude.memory_engine"
        assert rec["created_by"] == "lingclaude.memory_engine"

    def test_all_five_write_points_mirror(self, ms_pair):
        store, sink, _ = ms_pair
        ep = _mk_episode()
        store.put_episode(ep)
        facet = Facet(episode_id=ep.id, name="切面", body="切面体")
        store.put_facet(facet)
        fp = FacetPoint(facet_id=facet.id, claim="原子断言", tags=["t2"])
        store.put_facet_point(fp)
        ent = Entity(name="灵克", entity_type=EntityType.MEMBER)
        store.put_entity(ent)
        edge = Edge(source_id=ent.id, target_id=ep.id,
                    edge_type=EdgeType.INVOLVED, context="参与")
        store.put_edge(edge)

        store_names = {r["data"]["store_name"] for r in _recs(sink._lm)}
        assert store_names == {
            "episodes", "facets", "facet_points", "entities", "edges",
        }
        # edge 复合键落库验证（主路 f-string × bridge.edge_key 双锚防漂移）
        edge_recs = _recs(sink._lm, data_filter={"store_name": "edges"})
        assert edge_recs[0]["data"]["entry_key"] == edge_key(
            ent.id, ep.id, "involved")

    def test_re_put_no_duplicate(self, ms_pair):
        store, sink, _ = ms_pair
        ep = _mk_episode()
        store.put_episode(ep)
        store.put_episode(ep)  # 重复 put（INSERT OR REPLACE 语义）
        assert len(_recs(sink._lm)) == 1

    def test_forget_transitions_merged_away(self, ms_pair):
        store, sink, _ = ms_pair
        ep = _mk_episode()
        store.put_episode(ep)
        sink.on_forget("episodes", ep.id)

        states = {r["data"]["entry_key"]: r["state"]
                  for r in _recs(sink._lm)}
        assert states[ep.id] == "merged_away"  # 遗忘 → merge（留史）


class TestSwitchOff:
    def test_disabled_by_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LINGCLAUDE_MEMORY_DUALWRITE", raising=False)
        assert dualwrite_enabled() is False
        sink = LingMemoryStoreSink(db_path=tmp_path / "x.db")
        store = MemoryStore(
            db_path=tmp_path / "memory.db", legacy_sink=sink)
        ep = _mk_episode()
        assert store.put_episode(ep) == ep.id  # 主路照常
        assert sink._lm is None                # 灵忆从未被触

    def test_main_path_works_without_sink(self, tmp_path):
        store = MemoryStore(db_path=tmp_path / "memory.db")
        ep = _mk_episode()
        store.put_episode(ep)
        assert store.get_episode(ep.id).title == "测试事故"


class TestBypassDiscipline:
    def test_broken_sink_does_not_break_main_path(self, tmp_path):
        class BombSink:
            def on_put(self, *args, **kwargs):
                raise RuntimeError("boom")

        store = MemoryStore(
            db_path=tmp_path / "memory.db", legacy_sink=BombSink())
        ep = _mk_episode()
        assert store.put_episode(ep) == ep.id  # 主路不受影响
        assert store.get_episode(ep.id) is not None

    def test_bridge_fuse_on_failure(self, ms_pair, monkeypatch):
        store, sink, _ = ms_pair

        def _boom(*args, **kwargs):
            raise RuntimeError("lm down")

        monkeypatch.setattr(sink, "_lm_create", _boom)
        store.put_episode(_mk_episode())       # 触发首错 → 熔断
        assert sink._broken is True
        store.put_episode(_mk_episode(title="第二条"))  # 熔断后静默不再试
        assert sink._lm is None                # 灵忆从未被真正触达

    def test_recursive_put_guard(self, ms_pair):
        store, sink, _ = ms_pair
        calls: list[str] = []

        def spy_then_reenter(store_name, entry_key, value):
            calls.append(entry_key)
            if len(calls) == 1:
                store.put_episode(_mk_episode(title="递归写"))  # 递归触发

        sink.on_put = spy_then_reenter
        ep = _mk_episode()
        store.put_episode(ep)
        assert calls == [ep.id]  # 自指卫兵：递归 put 未再触发 on_put


class TestEdgeKeyContract:
    def test_edge_key_format(self):
        # 主路 put_edge 内联 f"{s}->{t}:{type}" 与本函数必须一致
        assert edge_key("a", "b", "involved") == "a->b:involved"
