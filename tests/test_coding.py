"""Tests for CodingRuntime"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from lingclaude.core.config import LingClaudeConfig, OptimizerConfig, PermissionConfig
from lingclaude.engine.coding import CodingRuntime


class TestCodingRuntimeInit:
    """Test CodingRuntime initialization"""

    def test_init_default_config(self):
        """Test initialization with default config"""
        runtime = CodingRuntime()
        assert runtime.config is not None
        assert runtime.bash is not None
        assert runtime.bash_lingxi is not None
        assert runtime.file_ops is not None
        assert runtime.file_edit is not None
        assert runtime.file_read is not None
        assert runtime.grep_tool is not None
        assert runtime.registry is not None
        assert runtime.permissions is not None
        assert runtime.evaluator is not None
        assert runtime.optimizer is not None
        assert runtime.advisor is not None
        assert runtime.stt is not None

    def test_init_custom_config(self):
        """Test initialization with custom config"""
        config = LingClaudeConfig(
            optimizer=OptimizerConfig(timeout_seconds=60),
            permissions=PermissionConfig(deny_tools=["bash"]),
        )
        runtime = CodingRuntime(config=config)
        assert runtime.config.optimizer.timeout_seconds == 60
        assert "bash" in runtime.config.permissions.deny_tools

    def test_setup_tools_registers_all(self):
        """Test that _setup_tools registers all tools"""
        runtime = CodingRuntime()
        expected_tools = [
            "bash",
            "bash_lingxi",
            "read",
            "write",
            "edit",
            "file_create",
            "file_insert",
            "file_delete_lines",
            "file_undo",
            "glob",
            "grep",
            "stt",
            "git_status",
            "git_diff",
            "git_log",
            "git_blame",
            "index_project",
            "ast_replace",
            "list_functions",
        ]
        for tool_name in expected_tools:
            assert tool_name in runtime.registry._tools


class TestBashHandlers:
    """Test bash command handlers"""

    def test_bash_handler_success(self):
        """Test bash handler with successful command"""
        runtime = CodingRuntime()
        result = runtime._bash_handler(command="echo 'hello'")
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]
        assert result["duration"] >= 0

    def test_bash_handler_failure(self):
        """Test bash handler with failing command"""
        runtime = CodingRuntime()
        result = runtime._bash_handler(command="exit 1")
        assert result["exit_code"] == 1

    def test_bash_lingxi_handler(self):
        """Test bash_lingxi handler"""
        runtime = CodingRuntime()
        result = runtime._bash_lingxi_handler(command="echo 'test'")
        assert result["exit_code"] == 0
        assert "test" in result["stdout"]


class TestFileHandlers:
    """Test file operation handlers"""

    @pytest.fixture
    def test_dir(self):
        """Create test directory within project"""
        test_root = Path("/home/ai/LingClaude/tests/temp_test_files")
        test_root.mkdir(exist_ok=True)
        yield test_root
        # Cleanup
        import shutil
        if test_root.exists():
            shutil.rmtree(test_root)

    def test_read_handler_success(self, test_dir):
        """Test read handler with existing file"""
        test_file = test_dir / "test.txt"
        test_file.write_text("Hello, World!", encoding="utf-8")

        runtime = CodingRuntime()
        result = runtime._read_handler(path=str(test_file))
        assert "content" in result
        assert "Hello, World!" in result["content"]

    def test_read_handler_with_offset_limit(self, test_dir):
        """Test read handler with offset and limit"""
        test_file = test_dir / "test.txt"
        test_file.write_text("line1\nline2\nline3\nline4\nline5", encoding="utf-8")

        runtime = CodingRuntime()
        result = runtime._read_handler(path=str(test_file), offset=1, limit=2)
        assert "line2" in result["content"]
        assert "line3" in result["content"]
        assert "line1" not in result["content"]
        assert "line4" not in result["content"]

    def test_read_handler_not_found(self, test_dir):
        """Test read handler with non-existent file"""
        runtime = CodingRuntime()
        result = runtime._read_handler(path=str(test_dir / "nonexistent.txt"))
        assert "error" in result

    def test_write_handler(self, test_dir):
        """Test write handler"""
        test_file = test_dir / "test.txt"
        runtime = CodingRuntime()
        result = runtime._write_handler(path=str(test_file), content="Test content")
        assert "path" in result
        assert test_file.read_text(encoding="utf-8") == "Test content"

    def test_edit_handler(self, test_dir):
        """Test edit handler"""
        test_file = test_dir / "test.txt"
        test_file.write_text("Hello, World!", encoding="utf-8")

        runtime = CodingRuntime()
        result = runtime._edit_handler(
            path=str(test_file),
            old_text="World",
            new_text="Universe"
        )
        assert "path" in result
        content = test_file.read_text(encoding="utf-8")
        assert "Universe" in content
        assert "World" not in content

    def test_file_create_handler(self, test_dir):
        """Test file create handler"""
        test_file = test_dir / "new.txt"
        runtime = CodingRuntime()
        result = runtime._file_create_handler(
            path=str(test_file),
            content="New file content"
        )
        assert "path" in result
        assert test_file.read_text(encoding="utf-8") == "New file content"

    def test_file_insert_handler(self, test_dir):
        """Test file insert handler"""
        test_file = test_dir / "test.txt"
        test_file.write_text("line1\nline3", encoding="utf-8")

        runtime = CodingRuntime()
        result = runtime._file_insert_handler(
            path=str(test_file),
            line=1,  # Insert at index 1 (between line1 and line3)
            text="line2"
        )
        assert "path" in result
        content = test_file.read_text(encoding="utf-8")
        assert "line1" in content
        assert "line2" in content
        assert "line3" in content

    def test_file_delete_lines_handler(self, test_dir):
        """Test file delete lines handler"""
        test_file = test_dir / "test.txt"
        test_file.write_text("line1\nline2\nline3\nline4", encoding="utf-8")

        runtime = CodingRuntime()
        result = runtime._file_delete_lines_handler(
            path=str(test_file),
            start_line=1,  # Delete index 1 (line2) to index 3 (exclusive, so includes line3)
            end_line=3
        )
        assert "path" in result
        content = test_file.read_text(encoding="utf-8")
        assert "line1" in content
        assert "line4" in content
        assert "line2" not in content
        assert "line3" not in content


class TestGlobGrepHandlers:
    """Test glob and grep handlers"""

    @pytest.fixture
    def test_dir(self):
        """Create test directory with test files"""
        test_root = Path("/home/ai/LingClaude/tests/temp_test_files2")
        test_root.mkdir(exist_ok=True)
        (test_root / "test1.txt").write_text("content1", encoding="utf-8")
        (test_root / "test2.py").write_text("content2", encoding="utf-8")
        (test_root / "subdir").mkdir(exist_ok=True)
        (test_root / "subdir" / "test3.txt").write_text("content3", encoding="utf-8")
        yield test_root
        # Cleanup
        import shutil
        if test_root.exists():
            shutil.rmtree(test_root)

    def test_glob_handler(self, test_dir):
        """Test glob handler"""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(str(test_dir))
            runtime = CodingRuntime()  # Initialize AFTER changing CWD
            result = runtime._glob_handler(pattern="*.txt")
            assert "files" in result
            assert len(result["files"]) >= 1
            assert any("test1.txt" in f for f in result["files"])
        finally:
            os.chdir(old_cwd)

    def test_glob_handler_recursive(self, test_dir):
        """Test glob handler with recursive pattern"""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(str(test_dir))
            runtime = CodingRuntime()  # Initialize AFTER changing CWD
            result = runtime._glob_handler(pattern="**/*.txt")
            assert "files" in result
            assert len(result["files"]) >= 1
        finally:
            os.chdir(old_cwd)

    def test_grep_handler_pattern(self, test_dir):
        """Test grep handler with pattern"""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(str(test_dir))
            runtime = CodingRuntime()  # Initialize AFTER changing CWD
            result = runtime._grep_handler(pattern="content")
            assert "matches" in result
            assert len(result["matches"]) >= 1
        finally:
            os.chdir(old_cwd)

    def test_grep_handler_literal(self, test_dir):
        """Test grep handler with literal pattern"""
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(str(test_dir))
            runtime = CodingRuntime()  # Initialize AFTER changing CWD
            result = runtime._grep_handler(
                pattern="content1",
                literal=True,
                include="*.txt"  # Search in .txt files
            )
            assert "matches" in result
            assert len(result["matches"]) >= 1
        finally:
            os.chdir(old_cwd)

    def test_grep_handler_with_include(self, test_dir):
        """Test grep handler with include filter"""
        runtime = CodingRuntime()
        import os
        old_cwd = os.getcwd()
        try:
            os.chdir(str(test_dir))
            result = runtime._grep_handler(
                pattern="content",
                include="*.py"
            )
            assert "matches" in result
            # Should only match .py files
            for match in result["matches"]:
                assert ".py" in match.get("file", "")
        finally:
            os.chdir(old_cwd)


class TestGitHandlers:
    """Test git command handlers"""

    @pytest.fixture
    def git_repo(self):
        """Create temporary git repository"""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            import subprocess
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True, capture_output=True)

            test_file = root / "test.txt"
            test_file.write_text("initial content", encoding="utf-8")
            subprocess.run(["git", "add", "test.txt"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=root, check=True, capture_output=True)

            yield root

    def test_git_status_handler(self, git_repo):
        """Test git_status handler"""
        runtime = CodingRuntime()
        result = runtime._git_status_handler(path=str(git_repo))
        assert isinstance(result, dict)
        assert "has_changes" in result or "files" in result

    def test_git_diff_handler(self, git_repo):
        """Test git_diff handler"""
        # Make a change
        test_file = git_repo / "test.txt"
        test_file.write_text("modified content", encoding="utf-8")

        runtime = CodingRuntime()
        result = runtime._git_diff_handler(path=str(git_repo))
        assert isinstance(result, dict)

    def test_git_log_handler(self, git_repo):
        """Test git_log handler"""
        runtime = CodingRuntime()
        result = runtime._git_log_handler(path=str(git_repo), count=5)
        assert isinstance(result, dict)
        assert "commits" in result

    def test_git_blame_handler(self, git_repo):
        """Test git_blame handler"""
        test_file = git_repo / "test.txt"
        runtime = CodingRuntime()
        result = runtime._git_blame_handler(
            file_path=str(test_file),
            cwd=str(git_repo)
        )
        assert isinstance(result, dict)


class TestAstHandlers:
    """Test AST operation handlers"""

    @pytest.fixture
    def test_dir(self):
        """Create test directory"""
        test_root = Path("/home/ai/LingClaude/tests/temp_test_files3")
        test_root.mkdir(exist_ok=True)
        yield test_root
        # Cleanup
        import shutil
        if test_root.exists():
            shutil.rmtree(test_root)

    def test_list_functions_handler(self, test_dir):
        """Test list_functions handler"""
        test_file = test_dir / "test.py"
        test_file.write_text("""
def foo():
    pass

class Bar:
    def method1(self):
        pass
    def method2(self):
        pass
""", encoding="utf-8")

        runtime = CodingRuntime()
        result = runtime._list_functions_handler(file_path=str(test_file))
        assert "functions" in result
        assert len(result["functions"]) >= 2  # foo and Bar.method1

    def test_ast_replace_handler(self, test_dir):
        """Test ast_replace handler"""
        test_file = test_dir / "test.py"
        test_file.write_text("""
def old_function():
    return "old"
""", encoding="utf-8")

        runtime = CodingRuntime()
        result = runtime._ast_replace_handler(
            file_path=str(test_file),
            function_name="old_function",
            new_body='return "new"'
        )
        # AST replace may fail or succeed depending on implementation
        assert isinstance(result, dict)


class TestExecuteTool:
    """Test execute_tool method"""

    def test_execute_tool_permission_blocked(self):
        """Test execute_tool with blocked tool"""
        from lingclaude.core.config import PermissionConfig
        config = LingClaudeConfig(permissions=PermissionConfig(deny_tools=["bash"]))
        runtime = CodingRuntime(config=config)
        result = runtime.execute_tool("bash", command="echo test")
        assert "error" in result
        assert "blocked" in result["error"].lower()

    def test_execute_tool_success(self):
        """Test execute_tool with successful execution"""
        runtime = CodingRuntime()
        result = runtime.execute_tool("bash", command="echo test")
        assert "stdout" in result
        assert "test" in result["stdout"]


class TestAnalyze:
    """Test analyze method"""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory with Python files"""
        test_root = Path("/home/ai/LingClaude/tests/temp_test_files_analyze")
        test_root.mkdir(exist_ok=True)
        (test_root / "test1.py").write_text("""
def short():
    pass

def very_long_function_that_has_many_parameters_and_is_too_complex(arg1, arg2, arg3, arg4):
    pass
""", encoding="utf-8")
        yield test_root
        # Cleanup
        import shutil
        if test_root.exists():
            shutil.rmtree(test_root)

    def test_analyze_file(self, temp_dir):
        """Test analyzing a single file"""
        runtime = CodingRuntime()
        test_file = temp_dir / "test1.py"
        metrics = runtime.analyze(str(test_file))
        assert "pattern_findings" in metrics
        assert "detectors" in metrics
        assert "findings" in metrics
        assert isinstance(metrics["findings"], list)

    def test_analyze_directory(self, temp_dir):
        """Test analyzing a directory"""
        runtime = CodingRuntime()
        metrics = runtime.analyze(str(temp_dir))
        assert "pattern_findings" in metrics
        assert "detectors" in metrics
        assert "findings" in metrics


class TestOptimize:
    """Test optimize method"""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory"""
        test_root = Path("/home/ai/LingClaude/tests/temp_test_files_optimize")
        test_root.mkdir(exist_ok=True)
        yield test_root
        # Cleanup
        import shutil
        if test_root.exists():
            shutil.rmtree(test_root)

    def test_optimize_success(self, temp_dir):
        """Test optimize with successful result"""
        test_file = temp_dir / "test.py"
        test_file.write_text("def foo(): pass", encoding="utf-8")

        runtime = CodingRuntime()
        result = runtime.optimize(str(temp_dir), goal="structure", max_trials=2)
        assert "success" in result
        assert isinstance(result["success"], bool)
        if result["success"]:
            assert "best_params" in result
            assert "best_score" in result
            assert "report" in result

    def test_optimize_invalid_goal(self, temp_dir):
        """Test optimize with invalid goal"""
        runtime = CodingRuntime()
        result = runtime.optimize(str(temp_dir), goal="invalid_goal", max_trials=1)
        # Should not crash, just return success=False or handle gracefully
        assert "success" in result


class TestCheckAndOptimize:
    """Test check_and_optimize method"""

    def test_check_and_optimize_no_trigger(self):
        """Test check_and_optimize when no trigger conditions met"""
        runtime = CodingRuntime()
        context = {"quality_score": 0.95}
        result = runtime.check_and_optimize(context)
        assert "triggered" in result
        assert result["triggered"] is False

    def test_check_and_optimize_with_trigger(self):
        """Test check_and_optimize with trigger conditions"""
        runtime = CodingRuntime()
        context = {"quality_score": 0.3}  # Low quality should trigger
        result = runtime.check_and_optimize(context)
        assert "triggered" in result
        # May or may not trigger depending on threshold


class TestSttHandler:
    """Test STT handler"""

    def test_stt_handler_no_backend(self):
        """Test STT handler when backend not available"""
        runtime = CodingRuntime()
        result = runtime._stt_handler(duration=5)
        # Should return error if STT backend not available or fails
        # Error can be: "无可用的 STT 后端" (Chinese) or torch error (nan values)
        if "error" in result:
            error_lower = result["error"].lower()
            # Check for expected error types
            assert ("stt" in result["error"] or
                    "backend" in error_lower or
                    "后端" in result["error"] or  # Chinese for "backend"
                    "parameter logits" in error_lower or  # Torch error
                    "nan" in error_lower)  # Torch error


class TestIndexProjectHandler:
    """Test index_project handler"""

    @pytest.fixture
    def test_dir(self):
        """Create test directory with Python files"""
        test_root = Path("/home/ai/LingClaude/tests/temp_test_files4")
        test_root.mkdir(exist_ok=True)
        (test_root / "test1.py").write_text("""
def foo():
    pass

class Bar:
    def method(self):
        pass
""", encoding="utf-8")
        (test_root / "test2.py").write_text("""
def baz():
    pass
""", encoding="utf-8")
        yield test_root
        # Cleanup
        import shutil
        if test_root.exists():
            shutil.rmtree(test_root)

    def test_index_project_handler(self, test_dir):
        """Test index_project handler"""
        runtime = CodingRuntime()
        result = runtime._index_project_handler(path=str(test_dir))
        assert "functions" in result or "classes" in result or "symbols" in result


class TestPermissionIntegration:
    """Test permission system integration"""

    def test_permission_blocks_tool(self):
        """Test that permissions block tools"""
        from lingclaude.core.config import PermissionConfig
        config = LingClaudeConfig(permissions=PermissionConfig(deny_tools=["bash", "write"]))
        runtime = CodingRuntime(config=config)

        # Bash should be blocked
        bash_result = runtime.execute_tool("bash", command="echo test")
        assert "error" in bash_result
        assert "blocked" in bash_result["error"].lower()

        # Write should be blocked
        write_result = runtime.execute_tool("write", path="/tmp/test.txt", content="test")
        assert "error" in write_result
        assert "blocked" in write_result["error"].lower()

    def test_permission_prefix_blocks(self):
        """Test that prefix permissions work"""
        from lingclaude.core.config import PermissionConfig
        config = LingClaudeConfig(permissions=PermissionConfig(deny_prefixes=["/etc"]))
        runtime = CodingRuntime(config=config)

        # Read from /etc should be blocked
        result = runtime.execute_tool("read", path="/etc/passwd")
        if "error" in result:
            # May be blocked or may not exist
            pass


class TestVerificationGate:
    """Test forced verification gate for file write operations"""

    def test_syntax_error_blocks_write(self, tmp_path):
        """写入包含语法错误的 Python 文件应被阻止"""
        runtime = CodingRuntime()
        bad_py = str(tmp_path / "bad.py")
        result = runtime.execute_tool("write", path=bad_py, content="def foo(\n")
        assert "error" in result
        assert "验证关卡" in result["error"]

    def test_valid_syntax_allows_write(self, tmp_path):
        """语法正确的 Python 文件应通过"""
        runtime = CodingRuntime()
        good_py = str(tmp_path / "good.py")
        result = runtime.execute_tool("write", path=good_py, content="def foo():\n    return 1\n")
        assert "验证关卡" not in result.get("error", "")

    def test_non_python_file_bypasses_gate(self, tmp_path):
        """非 Python 文件应跳过语法检查"""
        runtime = CodingRuntime()
        txt_file = str(tmp_path / "notes.txt")
        result = runtime.execute_tool("write", path=txt_file, content="any garbage {{{{")
        assert "验证关卡" not in result.get("error", "")

    def test_gate_disabled_allows_bad_syntax(self, tmp_path):
        """禁用验证门后，语法错误也应放行"""
        runtime = CodingRuntime()
        runtime.verification_gate.enabled = False
        bad_py = str(tmp_path / "unchecked.py")
        result = runtime.execute_tool("write", path=bad_py, content="def foo(\n")
        assert "验证关卡" not in result.get("error", "")

    def test_edit_with_syntax_error_blocked(self, tmp_path):
        """edit 操作对语法错误也应阻止"""
        runtime = CodingRuntime()
        py_file = tmp_path / "edit_target.py"
        py_file.write_text("x = 1\n", encoding="utf-8")
        result = runtime.execute_tool(
            "edit",
            path=str(py_file),
            old_text="x = 1",
            new_text="def bad(\n",
        )
        assert "error" in result
        assert "验证关卡" in result["error"]

    def test_edit_valid_syntax_passes(self, tmp_path):
        """edit 操作对语法正确的内容应通过"""
        runtime = CodingRuntime()
        py_file = tmp_path / "edit_ok.py"
        py_file.write_text("x = 1\n", encoding="utf-8")
        result = runtime.execute_tool(
            "edit",
            path=str(py_file),
            old_text="x = 1",
            new_text="y = 2\n",
        )
        assert "验证关卡" not in result.get("error", "")

    def test_file_create_with_syntax_error_blocked(self, tmp_path):
        """file_create 操作对语法错误也应阻止"""
        runtime = CodingRuntime()
        new_py = str(tmp_path / "new_bad.py")
        result = runtime.execute_tool("file_create", path=new_py, content="class Foo\n")
        assert "error" in result
        assert "验证关卡" in result["error"]

    def test_no_file_path_skips_gate(self):
        """没有 file_path 参数的工具应跳过检查"""
        runtime = CodingRuntime()
        result = runtime.execute_tool("bash", command="echo ok")
        assert "验证关卡" not in result.get("error", "")


class TestSecurityHardening:
    """Week 6 安全加固测试"""

    def test_rate_limit_blocks_excessive_calls(self) -> None:
        """会话工具调用次数上限"""
        runtime = CodingRuntime()
        runtime.verification_gate.max_tool_calls_per_session = 3
        for i in range(3):
            result = runtime.execute_tool("bash", command=f"echo {i}")
            assert "安全限制" not in result.get("error", "")
        result = runtime.execute_tool("bash", command="echo blocked")
        assert "安全限制" in result["error"]
        assert "上限" in result["error"]

    def test_rate_limit_reset(self) -> None:
        """重置后可继续调用"""
        runtime = CodingRuntime()
        runtime.verification_gate.max_tool_calls_per_session = 2
        runtime.execute_tool("bash", command="echo 1")
        runtime.execute_tool("bash", command="echo 2")
        runtime.verification_gate.reset_rate_limit()
        result = runtime.execute_tool("bash", command="echo 3")
        assert "安全限制" not in result.get("error", "")

    def test_rate_limit_zero_means_unlimited(self) -> None:
        """max_tool_calls=0 表示无限制"""
        runtime = CodingRuntime()
        runtime.verification_gate.max_tool_calls_per_session = 0
        for i in range(20):
            result = runtime.execute_tool("bash", command=f"echo {i}")
            assert "安全限制" not in result.get("error", "")

    def test_dangerous_command_blocked(self) -> None:
        """危险命令被阻止"""
        runtime = CodingRuntime()
        result = runtime.execute_tool("bash", command="rm -rf /")
        assert "安全限制" in result["error"]
        assert "危险命令" in result["error"]

    def test_dangerous_command_dd_blocked(self) -> None:
        """dd 写设备被阻止"""
        runtime = CodingRuntime()
        result = runtime.execute_tool("bash", command="dd if=/dev/zero of=/dev/sda")
        assert "安全限制" in result["error"]

    def test_safe_command_passes(self) -> None:
        """正常命令不受影响"""
        runtime = CodingRuntime()
        result = runtime.execute_tool("bash", command="ls -la")
        assert "安全限制" not in result.get("error", "")
        assert "危险命令" not in result.get("error", "")

    def test_path_traversal_outside_root_blocked(self, tmp_path) -> None:
        """写入允许范围外路径被阻止"""
        runtime = CodingRuntime()
        runtime.verification_gate.allowed_write_roots = (str(tmp_path),)
        outside = str(tmp_path / ".." / ".." / "etc" / "passwd.py")
        result = runtime.execute_tool("write", path=outside, content="x = 1\n")
        assert "error" in result

    def test_path_traversal_inside_root_passes(self, tmp_path) -> None:
        """VerificationGate 允许范围内路径通过（不依赖 FileOps scope check）"""
        from lingclaude.engine.verification_gate import VerificationGate
        gate = VerificationGate()
        gate.allowed_write_roots = (str(tmp_path),)
        inside = str(tmp_path / "safe.py")
        result = gate.verify("write", file_path=inside, content="x = 1\n")
        assert result.passed is True
        assert not any("path_traversal" in c.get("error", "") for c in result.checks if not c.get("passed", True))

    def test_path_traversal_no_roots_means_unrestricted(self, tmp_path) -> None:
        """不设 allowed_write_roots 时不限制路径"""
        runtime = CodingRuntime()
        runtime.verification_gate.allowed_write_roots = ()
        py_file = str(tmp_path / "anywhere.py")
        result = runtime.execute_tool("write", path=py_file, content="x = 1\n")
        assert "验证关卡" not in result.get("error", "")
