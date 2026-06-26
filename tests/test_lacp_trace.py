"""LACP v0.2.0-ready trace emitter tests.

5 个测试覆盖:
1. emit 成功 + backend 接收
2. validate 通过合法 trace
3. validate 拒绝缺 context_ref
4. validate 拒绝 schema_version 不匹配
5. JsonlFileBackend 持久化 + 读回

按灵元 1.0 测试范式: 写代码 + 同时生成测试 (auto-run 模式)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from lingclaude.lacp import (
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
    assert stored["executor"] == "audit_scanner@1.0.0"
    assert stored["outcome"] == "pass"
    assert stored["context_ref"] == ".ling/content/audit-001"
    assert stored["schema_version"] == SCHEMA_VERSION


def test_validate_accepts_legal_trace():
    """T2: validate 接受合法 trace."""
    trace = Trace(
        phase=Phase.VERIFY,
        actor="lingflow",
        executor="proxy21-scheduler@2.3.0",
        outcome=Outcome.PASS,
        context_ref=".ling/content/scheduler-pick-123",
        duration_ms=12,
    )
    ok, err = validate_trace(trace)
    assert ok, f"validate should pass: {err}"


def test_validate_rejects_missing_context_ref():
    """T3: validate 拒绝缺 context_ref (CDA 不脱钩)."""
    trace = Trace(
        phase=Phase.EXECUTE,
        actor="lingclaude",
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
        "schema_version": "0.0.1-bogus",  # 故意错
        "phase": "execute",
        "actor": "lingclaude",
        "executor": "x@1",
        "outcome": "pass",
        "context_ref": ".ling/content/x",
        "duration_ms": 10,
        "ts": "2026-06-27T00:00:00Z",
        "trace_id": "x",
        "metadata": {},
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
        assert all(p["schema_version"] == SCHEMA_VERSION for p in parsed)