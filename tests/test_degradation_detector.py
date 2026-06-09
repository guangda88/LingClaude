from __future__ import annotations

from lingclaude.core.degradation_detector import (
    DegradationAlert,
    DegradationDetector,
    DegradationSignal,
    DetectionConfig,
    ToolCall,
    extract_tool_calls_from_text,
)


def _make_call(
    tool: str = "edit",
    file_path: str = "/tmp/foo.py",
    success: bool = True,
    msg_index: int = 0,
    result: str = "ok",
) -> ToolCall:
    return ToolCall(
        tool_name=tool,
        params={"file_path": file_path},
        result=result,
        success=success,
        msg_index=msg_index,
    )


class TestToolCall:
    def test_param_signature_stable(self):
        c1 = _make_call(msg_index=1)
        c2 = _make_call(msg_index=2)
        assert c1.param_signature() == c2.param_signature()

    def test_param_signature_differs_on_file_change(self):
        c1 = _make_call(file_path="/a.py")
        c2 = _make_call(file_path="/b.py")
        assert c1.param_signature() != c2.param_signature()

    def test_result_hash_stable(self):
        c = _make_call(result="hello")
        assert c.result_hash() == c.result_hash()

    def test_result_hash_differs_on_change(self):
        c1 = _make_call(result="ok")
        c2 = _make_call(result="error")
        assert c1.result_hash() != c2.result_hash()


class TestEditRetryDetection:
    def test_no_alert_below_threshold(self):
        d = DegradationDetector()
        for i in range(2):
            d.record_call(_make_call(success=False, msg_index=i))
        alerts = [a for a in d.record_call(_make_call(success=True, msg_index=2)) if a.signal == DegradationSignal.EDIT_RETRY]
        assert alerts == []

    def test_alert_on_consecutive_edit_failures(self):
        d = DegradationDetector(DetectionConfig(edit_retry_threshold=3))
        alerts: list[DegradationAlert] = []
        for i in range(3):
            alerts.extend(d.record_call(_make_call(success=False, msg_index=i)))
        edit_alerts = [a for a in alerts if a.signal == DegradationSignal.EDIT_RETRY]
        assert len(edit_alerts) >= 1
        assert "3 次" in edit_alerts[0].detail or "3" in edit_alerts[0].detail

    def test_different_files_no_alert(self):
        d = DegradationDetector(DetectionConfig(edit_retry_threshold=3))
        alerts: list[DegradationAlert] = []
        files = ["/a.py", "/b.py", "/c.py"]
        for i, f in enumerate(files):
            alerts.extend(d.record_call(_make_call(file_path=f, success=False, msg_index=i)))
        edit_alerts = [a for a in alerts if a.signal == DegradationSignal.EDIT_RETRY]
        assert edit_alerts == []

    def test_non_edit_failures_ignored(self):
        d = DegradationDetector(DetectionConfig(edit_retry_threshold=3))
        alerts: list[DegradationAlert] = []
        for i in range(5):
            alerts.extend(d.record_call(_make_call(tool="bash", success=False, msg_index=i)))
        edit_alerts = [a for a in alerts if a.signal == DegradationSignal.EDIT_RETRY]
        assert edit_alerts == []


class TestRepetitionLoopDetection:
    def test_no_alert_on_varied_calls(self):
        d = DegradationDetector(DetectionConfig(repetition_threshold=3))
        alerts: list[DegradationAlert] = []
        for i in range(5):
            alerts.extend(d.record_call(_make_call(
                tool="grep",
                file_path=f"/file{i}.py",
                result=f"result{i}",
                msg_index=i,
            )))
        rep = [a for a in alerts if a.signal == DegradationSignal.REPETITION_LOOP]
        assert rep == []

    def test_alert_on_same_call_same_result(self):
        d = DegradationDetector(DetectionConfig(repetition_threshold=3))
        alerts: list[DegradationAlert] = []
        for i in range(3):
            alerts.extend(d.record_call(_make_call(
                tool="grep",
                result="same",
                msg_index=i,
            )))
        rep = [a for a in alerts if a.signal == DegradationSignal.REPETITION_LOOP]
        assert len(rep) >= 1

    def test_design_repetition_not_flagged(self):
        """SDT-style calls with same params but different results should not flag."""
        d = DegradationDetector(DetectionConfig(repetition_threshold=3))
        alerts: list[DegradationAlert] = []
        for i in range(5):
            alerts.extend(d.record_call(_make_call(
                tool="grep",
                file_path="/data.py",
                result=f"changing_data_{i}",
                msg_index=i,
            )))
        rep = [a for a in alerts if a.signal == DegradationSignal.REPETITION_LOOP]
        assert rep == []

    def test_mixed_results_partial_flag(self):
        """Same params, alternating results — degraded but not stuck."""
        d = DegradationDetector(DetectionConfig(repetition_threshold=4))
        alerts: list[DegradationAlert] = []
        results = ["r1", "r2", "r1", "r2", "r1"]
        for i, r in enumerate(results):
            alerts.extend(d.record_call(_make_call(result=r, msg_index=i)))
        rep = [a for a in alerts if a.signal == DegradationSignal.REPETITION_LOOP]
        assert rep == []


class TestHealthIndicators:
    def test_empty_history(self):
        d = DegradationDetector()
        h = d.get_health_indicators()
        assert h["window_size"] == 0

    def test_basic_indicators(self):
        d = DegradationDetector()
        tools = ["edit", "grep", "view", "bash"]
        for i in range(10):
            d.record_call(_make_call(
                tool=tools[i % 4],
                file_path=f"/f{i % 3}.py",
                success=not (i % 4 == 0 and i == 4),
                result=f"r{i}",
                msg_index=i,
            ))
        h = d.get_health_indicators()
        assert h["window_size"] == 10
        assert h["total_calls"] == 10
        assert h["edit_failures"] >= 1
        assert h["unique_tool_signatures"] > 0
        assert 0 < h["tool_diversity"] <= 1


class TestExtractFromText:
    def test_extract_tool_call(self):
        msgs = [
            'tool_name: "edit"\nfile_path: "/tmp/foo.py"\nok',
            'no tool here',
            'tool: grep\nfile_path: "/tmp/bar.py"\nerror: not found',
        ]
        calls = extract_tool_calls_from_text(msgs)
        assert len(calls) == 2
        assert calls[0].tool_name == "edit"
        assert calls[0].success is True
        assert calls[1].tool_name == "grep"
        assert calls[1].success is False

    def test_empty_messages(self):
        assert extract_tool_calls_from_text([]) == []


class TestWindowSliding:
    def test_history_pruned(self):
        d = DegradationDetector(DetectionConfig(window_size=10))
        for i in range(50):
            d.record_call(_make_call(result=f"r{i}", msg_index=i))
        assert len(d._call_history) <= 20

    def test_reset(self):
        d = DegradationDetector()
        for i in range(5):
            d.record_call(_make_call(msg_index=i))
        d.reset()
        assert len(d._call_history) == 0
