"""Datalog 适配测试"""
import sys; sys.path.insert(0, "lingmemory")
import json
import os
from pathlib import Path
from lingclaude.core.datalog import log_l5_audit, log_t0_behavior, log_degradation_alert


def _read_latest_datalog() -> list[dict]:
    datalog_dir = Path.home() / ".lingclaude" / "datalog"
    files = sorted(datalog_dir.glob("*.jsonl"))
    if not files:
        return []
    with open(files[-1]) as f:
        return [json.loads(line) for line in f if line.strip()]


class TestDatalogWrite:
    def test_l5_audit_written(self):
        log_l5_audit(
            session_id="test-sess", l5_session_id="l5-test", l5_round=2,
            consistency_score=0.95, should_early_exit=True, should_fix=False,
            t1_result={"total": 2, "verified": 2},
            model="glm-5.2@zai", latency_ms=1500,
        )
        events = _read_latest_datalog()
        l5_events = [e for e in events if e["event_type"] == "l5.audit.round"]
        assert any(e["l5_session_id"] == "l5-test" for e in l5_events)

    def test_t0_behavior_written(self):
        log_t0_behavior(
            session_id="test-sess",
            checks={"edit_verify": "nudge", "tool_repetition": "pass"},
            nudges=["检测到编辑后未验证"], should_block=False,
        )
        events = _read_latest_datalog()
        t0_events = [e for e in events if e["event_type"] == "t0.behavior.check"]
        assert len(t0_events) >= 1

    def test_degradation_written(self):
        log_degradation_alert(
            session_id="test-sess", signal="tool_repeat",
            severity="warning", detail="grep called 3 times", msg_index=42,
        )
        events = _read_latest_datalog()
        deg_events = [e for e in events if e["event_type"] == "tool.degradation.alert"]
        assert any(e["msg_index"] == 42 for e in deg_events)
