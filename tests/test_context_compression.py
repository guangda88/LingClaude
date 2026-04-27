from __future__ import annotations

import pytest
from lingclaude.core.context_compression import (
    CompressionConfig,
    CompressionLevel,
    CompressionResult,
    compress_messages,
    extract_facts_from_messages,
    generate_chinese_summary,
    _estimate_tokens_saved,
)


class TestExtractFactsFromMessages:
    def test_extracts_file_paths(self) -> None:
        messages = [
            "I read src/main.py and found the issue in utils/helper.py",
            "Also checked config.yaml for settings",
        ]
        facts = extract_facts_from_messages(messages)
        assert "src/main.py" in facts["files_read"]
        assert "utils/helper.py" in facts["files_read"]
        assert "config.yaml" in facts["files_read"]

    def test_extracts_decisions(self) -> None:
        messages = [
            "我们决定采用方案A来处理这个问题",
            "Decided to use the new approach for refactoring",
        ]
        facts = extract_facts_from_messages(messages)
        assert len(facts["decisions"]) == 2

    def test_extracts_exclusions(self) -> None:
        messages = [
            "排除了方案B因为性能不够",
            "Ruled out the old parser approach",
        ]
        facts = extract_facts_from_messages(messages)
        assert len(facts["exclusions"]) == 2

    def test_extracts_errors(self) -> None:
        messages = [
            "运行时出现错误: ModuleNotFoundError",
            "The test failed with assertion error",
        ]
        facts = extract_facts_from_messages(messages)
        assert len(facts["errors"]) == 2

    def test_empty_messages(self) -> None:
        facts = extract_facts_from_messages([])
        assert facts["files_read"] == []
        assert facts["decisions"] == []
        assert facts["exclusions"] == []
        assert facts["errors"] == []

    def test_ignores_urls_as_files(self) -> None:
        messages = ["Check https://example.com/app.py for docs"]
        facts = extract_facts_from_messages(messages)
        assert "https://example.com/app.py" not in facts["files_read"]

    def test_respects_limits(self) -> None:
        messages = [f"决定使用方案{i}" for i in range(20)]
        facts = extract_facts_from_messages(messages)
        assert len(facts["decisions"]) <= 15


class TestGenerateChineseSummary:
    def test_includes_all_sections(self) -> None:
        facts = {
            "files_read": ["src/main.py", "tests/test.py"],
            "decisions": ["采用方案A"],
            "exclusions": ["排除了方案B"],
            "errors": ["出现错误X"],
        }
        summary = generate_chinese_summary(facts, dropped_count=10)
        assert "已读文件" in summary
        assert "已做决策" in summary
        assert "已排除方案" in summary
        assert "已遇错误" in summary
        assert "main.py" in summary
        assert "采用方案A" in summary

    def test_empty_facts_minimal_summary(self) -> None:
        facts = {"files_read": [], "decisions": [], "exclusions": [], "errors": []}
        summary = generate_chinese_summary(facts, dropped_count=5)
        assert "前 5 轮" in summary

    def test_includes_recent_context(self) -> None:
        facts = {"files_read": [], "decisions": [], "exclusions": [], "errors": []}
        summary = generate_chinese_summary(facts, dropped_count=3, recent_context="Some recent text")
        assert "最近上下文片段" in summary
        assert "Some recent text" in summary


class TestCompressMessages:
    def test_no_compression_needed(self) -> None:
        messages = ["msg1", "msg2", "msg3"]
        result = compress_messages(messages, CompressionConfig(max_messages=5))
        assert result.dropped_count == 0
        assert result.compressed_messages == messages

    def test_truncate_level(self) -> None:
        messages = [f"message {i}" for i in range(10)]
        result = compress_messages(
            messages,
            CompressionConfig(max_messages=4, level=CompressionLevel.TRUNCATE),
        )
        assert result.dropped_count == 6
        assert "[前 6 轮对话已压缩]" in result.compressed_messages[0]
        assert len(result.compressed_messages) == 5  # summary + 4 kept

    def test_summary_level(self) -> None:
        messages = [
            "Read src/main.py and found the bug",
            "决定采用新方案",
            "排除了旧方法",
            "message 3",
            "message 4",
            "message 5",
            "message 6",
        ]
        result = compress_messages(
            messages,
            CompressionConfig(max_messages=4, level=CompressionLevel.SUMMARY),
        )
        assert result.dropped_count == 3
        assert "已读文件" in result.summary_text
        assert "src/main.py" in result.summary_text
        assert result.archived_facts > 0

    def test_summary_respects_max_chars(self) -> None:
        messages = [
            f"Read file_{i}.py with lots of content about decision and approach and error"
            for i in range(20)
        ]
        result = compress_messages(
            messages,
            CompressionConfig(max_messages=4, summary_max_chars=200, level=CompressionLevel.SUMMARY),
        )
        assert len(result.summary_text) <= 250  # some slack for truncation line

    def test_tokens_saved_positive(self) -> None:
        messages = [f"Long message with lots of content number {i}" for i in range(20)]
        result = compress_messages(
            messages,
            CompressionConfig(max_messages=5, level=CompressionLevel.SUMMARY),
        )
        assert result.tokens_estimated_saved > 0

    def test_result_type(self) -> None:
        messages = ["x"] * 30
        result = compress_messages(messages, CompressionConfig(max_messages=10))
        assert isinstance(result, CompressionResult)
        assert isinstance(result.compressed_messages, list)
        assert isinstance(result.level, CompressionLevel)
