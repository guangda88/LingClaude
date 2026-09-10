"""P2.a wiring manifest 契约测试 — 灵元 1.0 地基验收。

冻结当前 QueryEngine.__init__ 的 55 项装配面：
- manifest 与真实 __init__ 完全一致（P2.b 替换装配段时的回归网）
- assemble() 装配裸引擎（__new__ 绕过 __init__）结果与真实引擎逐属性对齐
- 顺序无关 + 无重复条目 + phase 分类守恒
"""
from __future__ import annotations

import random

import pytest

from lingclaude.core.wiring import (
    WIRING_MANIFEST,
    WiringContext,
    assemble,
)

# 冻结的装配面：从当前 __init__ 逐项抄录，P2.b 起作为唯一权威清单
EXPECTED_ATTRS = {
    # state (25)
    "_messages", "_conversation", "_denials", "_transcript",
    "_project_index", "_model_config", "_journal_dir", "_model_router",
    "_intel_relay", "_session_history_path", "_mcp_initialized",
    "_active_checkpoint", "_session_cache_hits", "_tool_call_count",
    "_total_messages_sent", "_l1_last_triggered_at", "_l1_handover_checksum",
    "_degradation_alerts", "_memory_engine", "_l5_orchestrator",
    "_pinned_model_config", "_pinned_model_expires", "_mv1_violations",
    "_usage", "_write_lock",
    # collaborator (28)
    "_behavior", "_intel_collector", "_session_persister", "_session_runtime",
    "_router", "_task_router", "_tool_router", "_cache", "_aggregator",
    "_monitor", "_prior_verifier", "_meta_cognition", "_layered_memory",
    "_dementia_detector", "_cognitive_rhythm", "_hooks",
    "_degradation_detector", "_task_manager", "_skill_index", "_role_checker",
    "_l5_loop", "_notifier",
    "session_store", "model_adapter", "audit_collector", "model_request_log",
    "_tool_executor", "_tool_call_executor",
    # parameterized (2)
    "_provider", "_runtime",
}


def _bare_engine():
    """裸引擎：绕过 __init__，手工补两个装配前置依赖。"""
    from lingclaude.core.query_engine import QueryEngine
    from lingclaude.core.session import SessionManager

    engine = QueryEngine.__new__(QueryEngine)
    engine.session_manager = SessionManager()
    engine.session_id = "p2a_wiring_test"
    return engine


def _make_ctx(engine):
    return WiringContext(
        engine=engine,
        session_manager=engine.session_manager,
        provider=object(),  # 哨兵 provider：装配后可断言身份
        runtime=None,
    )


class TestManifestIntegrity:
    def test_manifest_covers_exactly_55(self):
        assert len(WIRING_MANIFEST) == 55

    def test_manifest_attrs_match_frozen_set(self):
        attrs = {spec.attr for spec in WIRING_MANIFEST}
        assert attrs == EXPECTED_ATTRS

    def test_no_duplicate_attrs(self):
        attrs = [spec.attr for spec in WIRING_MANIFEST]
        assert len(attrs) == len(set(attrs)), "manifest 存在重复条目"

    def test_phase_vocabulary_conserved(self):
        counts = {"state": 25, "collaborator": 28, "parameterized": 2}
        actual: dict[str, int] = {}
        for spec in WIRING_MANIFEST:
            assert spec.phase in counts, f"未知 phase: {spec.phase}"
            actual[spec.phase] = actual.get(spec.phase, 0) + 1
        assert actual == counts

    def test_every_spec_has_note(self):
        for spec in WIRING_MANIFEST:
            assert spec.note.strip(), f"{spec.attr} 缺迁移依据 note"


class TestAssembleSemantics:
    def test_assemble_bare_engine_all_attrs_present(self):
        engine = _bare_engine()
        wired = assemble(_make_ctx(engine))
        assert len(wired) == 55
        for attr in EXPECTED_ATTRS:
            assert hasattr(engine, attr), f"装配后缺 {attr}"

    def test_provider_identity_injected(self):
        engine = _bare_engine()
        sentinel = object()
        assemble(WiringContext(engine=engine, session_manager=engine.session_manager,
                               provider=sentinel, runtime=None))
        assert engine._provider is sentinel

    def test_assemble_order_free(self):
        """乱序装配结果一致 — 顺序无关不变式。"""
        e1, e2 = _bare_engine(), _bare_engine()
        assemble(_make_ctx(e1))
        shuffled = list(WIRING_MANIFEST)
        random.Random(42).shuffle(shuffled)
        assemble(_make_ctx(e2), manifest=tuple(shuffled))
        for spec in WIRING_MANIFEST:
            a, b = getattr(e1, spec.attr, None), getattr(e2, spec.attr, None)
            if spec.phase == "state" and isinstance(a, (int, float, str, bool, type(None))):
                assert a == b, f"乱序改变 {spec.attr} 初始值"

    def test_state_literals_match_original_init(self):
        engine = _bare_engine()
        assemble(_make_ctx(engine))
        assert engine._mcp_initialized is False
        assert engine._tool_call_count == 0
        assert engine._l1_last_triggered_at == -1
        assert engine._pinned_model_expires == 0.0
        assert engine._messages == [] and engine._denials == []
        assert engine._memory_engine is None
        from lingclaude.core.models import UsageSummary
        assert isinstance(engine._usage, UsageSummary)


    def test_overrides_inject_skips_factory(self):
        """P2.c seam: overrides 命中的属性直接注入，工厂不执行（无 patch 接缝）。"""
        engine = _bare_engine()
        sentinel = object()
        wired = assemble(_make_ctx(engine), overrides={"audit_collector": sentinel})
        assert engine.audit_collector is sentinel
        assert "audit_collector" in wired  # 仍在装配报告内
        assert "session_store" in wired    # 未 override 的条目照常装配

    def test_overrides_none_keeps_default_semantics(self):
        """overrides=None（默认）与 P2.b 覆写语义完全一致。"""
        engine = _bare_engine()
        wired = assemble(_make_ctx(engine))
        assert engine.audit_collector is not None
        assert len(wired) == 55

class TestParityWithRealEngine:
    @pytest.fixture()
    def real_engine(self):
        from lingclaude.core.query_engine import QueryEngine

        return QueryEngine()

    def test_every_manifest_attr_exists_on_real_engine(self, real_engine):
        for spec in WIRING_MANIFEST:
            assert hasattr(real_engine, spec.attr), (
                f"manifest 条目 {spec.attr} 在真实 QueryEngine 上不存在 —— "
                "__init__ 与 manifest 漂移，先修 __init__ 再动 manifest"
            )

    def test_collaborator_types_parity(self, real_engine):
        """裸引擎装配的协作者与真实引擎同类（类型对齐抽查）。"""
        from lingclaude.core.session_store import SessionStore
        from lingclaude.engine.tool_router import ToolRouter

        bare = _bare_engine()
        assemble(_make_ctx(bare))
        assert isinstance(bare.session_store, SessionStore)
        assert type(bare._tool_router) is type(real_engine._tool_router)
        assert type(bare._hooks) is type(real_engine._hooks)

    def test_tool_router_functional_parity(self, real_engine):
        """装配出的 tool_router 与真实引擎行为等价（同名工具可达）。"""
        bare = _bare_engine()
        assemble(_make_ctx(bare))
        real_names = {m.name for m in real_engine._tool_router.list_manifests()}
        bare_names = {m.name for m in bare._tool_router.list_manifests()}
        assert real_names == bare_names, "tool_router 装配行为与真实引擎漂移"
