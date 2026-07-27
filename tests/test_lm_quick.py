"""Task 快捷标记单元测试 — D6 deliverable

依据：灵极优 docs/task_quick_mark_proposal.md v0.1 方案 ②
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path("/home/ai/lingclaude")
sys.path.insert(0, str(ROOT))


def _load_quick_with_stub(stub_transition=None, stub_query=None):
    """载入 lm_quick 模块并替换 MCP 调用为 stub。"""
    if "lingclaude.core.lm_quick" in sys.modules:
        del sys.modules["lingclaude.core.lm_quick"]
    if "ling_term_mcp" in sys.modules:
        del sys.modules["ling_term_mcp"]

    if stub_transition is not None or stub_query is not None:
        class _StubMCP:
            pass

        if stub_transition is not None:
            _StubMCP.lm_transition = lambda **kwargs: stub_transition(kwargs)
        if stub_query is not None:
            def _fake_query(**kwargs):
                return stub_query(kwargs)
            _StubMCP.lm_query = _fake_query
        sys.modules["ling_term_mcp"] = _StubMCP

    return importlib.import_module("lingclaude.core.lm_quick")


def test_lm_done_calls_transition() -> None:
    captured = []
    mod = _load_quick_with_stub(
        stub_transition=lambda kw: captured.append(kw) or {"ok": True}
    )

    result = mod.lm_done("task-001")

    assert result["status"] == "ok"
    assert result["task_id"] == "task-001"
    assert result["event_type"] == "completed"
    assert "gate_id" in result
    assert len(captured) == 1
    call = captured[0]
    assert call["record_id"] == "task-001"
    assert call["event_type"] == "completed"
    assert "lm_quick" in call["data"]


def test_lm_block_writes_reason() -> None:
    captured = []
    mod = _load_quick_with_stub(
        stub_transition=lambda kw: captured.append(kw) or {"ok": True}
    )

    result = mod.lm_block("task-002", "model rebuild stuck on shard #1")

    assert result["status"] == "ok"
    assert result["event_type"] == "blocked"
    gate = captured[0]["data"]["lm_quick"]
    assert gate["evidence_payload"]["evidence"]["block_reason"] == "model rebuild stuck on shard #1"
    assert "block_ts" in gate["evidence_payload"]["evidence"]
    assert captured[0]["event_type"] == "blocked"


def test_lm_done_with_evidence() -> None:
    captured = []
    mod = _load_quick_with_stub(
        stub_transition=lambda kw: captured.append(kw) or {"ok": True}
    )

    evidence = {
        "commit_hash": "abc123",
        "test_result": "42/42 passed",
    }
    mod.lm_done("task-003", evidence)

    gate = captured[0]["data"]["lm_quick"]
    assert gate["evidence_payload"]["evidence"]["commit_hash"] == "abc123"
    assert gate["evidence_payload"]["evidence"]["test_result"] == "42/42 passed"


def test_lm_status_returns_dict() -> None:
    mod = _load_quick_with_stub(
        stub_query=lambda kw: []
    )

    result = mod.lm_status("lingclaude")

    assert result["member"] == "lingclaude"
    assert "in_progress" in result
    assert "pending" in result
    assert "blocked" in result
    assert result["counts"]["in_progress"] == 0


def test_evidence_gate_schema_fields() -> None:
    captured = []
    mod = _load_quick_with_stub(
        stub_transition=lambda kw: captured.append(kw) or {"ok": True}
    )

    mod.lm_done("task-004")

    gate = captured[0]["data"]["lm_quick"]
    assert "gate_id" in gate
    assert gate["gate_type"] == "transition"
    assert "evidence_payload" in gate
    assert gate["verdict"] == "pass"
    assert "created_by" in gate
    assert len(gate["gate_id"]) == 32  # UUID hex 32 chars


def test_lm_done_fail_soft_on_error() -> None:
    def _raise(**kwargs):
        raise RuntimeError("lingmemory unavailable")

    mod = _load_quick_with_stub(stub_transition=_raise)

    result = mod.lm_done("task-005")

    assert result["status"] == "fail"
    assert "error" in result
    assert result["fail_closed_action"] == "alert"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])