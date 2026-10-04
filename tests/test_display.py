from __future__ import annotations

from lingclaude.cli.display import (
    QualityReport,
    SessionSummary,
    format_header,
    format_queue_hint,
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

    def test_preview_content_shown(self) -> None:
        # 2026-09-26: preview 内容直接外显（此前只显示 "(N chars)" 计数）
        result = format_tool_result(False, "x" * 100)
        assert "x" in result
        assert "chars)" not in result


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


class TestFormatQueueHint:
    """排队/插队提示行高亮（2026-10-04）：steal=黄粗体, queued=青, 三分支禁色。"""

    def test_steal_explicit_color(self) -> None:
        result = format_queue_hint("[round 3 插队] 检查日志", kind="steal", color=True)
        assert result == "\033[1;33m[round 3 插队] 检查日志\033[0m"

    def test_queued_explicit_color(self) -> None:
        result = format_queue_hint("[排队执行] 修复测试", kind="queued", color=True)
        assert result == "\033[36m[排队执行] 修复测试\033[0m"

    def test_color_false_passthrough(self) -> None:
        text = "[排队执行] 原样透传"
        assert format_queue_hint(text, kind="queued", color=False) == text

    def test_no_color_env_disables(self, monkeypatch: object) -> None:
        import os

        monkeypatch.setenv("NO_COLOR", "1")  # type: ignore[attr-defined]
        text = "[round 1 插队] 文本"
        assert format_queue_hint(text, kind="steal") == text

    def test_managed_tui_always_colors(self, monkeypatch: object) -> None:
        from lingclaude.cli import repl_io

        monkeypatch.setattr(repl_io, "_full_tui_managed", True, raising=False)  # type: ignore[attr-defined]
        result = format_queue_hint("[排队执行] x", kind="queued", color=None)
        assert result.startswith("\033[36m")

    def test_nontty_nocolor_by_default(self, monkeypatch: object) -> None:
        monkeypatch.delenv("NO_COLOR", raising=False)  # type: ignore[attr-defined]
        monkeypatch.delenv("LINGCLAUDE_COLOR", raising=False)  # type: ignore[attr-defined]
        # pytest 捕获流非 tty → 默认不上色（不污染日志）
        text = "[round 2 插队] 文本"
        assert format_queue_hint(text, kind="steal") == text
