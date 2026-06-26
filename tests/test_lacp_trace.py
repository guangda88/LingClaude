"""LACP v0.4.0 trace emitter tests.

10 个测试覆盖:
1-5. v0.2.0 / v0.3.0 兼容测试
6-7. v0.3.0 cost + caller_chain 校验
8. v0.4.0 NEW: human_context 字段校验
9. v0.4.0 NEW: INTUITIVE / UNVERIFIED outcome 状态
10. v0.4.0 NEW: human_context confidence ∈ [0,1] 边界

按灵元 1.0 测试范式: 写代码 + 同时生成测试 (auto-run 模式)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from lingclaude.lacp import (
    ActorRole,
    Cost,
    HumanContext,
    InMemoryBackend,
    JsonlFileBackend,
    Outcome,
    Phase,
    SCHEMA_VERSION,
    Trace,
    TraceEmitter,
    validate_trace,
)


def test_emit_success():
    """T1: emit 合法 trace → backend 收到."""
    backend = InMemoryBackend()
    emitter = TraceEmitter(backend)
    trace = emitter.emit(
        phase=Phase.EXECUTE,
        actor="lingclaude",
        actor_role=ActorRole.MEMBER,
        executor="audit_scanner@1.0.0",
        outcome=Outcome.PASS,
        context_ref=".ling/content/audit-001",
        duration_ms=234,
        target_plugin="audit_scanner@1.0.0",
    )
    assert emitter.count == 1
    stored = backend.traces[0]
    assert stored["schema_version"] == SCHEMA_VERSION
    assert stored["actor_role"] == "member"


def test_validate_accepts_legal_trace_with_caller_chain():
    """T2: validate 接受合法 trace (含 caller_chain)."""
    trace = Trace(
        phase=Phase.VERIFY,
        actor="lingflow",
        actor_role=ActorRole.SCHEDULER,
        executor="proxy21-scheduler@2.3.0",
        outcome=Outcome.PASS,
        context_ref=".ling/content/x",
        duration_ms=12,
        caller_chain=["lingflow", "proxy21"],
    )
    ok, err = validate_trace(trace)
    assert ok, f"validate should pass: {err}"


def test_validate_rejects_missing_context_ref():
    """T3: validate 拒绝缺 context_ref."""
    trace = Trace(
        phase=Phase.EXECUTE,
        actor="lingclaude",
        actor_role=ActorRole.MEMBER,
        executor="x@1",
        outcome=Outcome.PASS,
        context_ref="",
        duration_ms=10,
    )
    ok, err = validate_trace(trace)
    assert not ok
    assert "context_ref" in err.lower()


def test_validate_rejects_bad_schema_version():
    """T4: validate 拒绝 schema_version 不匹配."""
    trace_dict = {
        "schema_version": "0.3.0",  # v0.4.0 应拒绝
        "phase": "execute", "actor": "lingclaude", "actor_role": "member",
        "executor": "x@1", "outcome": "pass", "context_ref": ".ling/content/x",
        "duration_ms": 10, "trace_id": "x", "caller_chain": [],
        "ts": "2026-06-27T00:00:00Z", "metadata": {}, "cost": None,
        "decision_id": None, "target_plugin": None, "actor_instance_id": None,
    }
    ok, err = validate_trace(trace_dict)
    assert not ok
    assert "schema_version" in err.lower()


def test_jsonl_backend_persists():
    """T5: JsonlFileBackend 持久化 + 读回."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "traces.jsonl"
        backend = JsonlFileBackend(path)
        emitter = TraceEmitter(backend)
        for i, (phase, outcome) in enumerate([
            (Phase.SCHEDULE, Outcome.PASS),
            (Phase.EXECUTE, Outcome.INTUITIVE),  # v0.4.0 NEW
            (Phase.VERIFY, Outcome.UNVERIFIED),  # v0.4.0 NEW
        ]):
            emitter.emit(
                phase=phase, actor="lingflow", actor_role=ActorRole.SCHEDULER,
                executor="test@1", outcome=outcome,
                context_ref=f".ling/content/c-{i}", duration_ms=i * 10,
            )
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        parsed = [json.loads(line) for line in lines]
        assert [p["outcome"] for p in parsed] == ["pass", "intuitive", "unverified"]


def test_cost_field_validation():
    """T6: cost 字段校验."""
    # OK: tokens+ms
    t1 = Trace(
        phase=Phase.EXECUTE, actor="lingclaude", actor_role=ActorRole.MEMBER,
        executor="x@1", outcome=Outcome.PASS, context_ref=".ling/content/x",
        duration_ms=10, cost=Cost(tokens=1234, ms=234),
    )
    ok1, _ = validate_trace(t1)
    assert ok1
    # REJECT: 全 None
    t2 = Trace(
        phase=Phase.EXECUTE, actor="lingclaude", actor_role=ActorRole.MEMBER,
        executor="x@1", outcome=Outcome.PASS, context_ref=".ling/content/x",
        duration_ms=10, cost=Cost(),
    )
    ok2, err2 = validate_trace(t2)
    assert not ok2
    assert "cost" in err2.lower()


def test_caller_chain_and_actor_role():
    """T7: caller_chain + actor_role + actor_instance_id 校验."""
    t = Trace(
        phase=Phase.EXECUTE, actor="lingflow", actor_role=ActorRole.DAEMON,
        executor="daemon@1", outcome=Outcome.PASS, context_ref=".ling/content/x",
        duration_ms=10, caller_chain=["user", "lingflow"],
        actor_instance_id="daemon-942bf747",
    )
    ok, _ = validate_trace(t)
    assert ok
    d = t.to_dict()
    assert d["caller_chain"] == ["user", "lingflow"]
    assert d["actor_instance_id"] == "daemon-942bf747"


def test_human_context_validation():
    """T8 (v0.4.0 NEW): human_context 字段校验.

    - 不设 human_context: OK (向后兼容)
    - 设置 human_context 但缺 intent: reject
    - 设置完整 human_context: OK
    """
    # OK: 不设
    t1 = Trace(
        phase=Phase.EXECUTE, actor="lingclaude", actor_role=ActorRole.MEMBER,
        executor="x@1", outcome=Outcome.PASS, context_ref=".ling/content/x",
        duration_ms=10,
    )
    ok1, _ = validate_trace(t1)
    assert ok1

    # OK: 完整 human_context
    t2 = Trace(
        phase=Phase.EXECUTE, actor="lingclaude", actor_role=ActorRole.MEMBER,
        executor="x@1", outcome=Outcome.PASS, context_ref=".ling/content/x",
        duration_ms=10,
        metadata={"human_context": HumanContext(
            intent="v0.4.0 schema 升级",
            turn=4,
            reasoning="用户反馈 LACP 过度'去人类化', 5 维度承载人类思维",
            alternatives_considered=["v0.3.0 维持", "v0.5.0 大改"],
            confidence=0.85,
        ).to_dict()},
    )
    ok2, _ = validate_trace(t2)
    assert ok2
    d2 = t2.to_dict()
    assert d2["metadata"]["human_context"]["intent"] == "v0.4.0 schema 升级"
    assert d2["metadata"]["human_context"]["confidence"] == 0.85

    # REJECT: 缺 intent
    t3 = Trace(
        phase=Phase.EXECUTE, actor="lingclaude", actor_role=ActorRole.MEMBER,
        executor="x@1", outcome=Outcome.PASS, context_ref=".ling/content/x",
        duration_ms=10,
        metadata={"human_context": {"turn": 4, "confidence": 0.5}},  # 缺 intent
    )
    ok3, err3 = validate_trace(t3)
    assert not ok3
    assert "intent" in err3.lower()


def test_intuitive_and_unverified_outcomes():
    """T9 (v0.4.0 NEW): INTUITIVE + UNVERIFIED outcome 状态.

    体现"直觉决策"和"非确定性接受"的人类思维模式.
    """
    # INTUITIVE outcome (我建议但还没验证)
    t1 = Trace(
        phase=Phase.DISTILL, actor="lingclaude", actor_role=ActorRole.MEMBER,
        executor="brain@1", outcome=Outcome.INTUITIVE,  # NEW
        context_ref=".ling/content/intuition-001",
        duration_ms=234,
        metadata={"human_context": HumanContext(
            intent="猜一下 v0.4.0 是否合理",
            confidence=0.6,  # 不高, 体现直觉
        ).to_dict()},
    )
    ok1, _ = validate_trace(t1)
    assert ok1
    assert t1.to_dict()["outcome"] == "intuitive"

    # UNVERIFIED outcome (接受不确定性)
    t2 = Trace(
        phase=Phase.VERIFY, actor="lingclaude", actor_role=ActorRole.MEMBER,
        executor="brain@1", outcome=Outcome.UNVERIFIED,  # NEW
        context_ref=".ling/content/unverified-001",
        duration_ms=10,
        metadata={"human_context": HumanContext(
            intent="先做后验证",
            reasoning="自治推进 - 假设性讨论 + 按计划执行",
            confidence=0.4,
        ).to_dict()},
    )
    ok2, _ = validate_trace(t2)
    assert ok2
    assert t2.to_dict()["outcome"] == "unverified"


def test_human_context_confidence_boundary():
    """T10 (v0.4.0 NEW): confidence ∈ [0, 1] 边界 + alternatives 类型校验."""
    # confidence=0.0 OK
    t1 = Trace(
        phase=Phase.EXECUTE, actor="lingclaude", actor_role=ActorRole.MEMBER,
        executor="x@1", outcome=Outcome.PASS, context_ref=".ling/content/x",
        duration_ms=10,
        metadata={"human_context": {"intent": "x", "confidence": 0.0}},
    )
    ok1, _ = validate_trace(t1)
    assert ok1

    # confidence=1.0 OK
    t2 = Trace(
        phase=Phase.EXECUTE, actor="lingclaude", actor_role=ActorRole.MEMBER,
        executor="x@1", outcome=Outcome.PASS, context_ref=".ling/content/x",
        duration_ms=10,
        metadata={"human_context": {"intent": "x", "confidence": 1.0}},
    )
    ok2, _ = validate_trace(t2)
    assert ok2

    # confidence=1.5 REJECT
    t3_dict = {
        "schema_version": SCHEMA_VERSION, "phase": "execute", "actor": "lingclaude",
        "actor_role": "member", "executor": "x@1", "outcome": "pass",
        "context_ref": ".ling/content/x", "duration_ms": 10, "trace_id": "x",
        "caller_chain": [], "ts": "2026-06-27T00:00:00Z",
        "metadata": {"human_context": {"intent": "x", "confidence": 1.5}},
        "cost": None, "decision_id": None, "target_plugin": None,
        "actor_instance_id": None,
    }
    ok3, err3 = validate_trace(t3_dict)
    assert not ok3
    assert "confidence" in err3.lower()

    # alternatives_considered 含非 string REJECT
    t4_dict = dict(t3_dict)
    t4_dict["metadata"]["human_context"] = {"intent": "x", "alternatives_considered": ["ok", 123]}
    ok4, err4 = validate_trace(t4_dict)
    assert not ok4
    assert "alternatives" in err4.lower()