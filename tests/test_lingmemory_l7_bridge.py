"""Tests for lingclaude.core.lingmemory_l7_bridge — P3.3 三件之三 L7 认知层双写灵忆.

覆盖：
- 双写闭环：put_memory/put_doc/put_glossary/put_edge/log_session_event → 灵忆 record 落库（active）
- anchor 去重：同 anchor 重复 put 不重复建（首写为准）
- 跨重启续接：新 sink 回查灵忆侧 active record 续接
- facade：L7Cognitive.remember 写路径同样镜像
- 开关关闭 / 无 sink：零依赖（不建灵忆库、不写记录）
- 旁路纪律：sink 抛异常不影响主路；sink 内重入 put 不递归（_in_sink_emit）
- 熔断：桥接器首错停摆，不再重试
- edge 双锚：origin_id(s->t:rel) 与 entry_key(s->rel->t) 与主路 f-string 一致
"""
from __future__ import annotations

import pytest

from lingclaude.core.l7_cognitive import (
    CognitiveMemory,
    CognitiveStore,
    DocIndex,
    GlossaryTerm,
    GraphEdge,
    L7Cognitive,
)
from lingclaude.core.lingmemory_l7_bridge import LingMemoryL7Sink, l7_edge_key


# ---------------------------------------------------------------------------
# 灵忆侧查询辅助
# ---------------------------------------------------------------------------


def _lm_items(db, origin=None):
    from lingmemory import LingMemory

    lm = LingMemory(db_path=str(db))
    kw = {"type": "l7_state", "state": "active"}
    if origin is not None:
        kw["data_filter"] = {"origin": origin}
    return lm.query(**kw)["items"]


def _make_db(tmp_path, name="lm.db"):
    from lingmemory import init_db

    db = tmp_path / name
    init_db(db)
    return db


def _make_store(tmp_path, sink=None, name="l7.db"):
    return CognitiveStore(db_path=tmp_path / name, legacy_sink=sink)


# ---------------------------------------------------------------------------
# 双写闭环
# ---------------------------------------------------------------------------


class TestDualWriteClosedLoop:
    def test_memory_write_mirrors(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
        db = _make_db(tmp_path)
        sink = LingMemoryL7Sink(db_path=str(db))
        store = _make_store(tmp_path, sink)
        mem = CognitiveMemory(key="决策:用PG", value="PostgreSQL 16", importance=8)
        store.put_memory(mem)
        items = _lm_items(db, origin="memory")
        assert len(items) == 1
        data = items[0]["data"]
        assert data["entry_key"] == "决策:用PG"
        assert "PostgreSQL 16" in data["summary"]
        assert data["importance"] == 8
        assert data["tier"] == "always"
        assert data["origin_id"] == mem.id

    def test_four_origins_all_mirrored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
        db = _make_db(tmp_path)
        sink = LingMemoryL7Sink(db_path=str(db))
        store = _make_store(tmp_path, sink)
        store.put_doc(DocIndex(path="docs/a.md", title="A", summary="文档A"))
        store.put_glossary(GlossaryTerm(term="灵元", definition="灵字辈平台统称"))
        store.put_edge(GraphEdge(source_id="m1", target_id="m2", relation="related"))
        store.log_session_event("s1", "user_prompt", "测试")
        items = _lm_items(db)
        assert sorted(i["data"]["origin"] for i in items) == [
            "doc", "edge", "glossary", "session_log",
        ]

    def test_edge_dual_anchor(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
        db = _make_db(tmp_path)
        sink = LingMemoryL7Sink(db_path=str(db))
        store = _make_store(tmp_path, sink)
        store.put_edge(GraphEdge(source_id="m1", target_id="m2", relation="related"))
        edge = _lm_items(db, origin="edge")[0]["data"]
        assert edge["origin_id"] == "m1->m2:related"
        assert edge["entry_key"] == "m1->related->m2"
        assert l7_edge_key("m1", "related", "m2") == edge["entry_key"]  # 与主路 f-string 双锚

    def test_duplicate_put_no_recreate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
        db = _make_db(tmp_path)
        sink = LingMemoryL7Sink(db_path=str(db))
        store = _make_store(tmp_path, sink)
        mem = CognitiveMemory(key="k1", value="v1", importance=5)
        store.put_memory(mem)
        store.put_memory(mem)  # INSERT OR REPLACE 同 id 重放
        assert len(_lm_items(db, origin="memory")) == 1

    def test_cross_restart_resume(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
        db = _make_db(tmp_path)
        sink = LingMemoryL7Sink(db_path=str(db))
        store = _make_store(tmp_path, sink)
        mem = CognitiveMemory(key="k2", value="v2", importance=3)
        store.put_memory(mem)
        # 新 sink（模拟重启）：内存映射空，回查灵忆侧续接，不重复建
        sink2 = LingMemoryL7Sink(db_path=str(db))
        store2 = _make_store(tmp_path, sink2, name="l7_2.db")
        store2.put_memory(mem)
        assert len(_lm_items(db, origin="memory")) == 1

    def test_facade_remember_mirrors(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
        db = _make_db(tmp_path)
        sink = LingMemoryL7Sink(db_path=str(db))
        cog = L7Cognitive(db_path=tmp_path / "l7_facade.db", legacy_sink=sink)
        cog.remember("北极星", "P3.3 收官", importance=9)
        matches = [
            i for i in _lm_items(db, origin="memory")
            if i["data"]["entry_key"] == "北极星"
        ]
        assert len(matches) == 1


# ---------------------------------------------------------------------------
# 开关关闭 / 无 sink
# ---------------------------------------------------------------------------


class TestSwitchOff:
    def test_no_sink_zero_dependency(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LINGCLAUDE_MEMORY_DUALWRITE", raising=False)
        store = _make_store(tmp_path)
        store.put_memory(CognitiveMemory(key="k", value="v", importance=1))
        assert store.get_memory is not None  # 主路正常
        dbs = list(tmp_path.glob("lingmemory*"))
        assert dbs == []  # 灵忆库根本没被创建

    def test_switch_off_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "0")
        db = _make_db(tmp_path, name="lm_off.db")
        sink = LingMemoryL7Sink(db_path=str(db))
        store = _make_store(tmp_path, sink, name="l7_off.db")
        store.put_memory(CognitiveMemory(key="k", value="v", importance=1))
        assert len(_lm_items(db, origin="memory")) == 0


# ---------------------------------------------------------------------------
# 旁路纪律
# ---------------------------------------------------------------------------


class _BombSink:
    """任何事件都爆炸的 sink"""

    def __getattr__(self, name):
        def _boom(*args, **kwargs):
            raise RuntimeError("旁路爆炸")
        return _boom


class _SpyThenReenter:
    """首次 on_memory 时重入主路 put_memory（测 _in_sink_emit 递归卫兵）"""

    def __init__(self, store):
        self.store = store
        self.calls = 0

    def on_memory(self, payload):
        self.calls += 1
        if self.calls == 1:
            self.store.put_memory(
                CognitiveMemory(key="递归写", value="x", importance=1)
            )


class TestBypassDiscipline:
    def test_sink_bomb_no_effect_on_main_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
        store = _make_store(tmp_path, _BombSink(), name="l7_bomb.db")
        mem = CognitiveMemory(key="k", value="v", importance=1)
        rid = store.put_memory(mem)
        assert rid == mem.id  # 主路无感知
        assert store.get_memory(mem.id) is not None

    def test_no_recurse_via_reentrancy_guard(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LINGCLAUDE_MEMORY_DUALWRITE", raising=False)
        store = _make_store(tmp_path, name="l7_recurse.db")
        spy = _SpyThenReenter(store)
        store._legacy_sink = spy
        mem = CognitiveMemory(key="kr", value="vr", importance=1)
        store.put_memory(mem)
        assert spy.calls == 1  # 自指卫兵：递归 put 未再触发 on_memory


# ---------------------------------------------------------------------------
# 熔断
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_first_error_trips_and_stops(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
        db = _make_db(tmp_path, name="lm_trip.db")
        sink = LingMemoryL7Sink(db_path=str(db))

        def _explode(anchor):
            raise RuntimeError("灵忆侧查询爆炸")

        sink._lookup_active = _explode
        store = _make_store(tmp_path, sink, name="l7_trip.db")
        store.put_memory(CognitiveMemory(key="k", value="v", importance=1))
        assert sink._broken is True  # 首错熔断
        assert sink._record_ids == {}
        store.put_memory(CognitiveMemory(key="k2", value="v2", importance=1))
        assert sink._record_ids == {}  # 熔断后不再重试
        assert len(_lm_items(db)) == 0  # 灵忆侧零写入
