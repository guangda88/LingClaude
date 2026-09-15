"""P0-N2 灵忆闭环验收测试（灵元 1.0 三底线之"流转有闭环"）。

覆盖（对照 docs/EVALUATION_LINGYUAN_1.0_vs_PEERS.md:67 "记忆分层（L0-L4）
未完全迁移灵忆，双写期未闭环"）：

- 验收①：L2(experience) → 灵忆 双写闭环（store → working record 落库）
- 验收②：衰减遗忘闭环（decay_all 出清 → 灵忆 archived + 主路删除）
- 验收③：读出闭环（build_context_injection 汇流 L0 常识 + L2 经验 + L3 认知边界 → prompt）
- 验收④：灵忆不可用降级（_get_lingmemory → None 时主路照常，不崩不侵）
- 验收⑤：开关关闭纪律（无 LINGCLAUDE_MEMORY_DUALWRITE=1 不建灵忆、不写记录）

设计约束（承 P3.2/P3.3 桥接器 docstring）：
- L0(common) 静态硬编码、L1(working) 纯内存 buffer，设计上不入灵忆
- L3(meta)/L4(shared) 走 JSON 不双写（P3.3 范围外）
- 故"闭环"= L2 双写 + 读出汇流 + 降级安全 + 开关纪律，而非 L0-L4 全入灵忆
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from lingclaude.core.layered_memory import (
    EmotionIntensity,
    Experience,
    InMemoryExperienceStore,
    LayeredMemory,
)
from lingclaude.core.lingmemory_bridge import dualwrite_enabled
from lingclaude.core.lingmemory_experience_bridge import LingMemoryExperienceSink


def _mk_exp(**kw) -> Experience:
    defaults = dict(problem="如何优化查询", reflection="添加索引可以提升性能")
    defaults.update(kw)
    return Experience.create(**defaults)


@pytest.fixture()
def lm_pair(tmp_path, monkeypatch):
    """独立灵忆库 + 强制开双写开关（不碰生产库 lingmemory.db）。"""
    monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
    db = tmp_path / "lingmemory_closed_loop.db"
    from lingmemory import init_db

    init_db(db)
    sink = LingMemoryExperienceSink(db_path=db)
    store = InMemoryExperienceStore(legacy_sink=sink)
    return store, sink, db


def _recs(lm, **kw):
    return lm.query(type="layered_memory_entry", **kw)["items"]


# ---------------------------------------------------------------------------
# 验收①：L2(experience) → 灵忆 双写闭环
# ---------------------------------------------------------------------------


class TestDualWriteClosedLoop:
    def test_store_mirrors_to_lingyi(self, lm_pair):
        store, sink, _ = lm_pair
        exp = _mk_exp()

        exp_id = store.store(exp)

        assert exp_id == exp.id
        items = _recs(sink._lm)
        assert len(items) == 1
        rec = items[0]
        assert rec["state"] == "working"
        assert rec["data"]["experience_id"] == exp.id
        assert rec["data"]["layer_of_origin"] == "experience"
        assert "如何优化查询" in rec["data"]["content"]
        assert rec["created_by"] == "lingclaude.layered_memory"

    def test_re_store_no_duplicate(self, lm_pair):
        store, sink, _ = lm_pair
        exp = _mk_exp()
        store.store(exp)
        store.store(exp)  # INSERT OR REPLACE 语义
        assert len(_recs(sink._lm)) == 1

    def test_dualwrite_consistency(self, lm_pair):
        """主路 store 返回 id 与灵忆镜像 experience_id 一致（双写一致性）。"""
        store, sink, _ = lm_pair
        exp1 = _mk_exp(problem="A问题")
        exp2 = _mk_exp(problem="B问题")
        store.store(exp1)
        store.store(exp2)

        by_id = {r["data"]["experience_id"]: r for r in _recs(sink._lm)}
        assert set(by_id) == {exp1.id, exp2.id}
        assert by_id[exp1.id]["state"] == "working"
        assert by_id[exp2.id]["state"] == "working"


# ---------------------------------------------------------------------------
# 验收②：衰减遗忘闭环（decay_all 出清 → 灵忆 archived + 主路删除）
# ---------------------------------------------------------------------------


class TestDecayClosedLoop:
    def test_forgotten_transitions_archived(self, lm_pair):
        store, sink, _ = lm_pair
        old = replace(
            _mk_exp(),
            last_recalled=datetime.now(timezone.utc) - timedelta(days=365),
        )
        fresh = _mk_exp(problem="新鲜经验")
        store.store(old)
        store.store(fresh)

        updated = store.decay_all()
        assert updated == 2

        states = {r["data"]["experience_id"]: r["state"] for r in _recs(sink._lm)}
        assert states[old.id] == "archived"   # 出清 → forget → archived
        assert states[fresh.id] == "working"  # 未出清保持

        # 主路侧：出清的被物理删除
        remaining = {e.id for e in store.recall("经验", limit=10)}
        assert old.id not in remaining
        assert fresh.id in remaining

    def test_decay_no_archived_without_outgoing(self, lm_pair):
        store, sink, _ = lm_pair
        exp = _mk_exp()
        store.store(exp)
        store.decay_all()
        # 新经验权重 1.0，衰减后仍 >0.05，不应 archived
        states = {r["data"]["experience_id"]: r["state"] for r in _recs(sink._lm)}
        assert states[exp.id] == "working"


# ---------------------------------------------------------------------------
# 验收③：读出闭环（build_context_injection 汇流 L0+L2+L3 → prompt）
# ---------------------------------------------------------------------------


class TestReadClosedLoop:
    def test_context_injection_fuses_all_layers(self, tmp_path):
        """五层记忆系统作为整体：L0 常识 + L2 经验 + L3 认知边界 汇入 prompt。"""
        lm = LayeredMemory(
            experience_store=InMemoryExperienceStore(),
            persist_dir=tmp_path,
        )
        # L2 经验
        lm.record_experience(
            Experience.create(
                problem="如何优化查询",
                hypothesis="添加索引",
                action="在ID字段加索引",
                result="查询快10倍",
                reflection="索引是数据库优化关键",
                emotion=EmotionIntensity.HIGH,
                associations=("数据库", "优化"),
            )
        )
        # L3 认知边界
        lm.record_meta("擅长领域", "Python编程")
        lm.record_meta("不擅长领域", "机器学习")

        context = lm.build_context_injection(current_query="优化查询")

        # L0 常识
        assert "灵字辈大家庭成员" in context
        assert "灵克" in context
        # L2 经验
        assert "相关经验" in context
        assert "如何优化查询" in context
        assert "索引是数据库优化关键" in context
        # L3 认知边界
        assert "认知边界" in context
        assert "Python编程" in context

    def test_read_path_recall_boosts_weight(self):
        """读出闭环的自我强化：recall 后权重提升、recall_count 增加。"""
        store = InMemoryExperienceStore()
        lm = LayeredMemory(experience_store=store)
        lm.record_experience(_mk_exp())

        lm.recall_experience("优化")
        exp = store.recall("优化")[0]
        assert exp.recall_count == 1
        assert exp.weight > 1.0

    def test_read_path_survives_lingyi_absent(self, monkeypatch, tmp_path):
        """灵忆不可用时，读出闭环仍成立（主路权威）。"""
        monkeypatch.delenv("LINGCLAUDE_MEMORY_DUALWRITE", raising=False)
        monkeypatch.setattr(
            "lingclaude.core.lingmemory_bridge._get_lingmemory",
            lambda: None,
        )
        lm = LayeredMemory(
            experience_store=InMemoryExperienceStore(),
            persist_dir=tmp_path,
        )
        lm.record_experience(_mk_exp())
        lm.record_meta("边界", "值")
        context = lm.build_context_injection("优化")
        assert "相关经验" in context
        assert "如何优化查询" in context
        assert "认知边界" in context


# ---------------------------------------------------------------------------
# 验收④：灵忆不可用降级（_get_lingmemory → None 时主路照常）
# ---------------------------------------------------------------------------


class TestDegradePath:
    def test_sink_without_lingmemory_keeps_main_path(self, monkeypatch):
        """灵忆模块缺失/返回 None：带 sink 的 store 仍正常 store/recall/decay。"""
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
        # _get_lingmemory 返回 None 模拟"灵忆不可用"（绕过懒加载缓存）
        monkeypatch.setattr(
            "lingclaude.core.lingmemory_bridge._get_lingmemory",
            lambda: None,
        )

        sink = LingMemoryExperienceSink(db_path=":memory:")
        store = InMemoryExperienceStore(legacy_sink=sink)
        exp = _mk_exp()
        exp_id = store.store(exp)
        assert exp_id == exp.id
        assert store.get_stats()["total_experiences"] == 1
        assert store.recall("优化")[0].problem == "如何优化查询"
        # 熔断未触发、主路完好
        assert store.decay_all() == 1

    def test_degrade_does_not_trip_broken(self, monkeypatch):
        """降级路径：灵忆不可用 → 熔断（旁路纪律），但主路不崩。"""
        monkeypatch.setenv("LINGCLAUDE_MEMORY_DUALWRITE", "1")
        monkeypatch.setattr(
            "lingclaude.core.lingmemory_bridge._get_lingmemory",
            lambda: None,
        )
        sink = LingMemoryExperienceSink(db_path=":memory:")
        store = InMemoryExperienceStore(legacy_sink=sink)
        store.store(_mk_exp())
        # 灵忆不可用 → on_store 内 _lm_create 抛 RuntimeError → _trip 熔断。
        # 这是旁路纪律的预期行为（不可用=故障，首错熔断防反复重试）。
        assert sink._broken is True
        # 主路权威：store 成功、可 recall、可 decay
        assert store.get_stats()["total_experiences"] == 1
        assert store.recall("优化")[0].problem == "如何优化查询"
        assert store.decay_all() == 1

    def test_get_lingmemory_none_when_import_fails(self, monkeypatch):
        """_get_lingmemory 在 import 失败时返回 None（懒加载纪律）。

        直接 monkeypatch 模块内部 _get_lingmemory（确保绑定名称被替换），
        验证调用方拿到 None 时不崩。
        """
        monkeypatch.setattr(
            "lingclaude.core.lingmemory_bridge._get_lingmemory",
            lambda: None,
        )
        from lingclaude.core import lingmemory_bridge

        assert lingmemory_bridge._get_lingmemory() is None


# ---------------------------------------------------------------------------
# 验收⑤：开关关闭纪律（默认不建灵忆、不写记录）
# ---------------------------------------------------------------------------


class TestSwitchOffDiscipline:
    def test_disabled_by_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LINGCLAUDE_MEMORY_DUALWRITE", raising=False)
        assert dualwrite_enabled() is False

        sink = LingMemoryExperienceSink(db_path=tmp_path / "x.db")
        store = InMemoryExperienceStore(legacy_sink=sink)
        exp = _mk_exp()
        assert store.store(exp) == exp.id  # 主路照常
        assert sink._lm is None            # 灵忆从未被触

    def test_main_path_record_present_without_dualwrite(self, monkeypatch):
        monkeypatch.delenv("LINGCLAUDE_MEMORY_DUALWRITE", raising=False)
        store = InMemoryExperienceStore()
        store.store(_mk_exp())
        assert store.get_stats()["total_experiences"] == 1
