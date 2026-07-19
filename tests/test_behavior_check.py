"""T0 行为校验测试"""
import sys; sys.path.insert(0, "lingmemory")
from test_engine import TestCase, pytest_case
from lingclaude.core.behavior_check import (
    check_edit_verify, check_tool_repetition, check_consecutive_failure,
    check_output_completeness, check, BehaviorCheckResult,
)


class TestEditVerify:
    def test_edit_without_verify_nudges(self):
        history = [{"tool_name": "edit_file", "arguments": {"file_path": "x.py"}}]
        assert len(check_edit_verify(history)) == 1

    def test_edit_then_build_passes(self):
        history = [
            {"tool_name": "edit_file", "arguments": {"file_path": "x.py"}},
            {"tool_name": "bash", "command": "cargo check"},
        ]
        assert len(check_edit_verify(history)) == 0

    def test_edit_then_readonly_still_nudges(self):
        history = [
            {"tool_name": "edit_file", "arguments": {"file_path": "x.py"}},
            {"tool_name": "bash", "command": "ls -la"},
        ]
        assert len(check_edit_verify(history)) == 1


class TestToolRepetition:
    def test_repeated_tool_nudges(self):
        history = [
            {"tool_name": "bash", "arguments": {"command": "ls"}},
            {"tool_name": "bash", "arguments": {"command": "ls"}},
            {"tool_name": "bash", "arguments": {"command": "ls"}},
        ]
        assert len(check_tool_repetition(history)) >= 1

    def test_different_tools_pass(self):
        history = [
            {"tool_name": "bash", "arguments": {"command": "cargo"}},
            {"tool_name": "code_search", "arguments": {}},
            {"tool_name": "bash", "arguments": {"command": "cargo check"}},
        ]
        assert len(check_tool_repetition(history)) == 0


class TestConsecutiveFailure:
    def test_same_cmd_fails_nudges(self):
        history = [
            {"command": "cargo check", "is_error": True},
            {"command": "cargo check", "is_error": True},
        ]
        assert len(check_consecutive_failure(history)) == 1

    def test_success_after_failure_passes(self):
        history = [
            {"command": "cargo check", "is_error": True},
            {"command": "cargo check", "is_error": False},
        ]
        assert len(check_consecutive_failure(history)) == 0


class TestOutputCompleteness:
    def test_empty_output(self):
        assert len(check_output_completeness("")) >= 1

    def test_i_dont_know(self):
        assert len(check_output_completeness("我不知道")) >= 1

    def test_normal_output_passes(self):
        assert len(check_output_completeness("文件内容是 hello world")) == 0


class TestCheck:
    def test_combined_check(self):
        result = check(
            tool_history=[
                {"tool_name": "edit_file", "arguments": {"file_path": "x.py"}},
                {"tool_name": "bash", "command": "ls"},
            ],
            output="我不知道",
        )
        assert not result.passed
        assert len(result.nudges) >= 2

    def test_clean_passes(self):
        result = check(
            tool_history=[
                {"tool_name": "bash", "command": "cargo check"},
            ],
            output="一切都好",
        )
        assert result.passed
