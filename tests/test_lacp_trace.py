"""LACP v0.3.0 trace emitter tests.

7 个测试覆盖 (v0.3.0 增量):
1. emit 成功 + backend 接收 (含新字段)
2. validate 通过合法 trace
3. validate 拒绝缺 context_ref
4. validate 拒绝 schema_version 不匹配
5. JsonlFileBackend 持久化 + 读回
6. v0.3.0 NEW: cost 字段校验 (灵极优 R2)
7. v0.3.0 NEW: caller_chain + actor_role + actor_instance_id (灵研 R2)

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
    assert len(backend.traces) == 1
    stored = backend.traces[0]
    assert stored["trace_id"] == trace.trace_id
    assert stored["phase"] == "execute"
    assert stored["actor"] == "lingclaude"
    assert stored["actor_role"] == "member"  # enum → str
    assert stored["executor"] == "audit_scanner@1.0.0"
    assert stored["outcome"] == "pass"
    assert stored["context_ref"] == ".ling/content/audit-001"
    assert stored["schema_version"] == SCHEMA_VERSION
    assert stored["caller_chain"] == []  # 默认空


def test_validate_accepts_legal_trace():
    """T2: validate 接受合法 trace (含 caller_chain)."""
    trace = Trace(
        phase=Phase.VERIFY,
        actor="lingflow",
        actor_role=ActorRole.SCHEDULER,
        executor="proxy21-scheduler@2.3.0",
        outcome=Outcome.PASS,
        context_ref=".ling/content/scheduler-pick-123",
        duration_ms=12,
        caller_chain=["lingflow", "proxy21", "scheduler"],
    )
    ok, err = validate_trace(trace)
    assert ok, f"validate should pass: {err}"


def test_validate_rejects_missing_context_ref():
    """T3: validate 拒绝缺 context_ref (CDA 不脱钩)."""
    trace = Trace(
        phase=Phase.EXECUTE,
        actor="lingclaude",
        actor_role=ActorRole.MEMBER,
        executor="x@1",
        outcome=Outcome.PASS,
        context_ref="",  # 故意空
        duration_ms=10,
    )
    ok, err = validate_trace(trace)
    assert not ok
    assert "context_ref" in err.lower()


def test_validate_rejects_bad_schema_version():
    """T4: validate 拒绝 schema_version 不匹配."""
    trace_dict = {
        "schema_version": "0.2.0",  # v0.3.0 应拒绝旧版本
        "phase": "execute",
        "actor": "lingclaude",
        "actor_role": "member",
        "executor": "x@1",
        "outcome": "pass",
        "context_ref": ".ling/content/x",
        "duration_ms": 10,
        "trace_id": "x",
        "caller_chain": [],
        "ts": "2026-06-27T00:00:00Z",
        "metadata": {},
        "cost": None,
        "decision_id": None,
        "target_plugin": None,
        "actor_instance_id": None,
    }
    ok, err = validate_trace(trace_dict)
    assert not ok
    assert "schema_version" in err.lower()


def test_jsonl_backend_persists_and_reads_back():
    """T5: JsonlFileBackend 持久化 + 读回内容一致."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "traces.jsonl"
        backend = JsonlFileBackend(path)
        emitter = TraceEmitter(backend)

        # emit 3 个不同 phase
        for i, (phase, outcome) in enumerate([
            (Phase.SCHEDULE, Outcome.PASS),
            (Phase.EXECUTE, Outcome.FAIL),
            (Phase.VERIFY, Outcome.DRIFT),
        ]):
            emitter.emit(
                phase=phase,
                actor="lingflow",
                actor_role=ActorRole.SCHEDULER,
                executor="test@1",
                outcome=outcome,
                context_ref=f".ling/content/case-{i}",
                duration_ms=i * 10,
            )

        # 读回
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

        # 验证每行是合法 JSON + phase 正确
        parsed = [json.loads(line) for line in lines]
        assert [p["phase"] for p in parsed] == ["schedule", "execute", "verify"]
        assert [p["outcome"] for p in parsed] == ["pass", "fail", "drift"]
        assert [p["actor_role"] for p in parsed] == ["scheduler", "scheduler", "scheduler"]
        assert all(p["schema_version"] == SCHEMA_VERSION for p in parsed)


def test_cost_field_validation():
    """T6 (v0.3.0 NEW): cost 字段校验 (灵极优 R2).

    - 不设 cost: OK (可选)
    - 设置但全 None: reject
    - 设置且至少一个非 None: OK
    """
    # OK: 不设 cost
    t1 = Trace(
        phase=Phase.EXECUTE,
        actor="lingclaude",
        actor_role=ActorRole.MEMBER,
        executor="x@1",
        outcome=Outcome.PASS,
        context_ref=".ling/content/x",
        duration_ms=10,
    )
    ok1, _ = validate_trace(t1)
    assert ok1

    # OK: 至少一个非 None
    t2 = Trace(
        phase=Phase.EXECUTE,
        actor="lingclaude",
        actor_role=ActorRole.MEMBER,
        executor="x@1",
        outcome=Outcome.PASS,
        context_ref=".ling/content/x",
        duration_ms=10,
        cost=Cost(tokens=1234, ms=234),
    )
    ok2, _ = validate_trace(t2)
    assert ok2
    # dict 转换正确
    assert t2.to_dict()["cost"] == {"tokens": 1234, "usd": None, "ms": 234}

    # REJECT: 全 None
    t3 = Trace(
        phase=Phase.EXECUTE,
        actor="lingclaude",
        actor_role=ActorRole.MEMBER,
        executor="x@1",
        outcome=Outcome.PASS,
        context_ref=".ling/content/x",
        duration_ms=10,
        cost=Cost(),  # 全 None
    )
    ok3, err3 = validate_trace(t3)
    assert not ok3
    assert "cost" in err3.lower()


def test_caller_chain_and_actor_role_validation():
    """T7 (v0.3.0 NEW): caller_chain + actor_role + actor_instance_id (灵研 R2).

    - caller_chain 默认 []: OK
    - caller_chain list of strings: OK
    - caller_chain 含非 string: reject
    - actor_role 必填 enum: 缺/错都 reject
    - actor_instance_id 可选
    """
    # OK: 默认 caller_chain=[]
    t1 = Trace(
        phase=Phase.EXECUTE,
        actor="lingflow",
        actor_role=ActorRole.DAEMON,
        executor="lingflow_plus@1.0",
        outcome=Outcome.PASS,
        context_ref=".ling/content/x",
        duration_ms=10,
    )
    ok1, _ = validate_trace(t1)
    assert ok1
    assert t1.to_dict()["caller_chain"] == []
    assert t1.to_dict()["actor_role"] == "daemon"

    # OK: caller_chain 完整 + actor_instance_id
    t2 = Trace(
        phase=Phase.EXECUTE,
        actor="lingclaude",
        actor_role=ActorRole.MEMBER,
        executor="crush-cli@0.79.1",
        outcome=Outcome.PASS,
        context_ref=".ling/content/x",
        duration_ms=10,
        caller_chain=["user", "lingclaude"],
        actor_instance_id="session-942bf747@crush-daemon",
    )
    ok2, _ = validate_trace(t2)
    assert ok2
    d2 = t2.to_dict()
    assert d2["caller_chain"] == ["user", "lingclaude"]
    assert d2["actor_instance_id"] == "session-942bf747@crush-daemon"

    # REJECT: caller_chain 含非 string
    t3_dict = {
        "schema_version": SCHEMA_VERSION,
        "phase": "execute",
        "actor": "lingclaude",
        "actor_role": "member",
        "executor": "x@1",
        "outcome": "pass",
        "context_ref": ".ling/content/x",
        "duration_ms": 10,
        "trace_id": "x",
        "caller_chain": ["lingclaude", 123],  # 故意错
        "ts": "2026-06-27T00:00:00Z",
        "metadata": {},
        "cost": None,
        "decision_id": None,
        "target_plugin": None,
        "actor_instance_id": None,
    }
    ok3, err3 = validate_trace(t3_dict)
    assert not ok3
    assert "caller_chain" in err3.lower()

    # REJECT: actor_role 错
    t4_dict = dict(t3_dict, caller_chain=[], actor_role="god_mode")
    ok4, err4 = validate_trace(t4_dict)
    assert not ok4
    assert "actor_role" in err4.lower()