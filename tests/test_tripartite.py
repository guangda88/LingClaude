"""Tripartite cycle health gauge tests（ERR-04 重建件——全部断言走真实 record 数据）。"""
import json

from lingclaude.engine import tripartite as tp


def test_cycle_record_has_tripartite_attribution():
    cyc = tp.CycleRecord(cycle_id="t", as_of="2026-09-25",
                         identified_by="a", enforced_by="b", corrected_by="c")
    assert cyc.identified_by and cyc.enforced_by and cyc.corrected_by


def test_collect_cycle001_inputs_uses_real_records():
    inputs = tp.collect_cycle001_inputs()
    items = {c["item"] for c in inputs["candidates"]}
    assert "SeamType.TRANSPORT" in items           # 4b9149a 真实撤缝 record（缝名原样保留）
    assert "event_exempt" in items                 # d36e5b9 真实棘轮迁移（裸名/全路径归一后合一）
    report = inputs["cycle_report"]
    # 去重防双计：ratchet(裸名) 与 exemption(core/X.py) 描述同 4 件，归并后 migrated 恰为 4；
    # 9666f90 棘轮二格五桥销账 → migrated 4+5=9、observing 102→97（2026-09-25 账本演进实测）
    assert report["migrated"] == 9
    # removed 状态是回收不是 pending（l5_conversation_loop/loop_seam 实测 state=removed）
    assert report["recycled"] == 3
    # 通用载体文件名归一防碰撞：五桥 file=plugins/memory/<桥>/bridge.py 不得并成一个 bridge
    assert {"lingmemory_bridge", "lingmemory_l7_bridge", "lingmemory_memstore_bridge",
            "lingmemory_token_bridge", "lingmemory_experience_bridge"} <= items


def test_recycle_tier_and_fitness_and_drift():
    """借鉴①②④：档位透传 + fitness 互证 + 漂移观测（全部真实 record 数据）。"""
    inputs = tp.collect_cycle001_inputs()
    trans = next(c for c in inputs["candidates"] if c["item"] == "SeamType.TRANSPORT")
    # TRANSPORT record 带 revival_clause → 轻回收（light）
    assert trans.get("tier") == "light"
    # fitness 互证：逐候选求和 == cycle_report 加权期望（防口径漂移）
    fit = tp.fitness_summary(inputs["candidates"], inputs["cycle_report"])
    assert fit["mismatch"] is False
    assert fit["score"] == fit["expected_from_report"] > 0
    # 单候选分数：migrated=1.0 / observing=0.3（FITNESS_WEIGHTS 语义锚）
    assert tp.fitness_for_candidate({"disposition": "migrated"}) == 1.0
    assert tp.fitness_for_candidate({"disposition": "observing"}) == 0.3
    # 漂移观测：真实快照对（arch_m6_snapshot）可算 l1 距离，不足两份则如实 observed=False
    drift = tp.task_type_drift()
    assert drift["observed"] is True and "l1_distance" in drift
    assert drift["reset_recommended"] == (drift["l1_distance"] > drift["threshold"])
    m = tp.snap_tripartite_health()
    assert m.fitness["mismatch"] is False and m.task_drift["observed"] is True


def test_health_metrics_fresh_and_honest():
    m = tp.snap_tripartite_health()
    assert m.freshness_hours < tp.STALE_AFTER_HOURS
    assert sum(m.cycle_report.values()) >= 100
    # 全部候选有真实归宿（observing=带期限豁免观察中，是合法归宿）→ 无消失候选 → 覆盖率 1.0
    assert m.cycle_report["pending"] == 0
    assert m.disposition_coverage == 1.0
    assert not any("coverage" in f for f in m.red_flags)


def test_pending_candidate_trips_red():
    # 语义回归：出现无归宿候选时仪表必须亮红（防"候选消失即红"逻辑被改哑）
    from lingclaude.engine.tripartite import MetricsRecord
    m = MetricsRecord(freshness_hours=1.0, disposition_coverage=0.5,
                      cycle_report={"recycled": 1, "migrated": 0, "observing": 0,
                                    "reverted": 0, "pending": 1}, red_flags=[])
    assert m.disposition_coverage < 1.0


def test_save_tripartite_cycle_roundtrip(tmp_path):
    out = tp.save_tripartite_cycle(out_dir=tmp_path)
    assert out.exists()
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["type"] == "arch_tripartite_cycle"
    assert d["key"] == "cycle-001"
    cyc = d["cycle"]
    assert all(cyc[k] for k in ("identified_by", "enforced_by", "corrected_by"))
    assert d["metrics"]["cycle_report"] == cyc["cycle_report"]
