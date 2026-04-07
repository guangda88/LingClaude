from __future__ import annotations

import pathlib

from lingclaude.engine.grep import GrepTool, GrepMatch, GrepResult


class TestGrepBasic:
    def test_search_finds_match(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("def hello():\n    print('world')\n")
        tool = GrepTool(base_dir=str(tmp_path))
        result = tool.search("hello")
        assert result.is_ok
        assert result.data.total_matches >= 1
        assert result.data.files_matched == 1

    def test_search_no_match(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("nothing here\n")
        tool = GrepTool(base_dir=str(tmp_path))
        result = tool.search("nonexistent_pattern_xyz")
        assert result.is_ok
        assert result.data.total_matches == 0

    def test_search_invalid_regex(self) -> None:
        tool = GrepTool()
        result = tool.search("[invalid")
        assert result.is_error
        assert "正则" in result.error


class TestGrepLiteral:
    def test_literal_search(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("result = func(a+b)\n")
        tool = GrepTool(base_dir=str(tmp_path))
        result = tool.search("a+b", literal=True)
        assert result.is_ok
        assert result.data.total_matches == 1
        assert "a+b" in result.data.matches[0].matched_text


class TestGrepCaseSensitivity:
    def test_case_sensitive(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("Hello\nhello\nHELLO\n")
        tool = GrepTool(base_dir=str(tmp_path))
        result = tool.search("Hello", case_sensitive=True)
        assert result.is_ok
        assert result.data.total_matches == 1

    def test_case_insensitive(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("Hello\nhello\nHELLO\n")
        tool = GrepTool(base_dir=str(tmp_path))
        result = tool.search("hello", case_sensitive=False)
        assert result.is_ok
        assert result.data.total_matches == 3


class TestGrepInclude:
    def test_custom_include(self, tmp_path: pathlib.Path) -> None:
        py_file = tmp_path / "test.py"
        py_file.write_text("match_me\n")
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("match_me\n")
        tool = GrepTool(base_dir=str(tmp_path))
        result = tool.search("match_me", include="*.txt")
        assert result.is_ok
        assert result.data.total_matches == 1

    def test_multiple_files(self, tmp_path: pathlib.Path) -> None:
        for name in ("a.py", "b.py", "c.py"):
            (tmp_path / name).write_text("target_string\n")
        tool = GrepTool(base_dir=str(tmp_path))
        result = tool.search("target_string")
        assert result.is_ok
        assert result.data.files_matched == 3
        assert result.data.total_matches == 3


class TestGrepHiddenFiles:
    def test_skips_hidden_files(self, tmp_path: pathlib.Path) -> None:
        hidden_dir = tmp_path / ".hidden"
        hidden_dir.mkdir()
        (hidden_dir / "secret.py").write_text("findme\n")
        (tmp_path / "visible.py").write_text("findme\n")
        tool = GrepTool(base_dir=str(tmp_path))
        result = tool.search("findme")
        assert result.is_ok
        assert result.data.files_matched == 1


class TestGrepTruncation:
    def test_max_results_truncation(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        lines = [f"line_{i}: target_word" for i in range(50)]
        f.write_text("\n".join(lines) + "\n")
        tool = GrepTool(base_dir=str(tmp_path), max_results=5)
        result = tool.search("target_word")
        assert result.is_ok
        assert result.data.total_matches == 5
        assert result.data.truncated is True


class TestGrepResultDict:
    def test_to_dict(self) -> None:
        r = GrepResult(
            matches=(
                GrepMatch(file="a.py", line=1, column=1, content="test", matched_text="test"),
            ),
            files_searched=3,
            files_matched=1,
            total_matches=1,
            duration=0.01,
            truncated=False,
        )
        d = r.to_dict()
        assert d["total_matches"] == 1
        assert d["files_searched"] == 3
        assert len(d["matches"]) == 1
        assert d["matches"][0]["file"] == "a.py"


class TestGrepContract:
    def test_grep_contract(self) -> None:
        assert GrepTool.contract.scope == "file:search"
        assert GrepTool.contract.timeout == 15.0
