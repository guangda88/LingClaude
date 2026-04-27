from __future__ import annotations

import json
import pytest

from lingclaude.core.dementia_detector import (
    CognitiveState,
    DementiaDetector,
    DementiaDiagnosis,
    DementiaMetrics,
    ToolCallFingerprint,
)


class TestToolCallFingerprint:
    def test_same_args_same_hash(self) -> None:
        fp1 = ToolCallFingerprint.from_call("read", '{"path": "/tmp/a.py"}')
        fp2 = ToolCallFingerprint.from_call("read", '{"path": "/tmp/a.py"}')
        assert fp1 == fp2

    def test_different_args_different_hash(self) -> None:
        fp1 = ToolCallFingerprint.from_call("read", '{"path": "/tmp/a.py"}')
        fp2 = ToolCallFingerprint.from_call("read", '{"path": "/tmp/b.py"}')
        assert fp1 != fp2

    def test_different_tool_different_fp(self) -> None:
        fp1 = ToolCallFingerprint.from_call("read", '{"path": "/tmp/a.py"}')
        fp2 = ToolCallFingerprint.from_call("grep", '{"path": "/tmp/a.py"}')
        assert fp1 != fp2


class TestDementiaMetrics:
    def test_zero_rates_when_no_calls(self) -> None:
        m = DementiaMetrics()
        assert m.duplicate_file_read_rate == 0.0
        assert m.duplicate_tool_call_rate == 0.0

    def test_file_read_rate(self) -> None:
        m = DementiaMetrics(files_read={"/a.py": 3, "/b.py": 1}, duplicate_file_reads=2)
        assert m.duplicate_file_read_rate == pytest.approx(0.5)

    def test_tool_dup_rate(self) -> None:
        m = DementiaMetrics(total_tool_calls=10, duplicate_tool_calls=3)
        assert m.duplicate_tool_call_rate == pytest.approx(0.3)


class TestDementiaDetector:
    def test_healthy_when_no_duplicates(self) -> None:
        det = DementiaDetector()
        for i in range(10):
            det.record_tool_call("read", json.dumps({"path": f"/tmp/file{i}.py"}))
        diag = det.diagnose()
        assert diag.state == CognitiveState.HEALTHY
        assert diag.dementia_index == 0.0
        assert diag.intervention_prompt == ""

    def test_mild_on_file_rereads(self) -> None:
        det = DementiaDetector(file_read_threshold=1)
        for _ in range(3):
            det.record_tool_call("read", json.dumps({"path": "/tmp/same.py"}))
        diag = det.diagnose()
        assert diag.state in (
            CognitiveState.MILD_DEGRADATION,
            CognitiveState.MODERATE_DEGRADATION,
        )
        assert "认知预警" in diag.intervention_prompt

    def test_severe_on_massive_duplicates(self) -> None:
        det = DementiaDetector(file_read_threshold=1, tool_dup_threshold=1)
        for _ in range(5):
            det.record_tool_call("read", json.dumps({"path": "/tmp/a.py"}))
            det.record_tool_call("grep", json.dumps({"pattern": "foo"}))
        diag = det.diagnose()
        assert diag.state in (
            CognitiveState.SEVERE_DEGRADATION,
            CognitiveState.DEMENTIA,
        )

    def test_hard_stop_after_repeated_dementia(self) -> None:
        det = DementiaDetector(file_read_threshold=1, tool_dup_threshold=1)
        for _ in range(3):
            for __ in range(5):
                det.record_tool_call("read", json.dumps({"path": "/tmp/a.py"}))
            det.diagnose()
        diag = det.diagnose()
        assert diag.should_hard_stop

    def test_no_hard_stop_when_healthy(self) -> None:
        det = DementiaDetector()
        for i in range(10):
            det.record_tool_call("read", json.dumps({"path": f"/tmp/{i}.py"}))
        diag = det.diagnose()
        assert not diag.should_hard_stop

    def test_record_tool_call_returns_dup_flag(self) -> None:
        det = DementiaDetector(file_read_threshold=1, tool_dup_threshold=1)
        args = json.dumps({"path": "/tmp/x.py"})
        det.record_tool_call("read", args)
        det.record_tool_call("read", args)
        is_dup = det.record_tool_call("read", args)
        assert is_dup

    def test_record_retrace(self) -> None:
        det = DementiaDetector(file_read_threshold=1, tool_dup_threshold=1)
        for _ in range(5):
            det.record_tool_call("read", json.dumps({"path": "/tmp/a.py"}))
            det.record_retrace()
        diag = det.diagnose()
        assert diag.dementia_index > 0

    def test_reset_clears_state(self) -> None:
        det = DementiaDetector(file_read_threshold=1)
        for _ in range(5):
            det.record_tool_call("read", json.dumps({"path": "/tmp/a.py"}))
        det.reset()
        diag = det.diagnose()
        assert diag.state == CognitiveState.HEALTHY
        assert det.get_metrics().total_tool_calls == 0

    def test_window_pruning(self) -> None:
        det = DementiaDetector(window_size=5)
        for i in range(100):
            det.record_tool_call("read", json.dumps({"path": f"/tmp/{i}.py"}))
        assert len(det._recent_calls) <= 50

    def test_non_read_tools_tracked(self) -> None:
        det = DementiaDetector(tool_dup_threshold=1)
        args = json.dumps({"pattern": "test"})
        det.record_tool_call("grep", args)
        det.record_tool_call("grep", args)
        is_dup = det.record_tool_call("grep", args)
        assert is_dup

    def test_diagnosis_type(self) -> None:
        det = DementiaDetector()
        det.record_tool_call("read", json.dumps({"path": "/tmp/a.py"}))
        diag = det.diagnose()
        assert isinstance(diag, DementiaDiagnosis)
        assert isinstance(diag.state, CognitiveState)
        assert isinstance(diag.recent_duplicates, tuple)

    def test_intervention_includes_files(self) -> None:
        det = DementiaDetector(file_read_threshold=1)
        for _ in range(3):
            det.record_tool_call("read", json.dumps({"path": "/tmp/important.py"}))
        diag = det.diagnose()
        if diag.state != CognitiveState.HEALTHY:
            assert "important.py" in diag.intervention_prompt
