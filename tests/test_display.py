from __future__ import annotations

from lingclaude.cli.display import (
    QualityReport,
    SessionSummary,
    format_header,
    format_score,
    format_tool_call,
    format_tool_result,
    print_error,
    print_header,
    print_info,
    print_kv,
    print_metrics_stats,
    print_quality_report,
    print_session_summary,
    print_success,
    print_trend,
    print_warning,
    print_welcome,
)


class TestFormatHeader:
    def test_basic(self) -> None:
        result = format_header("Test")
        assert "Test" in result

    def test_with_subtitle(self) -> None:
        result = format_header("Test", "sub")
        assert "sub" in result


class TestFormatScore:
    def test_full_score(self) -> None:
        result = format_score(1.0, width=10)
        assert "100%" in result
        assert "█" in result

    def test_zero_score(self) -> None:
        result = format_score(0.0, width=10)
        assert "0%" in result
        assert "░" in result

    def test_half_score(self) -> None:
        result = format_score(0.5, width=10)
        assert "50%" in result


class TestFormatToolCall:
    def test_basic(self) -> None:
        result = format_tool_call("bash", "ls -la")
        assert "bash" in result
        assert "ls -la" in result


class TestFormatToolResult:
    def test_success(self) -> None:
        result = format_tool_result(False, "output text here")
        assert "✓" in result

    def test_error(self) -> None:
        result = format_tool_result(True)
        assert "✗" in result

    def test_preview_chars(self) -> None:
        result = format_tool_result(False, "x" * 100)
        assert "100 chars" in result


class TestSessionSummary:
    def test_creation(self) -> None:
        s = SessionSummary(
            turns=5,
            session_id="abc123",
            usage={"input_tokens": 100},
            behavior={"hallucination_risk": 0.1},
        )
        assert s.turns == 5
        assert s.session_id == "abc123"
        assert s.stop_reason == ""

    def test_with_stop_reason(self) -> None:
        s = SessionSummary(
            turns=3, session_id="x", usage={}, behavior={}, stop_reason="max_turns"
        )
        assert s.stop_reason == "max_turns"


class TestQualityReport:
    def test_creation(self) -> None:
        r = QualityReport(overall=0.8, safety=0.9, structure=0.7, behavior=0.6, knowledge=0.5)
        assert r.overall == 0.8
        assert r.safety == 0.9


class TestPrintFunctions:
    def test_print_success(self, capsys: object) -> None:
        print_success("test message")
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        assert "test message" in captured.out + captured.err

    def test_print_error(self, capsys: object) -> None:
        print_error("error msg")
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        output = captured.out + captured.err
        assert "error msg" in output

    def test_print_warning(self, capsys: object) -> None:
        print_warning("warn")
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        assert "warn" in captured.out + captured.err

    def test_print_info(self, capsys: object) -> None:
        print_info("info msg")
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        assert "info msg" in captured.out + captured.err

    def test_print_kv(self, capsys: object) -> None:
        print_kv("Label", "value")
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        output = captured.out + captured.err
        assert "Label" in output
        assert "value" in output

    def test_print_header(self, capsys: object) -> None:
        print_header("Title", "sub")
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        output = captured.out + captured.err
        assert "Title" in output

    def test_print_session_summary(self, capsys: object) -> None:
        s = SessionSummary(
            turns=10,
            session_id="test123",
            usage={"tokens": 500},
            behavior={"hallucination_risk": 0.2},
        )
        print_session_summary(s)
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        output = captured.out + captured.err
        assert "10" in output
        assert "test123" in output

    def test_print_welcome(self, capsys: object) -> None:
        print_welcome("0.3.0", "openai", "gpt-4o", 12)
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        output = captured.out + captured.err
        assert "0.3.0" in output

    def test_print_quality_report(self, capsys: object) -> None:
        r = QualityReport(overall=0.8, safety=0.9, structure=0.7, behavior=0.6, knowledge=0.5)
        print_quality_report(r)
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        output = captured.out + captured.err
        assert "80%" in output

    def test_print_trend(self, capsys: object) -> None:
        print_trend("score", "up", 0.1, 0.75)
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        output = captured.out + captured.err
        assert "score" in output
        assert "0.750" in output

    def test_print_metrics_stats(self, capsys: object) -> None:
        stats = {"total_points": 42, "categories": {"quality": 30, "behavior": 12}}
        print_metrics_stats(stats)
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        output = captured.out + captured.err
        assert "42" in output
        assert "quality" in output
