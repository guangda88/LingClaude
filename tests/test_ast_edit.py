from __future__ import annotations

from pathlib import Path

from lingclaude.engine.ast_edit import (
    ASTEditResult,
    list_functions,
    replace_function_body,
)


class TestASTEditResult:
    def test_to_dict(self) -> None:
        r = ASTEditResult(
            file="test.py",
            target="my_func",
            kind="function",
            lines_old=5,
            lines_new=10,
            success=True,
            message="Replaced successfully",
        )
        d = r.to_dict()
        assert d["file"] == "test.py"
        assert d["target"] == "my_func"
        assert d["kind"] == "function"
        assert d["lines_old"] == 5
        assert d["lines_new"] == 10
        assert d["success"] is True
        assert d["message"] == "Replaced successfully"


class TestReplaceFunctionBody:
    def test_replace_simple_function(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
def my_function():
    old_content = 1
    return old_content
""")
        result = replace_function_body(
            str(f),
            "my_function",
            "new_content = 2\nreturn new_content",
        )
        assert result.is_ok
        assert result.data.success is True
        assert "new_content" in f.read_text()
        assert "old_content" not in f.read_text()

    def test_replace_function_with_class_method(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
class MyClass:
    def my_method(self):
        old = 1
        return old
""")
        result = replace_function_body(
            str(f),
            "my_method",
            "new = 2\nreturn new",
            class_name="MyClass",
        )
        assert result.is_ok
        assert result.data.kind == "method"
        assert result.data.target == "MyClass.my_method"
        assert "new = 2" in f.read_text()

    def test_replace_function_preserves_decorators(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
def decorator(func):
    return func

@decorator
def decorated_func():
    pass
""")
        result = replace_function_body(
            str(f),
            "decorated_func",
            "new_body\nreturn new_body",
        )
        assert result.is_ok
        content = f.read_text()
        assert "@decorator" in content
        assert "def decorated_func" in content
        assert "new_body" in content

    def test_replace_nonexistent_function(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("def my_func(): pass")
        result = replace_function_body(
            str(f),
            "nonexistent_func",
            "new content",
        )
        assert result.is_error
        assert "未找到" in result.error

    def test_replace_nonexistent_file(self, tmp_path) -> None:
        result = replace_function_body(
            str(tmp_path / "nonexistent.py"),
            "my_func",
            "content",
        )
        assert result.is_error
        assert "文件不存在" in result.error

    def test_replace_with_syntax_error_in_new_body(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("def my_func():\n    return 1")
        original_content = f.read_text()
        result = replace_function_body(
            str(f),
            "my_func",
            "invalid syntax {{{",
        )
        assert result.is_error
        assert "语法错误" in result.error
        # File should not be changed
        assert f.read_text() == original_content

    def test_replace_with_syntax_error_in_original(self, tmp_path) -> None:
        f = tmp_path / "broken.py"
        f.write_text("this is not valid python {{{")
        result = replace_function_body(
            str(f),
            "my_func",
            "new content",
        )
        assert result.is_error
        assert "语法错误" in result.error

    def test_replace_second_occurrence(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
def my_func():
    return 1

def my_func():
    return 2
""")
        result = replace_function_body(
            str(f),
            "my_func",
            "return 3",
            occurrence=2,
        )
        assert result.is_ok
        content = f.read_text()
        # First function should be unchanged
        assert "return 1" in content
        # Second function should be replaced
        assert "return 3" in content
        assert "return 2" not in content

    def test_replace_preserves_indentation(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
class MyClass:
    def my_method(self):
        old_line()
        another_old()
""")
        result = replace_function_body(
            str(f),
            "my_method",
            "new_line()\nanother_new()",
            class_name="MyClass",
        )
        assert result.is_ok
        content = f.read_text()
        # Check that new lines are properly indented
        assert "    new_line()" in content
        assert "    another_new()" in content

    def test_replace_empty_function(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("def my_func(): pass")
        result = replace_function_body(
            str(f),
            "my_func",
            "x = 1\nreturn x",
        )
        assert result.is_ok
        content = f.read_text()
        assert "x = 1" in content
        assert "return x" in content

    def test_replace_multiline_function(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
def my_func():
    line1()
    line2()
    line3()
    line4()
    line5()
    return
""")
        result = replace_function_body(
            str(f),
            "my_func",
            "new_line()",
        )
        assert result.is_ok
        content = f.read_text()
        assert "new_line()" in content
        assert "line1" not in content
        assert "line5" not in content

    def test_replace_with_async_function(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
async def my_async_func():
    await something()
""")
        result = replace_function_body(
            str(f),
            "my_async_func",
            "await something_else()",
        )
        assert result.is_ok
        content = f.read_text()
        assert "async def my_async_func" in content
        assert "await something_else()" in content
        assert "await something()" not in content


class TestListFunctions:
    def test_list_simple_functions(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
def func1():
    pass

def func2(x, y):
    pass
""")
        result = list_functions(str(f))
        assert result.is_ok
        funcs = result.data
        assert len(funcs) == 2
        names = [f["name"] for f in funcs]
        assert "func1" in names
        assert "func2" in names

    def test_list_class_methods(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
class MyClass:
    def method1(self):
        pass

    def method2(self, x):
        pass
""")
        result = list_functions(str(f))
        assert result.is_ok
        funcs = result.data
        assert len(funcs) == 2
        assert all(f["kind"] == "method" for f in funcs)
        names = [f["name"] for f in funcs]
        assert "MyClass.method1" in names
        assert "MyClass.method2" in names

    def test_list_mixed_functions_and_methods(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
def standalone_func():
    pass

class MyClass:
    def my_method(self):
        pass

def another_func():
    pass
""")
        result = list_functions(str(f))
        assert result.is_ok
        funcs = result.data
        assert len(funcs) == 3
        kinds = [f["kind"] for f in funcs]
        assert "function" in kinds
        assert "method" in kinds

    def test_list_with_line_numbers(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
# comment
def func1():
    pass

def func2():
    pass
""")
        result = list_functions(str(f))
        assert result.is_ok
        funcs = result.data
        # Line numbers should be present
        for func in funcs:
            assert "line" in func
            assert func["line"] > 0

    def test_list_with_end_line_numbers(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
def func1():
    return 1

def func2():
    return 2
""")
        result = list_functions(str(f))
        assert result.is_ok
        funcs = result.data
        # end_line should be present
        for func in funcs:
            assert "end_line" in func
            assert func["end_line"] >= func["line"]

    def test_list_with_args(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
def func_with_args(x, y, z):
    pass

class MyClass:
    def method_with_args(self, a, b):
        pass
""")
        result = list_functions(str(f))
        assert result.is_ok
        funcs = result.data
        # Check args are captured
        func_args = next((f["args"] for f in funcs if f["name"] == "func_with_args"))
        assert "x" in func_args
        assert "y" in func_args

        method = next((f for f in funcs if f["name"] == "MyClass.method_with_args"))
        # self and cls should be excluded from args
        assert "self" not in method["args"]
        assert "a" in method["args"]
        assert "b" in method["args"]

    def test_list_nonexistent_file(self, tmp_path) -> None:
        result = list_functions(str(tmp_path / "nonexistent.py"))
        assert result.is_error
        assert "文件不存在" in result.error

    def test_list_syntax_error(self, tmp_path) -> None:
        f = tmp_path / "broken.py"
        f.write_text("this is not valid python {{{")
        result = list_functions(str(f))
        assert result.is_error
        assert "解析失败" in result.error

    def test_list_empty_file(self, tmp_path) -> None:
        f = tmp_path / "empty.py"
        f.write_text("")
        result = list_functions(str(f))
        assert result.is_ok
        assert len(result.data) == 0

    def test_list_with_decorator(self, tmp_path) -> None:
        f = tmp_path / "test.py"
        f.write_text("""
def decorator(func):
    return func

@decorator
def decorated_func():
    pass
""")
        result = list_functions(str(f))
        assert result.is_ok
        funcs = result.data
        # Should find decorated_func
        assert any(f["name"] == "decorated_func" for f in funcs)


class TestASTEditReal:
    def test_replace_real_lingclaude_function(self) -> None:
        # Use a real file from the project for testing
        import shutil
        import tempfile

        # Copy a real file to temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            source_file = Path(__file__).parent.parent / "lingclaude" / "engine" / "git.py"
            test_file = Path(tmpdir) / "git_test.py"
            shutil.copy(source_file, test_file)

            # Replace a function
            original_content = test_file.read_text()
            result = replace_function_body(
                str(test_file),
                "_run_git",
                "return GitResult(0, '', '', 0.0)",
            )
            assert result.is_ok
            assert result.data.success is True

            # Restore original
            test_file.write_text(original_content)

    def test_list_real_lingclaude_file(self) -> None:
        result = list_functions("/home/ai/lingclaude/lingclaude/engine/git.py")
        assert result.is_ok
        assert len(result.data) > 0
        # Should find _run_git function
        assert any(f["name"] == "_run_git" for f in result.data)
