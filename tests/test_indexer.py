from __future__ import annotations

from pathlib import Path

from lingclaude.engine.indexer import (
    ProjectIndex,
    SymbolInfo,
    _parse_file,
    _should_skip,
    index_project,
)


class TestShouldSkip:
    def test_skip_pycache(self) -> None:
        p = Path("/home/user/project/__pycache__/module.py")
        assert _should_skip(p) is True

    def test_skip_git(self) -> None:
        p = Path("/home/user/project/.git/file.py")
        assert _should_skip(p) is True

    def test_skip_venv(self) -> None:
        p = Path("/home/user/project/venv/lib/file.py")
        assert _should_skip(p) is True

    def test_skip_node_modules(self) -> None:
        p = Path("/home/user/project/node_modules/file.js")
        assert _should_skip(p) is True

    def test_skip_test_cache(self) -> None:
        p = Path("/home/user/project/.pytest_cache/file.py")
        assert _should_skip(p) is True

    def test_no_skip_normal_file(self) -> None:
        p = Path("/home/user/project/lingclaude/api.py")
        assert _should_skip(p) is False


class TestSymbolInfo:
    def test_to_dict(self) -> None:
        s = SymbolInfo(
            name="my_function",
            kind="function",
            file="test.py",
            line=10,
            detail="args: x, y",
        )
        d = s.to_dict()
        assert d["name"] == "my_function"
        assert d["kind"] == "function"
        assert d["file"] == "test.py"
        assert d["line"] == 10
        assert d["detail"] == "args: x, y"


class TestProjectIndex:
    def test_to_dict(self) -> None:
        idx = ProjectIndex(
            root="/test",
            files_scanned=5,
            symbols=[
                SymbolInfo("Func1", "function", "a.py", 1, ""),
                SymbolInfo("Class1", "class", "b.py", 10, ""),
                SymbolInfo("Import1", "import", "a.py", 1, ""),
            ],
        )
        d = idx.to_dict()
        assert d["root"] == "/test"
        assert d["files_scanned"] == 5
        assert d["total_symbols"] == 3
        assert d["classes"] == 1
        assert d["functions"] == 1
        assert d["imports"] == 1

    def test_format_compact(self) -> None:
        idx = ProjectIndex(
            root="/test",
            files_scanned=2,
            symbols=[
                SymbolInfo("MyClass", "class", "test.py", 10, ""),
                SymbolInfo("my_method", "function", "test.py", 15, "method:MyClass()"),
                SymbolInfo("standalone_func", "function", "other.py", 5, "x, y"),
            ],
        )
        result = idx.format_compact()
        assert "项目索引" in result
        assert "2 文件" in result
        assert "3 符号" in result
        assert "类:" in result
        assert "MyClass" in result


class TestParseFile:
    def test_parse_simple_class(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
class MyClass:
    def method1(self, x):
        pass
""")
        root = tmp_path
        symbols = _parse_file(f, root)
        assert len(symbols) == 2
        assert symbols[0].name == "MyClass"
        assert symbols[0].kind == "class"
        assert symbols[1].name == "method1"
        assert symbols[1].kind == "function"
        assert "method:MyClass" in symbols[1].detail

    def test_parse_import(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
import os
import sys as system
from typing import List, Optional
""")
        root = tmp_path
        symbols = _parse_file(f, root)
        import_symbols = [s for s in symbols if s.kind == "import"]
        assert len(import_symbols) == 3
        names = [s.name for s in import_symbols]
        assert "os" in names
        assert "system" in names
        assert "from typing" in symbols[2].detail

    def test_parse_function_with_args(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
def my_function(x: int, y: str = "default") -> None:
    pass
""")
        root = tmp_path
        symbols = _parse_file(f, root)
        assert len(symbols) == 1
        assert symbols[0].name == "my_function"
        assert symbols[0].kind == "function"
        assert "x" in symbols[0].detail
        assert "y" in symbols[0].detail

    def test_parse_decorated_function(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
def decorator(func):
    return func

@decorator
def decorated_func():
    pass
""")
        root = tmp_path
        symbols = _parse_file(f, root)
        # Both decorator and decorated_func
        assert len(symbols) == 2
        decorated = [s for s in symbols if s.name == "decorated_func"][0]
        assert "decorator" in decorated.detail

    def test_parse_class_with_bases(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
class MyClass(BaseClass, AnotherBase):
    pass
""")
        root = tmp_path
        symbols = _parse_file(f, root)
        assert len(symbols) == 1
        assert symbols[0].name == "MyClass"
        assert "BaseClass" in symbols[0].detail
        assert "AnotherBase" in symbols[0].detail

    def test_parse_syntax_error(self, tmp_path) -> None:
        f = tmp_path / "broken.py"
        f.write_text("this is not valid python syntax {{{")
        root = tmp_path
        symbols = _parse_file(f, root)
        assert len(symbols) == 0


class TestIndexProject:
    def test_index_current_directory(self) -> None:
        result = index_project(".", max_files=10)
        assert result.is_ok
        idx = result.data
        assert idx.root != ""
        assert idx.files_scanned > 0
        assert len(idx.symbols) > 0

    def test_index_nonexistent_directory(self) -> None:
        result = index_project("/nonexistent/path")
        assert result.is_error
        assert "Not a directory" in result.error

    def test_index_file_not_directory(self, tmp_path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("content")
        result = index_project(str(f))
        assert result.is_error

    def test_index_with_max_files(self, tmp_path) -> None:
        # Create multiple Python files
        for i in range(5):
            f = tmp_path / f"file{i}.py"
            f.write_text(f"def func{i}(): pass")

        result = index_project(str(tmp_path), max_files=3)
        assert result.is_ok
        assert result.data.files_scanned <= 3

    def test_index_skips_venv(self, tmp_path) -> None:
        # Create a Python file in venv
        venv_dir = tmp_path / "venv" / "lib"
        venv_dir.mkdir(parents=True)
        venv_file = venv_dir / "module.py"
        venv_file.write_text("def venv_func(): pass")

        # Create a normal Python file
        normal_file = tmp_path / "normal.py"
        normal_file.write_text("def normal_func(): pass")

        result = index_project(str(tmp_path))
        assert result.is_ok
        # Should only scan normal.py, not venv/module.py
        assert result.data.files_scanned == 1
        assert any(s.name == "normal_func" for s in result.data.symbols)
        assert not any(s.name == "venv_func" for s in result.data.symbols)

    def test_index_generates_file_summaries(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
class MyClass:
    pass

def my_func():
    pass
""")
        result = index_project(str(tmp_path))
        assert result.is_ok
        idx = result.data
        assert "test.py" in idx.file_summaries
        summary = idx.file_summaries["test.py"]
        assert summary["classes"] == 1
        assert summary["functions"] == 1

    def test_index_measures_duration(self) -> None:
        result = index_project(".")
        assert result.is_ok
        assert result.data.duration > 0

    def test_index_to_dict_structure(self) -> None:
        result = index_project(".", max_files=1)
        assert result.is_ok
        d = result.data.to_dict()
        required_keys = [
            "root", "files_scanned", "total_symbols",
            "classes", "functions", "imports", "duration",
            "symbols", "file_summaries"
        ]
        for key in required_keys:
            assert key in d

    def test_index_sorts_symbols_by_file(self, tmp_path) -> None:
        f1 = tmp_path / "a.py"
        f1.write_text("def func_a(): pass")
        f2 = tmp_path / "z.py"
        f2.write_text("def func_z(): pass")

        result = index_project(str(tmp_path))
        assert result.is_ok
        # Symbols should be sorted alphabetically by filename
        symbols = result.data.symbols
        files = [s.file for s in symbols]
        assert files == sorted(files)


class TestIndexProjectReal:
    def test_index_real_lingclaude_project(self) -> None:
        result = index_project(str(Path(__file__).parent.parent), max_files=50)
        assert result.is_ok
        idx = result.data
        assert idx.files_scanned > 0
        assert len(idx.symbols) > 0
        assert idx.duration < 5.0  # Should be fast (< 5 seconds)

    def test_format_compact_on_real_project(self) -> None:
        result = index_project(str(Path(__file__).parent.parent), max_files=10)
        assert result.is_ok
        idx = result.data
        formatted = idx.format_compact()
        assert "项目索引" in formatted
        assert "文件" in formatted
        assert "符号" in formatted
        # Check that we have some actual content
        assert len(formatted) > 30
