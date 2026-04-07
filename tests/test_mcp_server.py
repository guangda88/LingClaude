"""灵克 MCP Server 集成测试 — 验证15个工具的注册和基本功能。"""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def mcp_server():
    from lingclaude.mcp.server import mcp
    return mcp


@pytest.fixture
def sample_project(tmp_path):
    """创建一个测试项目目录，含Python文件和git仓库。"""
    (tmp_path / "main.py").write_text(
        "def hello():\n    return 'world'\n\ndef add(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    (tmp_path / "utils.py").write_text(
        "class Helper:\n    def run(self):\n        pass\n",
        encoding="utf-8",
    )
    os.system(f"cd {tmp_path} && git init && git add -A && git commit -m 'init'")
    return tmp_path


class TestMCPToolRegistration:
    """测试MCP工具注册。"""

    def test_all_15_tools_registered(self, mcp_server):
        tools = mcp_server._tool_manager.list_tools()
        names = {t.name for t in tools}
        assert len(names) == 15

    def test_core_coding_tools(self, mcp_server):
        tools = mcp_server._tool_manager.list_tools()
        names = {t.name for t in tools}
        expected = {"read_file", "write_file", "edit_code", "search_code", "run_bash", "index_project", "list_functions", "replace_function"}
        assert expected.issubset(names)

    def test_version_control_tools(self, mcp_server):
        tools = mcp_server._tool_manager.list_tools()
        names = {t.name for t in tools}
        expected = {"git_status", "git_log", "git_diff"}
        assert expected.issubset(names)

    def test_self_optimization_tools(self, mcp_server):
        tools = mcp_server._tool_manager.list_tools()
        names = {t.name for t in tools}
        expected = {"evaluate_code", "run_optimization", "get_advice", "check_triggers"}
        assert expected.issubset(names)

    def test_tool_descriptions_use_ling_naming(self, mcp_server):
        tools = mcp_server._tool_manager.list_tools()
        ling_names = {"灵读", "灵写", "灵编", "灵查", "灵动", "灵索", "灵析", "灵构",
                      "灵态", "灵史", "灵异", "灵评", "灵优", "灵谏", "灵检"}
        descriptions = {t.description for t in tools}
        for name in ling_names:
            assert any(name in d for d in descriptions), f"灵系命名 '{name}' 未出现在任何工具描述中"


class TestReadFile:
    def test_read_existing_file(self, sample_project):
        from lingclaude.mcp.server import tool_read_file
        result = tool_read_file(str(sample_project / "main.py"))
        assert isinstance(result, dict)
        assert result.get("content") is not None
        assert "hello" in result["content"]

    def test_read_nonexistent_file(self, sample_project):
        from lingclaude.mcp.server import tool_read_file
        result = tool_read_file(str(sample_project / "nonexistent.py"))
        assert isinstance(result, dict)
        assert "error" in result


class TestWriteFile:
    def test_create_new_file(self, tmp_path):
        from lingclaude.mcp.server import tool_write_file
        target = str(tmp_path / "new.py")
        result = tool_write_file(target, "print('hello')")
        assert isinstance(result, dict)
        assert Path(target).exists()
        assert Path(target).read_text() == "print('hello')"


class TestEditCode:
    def test_replace_text(self, tmp_path):
        from lingclaude.mcp.server import tool_write_file, tool_edit_code
        target = str(tmp_path / "edit.py")
        tool_write_file(target, "x = 1\ny = 2\n")
        result = tool_edit_code(target, "x = 1", "x = 42")
        assert isinstance(result, dict)
        content = Path(target).read_text()
        assert "x = 42" in content


class TestSearchCode:
    def test_search_pattern(self, sample_project):
        from lingclaude.mcp.server import tool_search_code
        result = tool_search_code("def hello", include="*.py", path=str(sample_project))
        assert isinstance(result, dict)
        assert result.get("total_matches", 0) >= 1

    def test_search_literal(self, sample_project):
        from lingclaude.mcp.server import tool_search_code
        result = tool_search_code("def hello", include="*.py", literal=True)
        assert isinstance(result, dict)


class TestRunBash:
    def test_simple_command(self, tmp_path):
        from lingclaude.mcp.server import tool_run_bash
        result = tool_run_bash("echo hello", working_dir=str(tmp_path))
        assert isinstance(result, dict)
        assert "hello" in result.get("stdout", "")

    def test_blocked_command(self, tmp_path):
        from lingclaude.mcp.server import tool_run_bash
        result = tool_run_bash("rm -rf /", working_dir=str(tmp_path))
        assert isinstance(result, dict)
        assert result.get("exit_code", 0) != 0


class TestGitTools:
    def test_git_status(self, sample_project):
        from lingclaude.mcp.server import tool_git_status
        result = tool_git_status(str(sample_project))
        assert isinstance(result, dict)

    def test_git_log(self, sample_project):
        from lingclaude.mcp.server import tool_git_log
        result = tool_git_log(str(sample_project), count=5)
        assert isinstance(result, dict)
        commits = result.get("commits", [])
        assert len(commits) >= 1

    def test_git_diff(self, sample_project):
        from lingclaude.mcp.server import tool_git_diff
        result = tool_git_diff(str(sample_project))
        assert isinstance(result, dict)


class TestIndexProject:
    def test_index(self, sample_project):
        from lingclaude.mcp.server import tool_index_project
        result = tool_index_project(str(sample_project))
        assert isinstance(result, dict)
        assert result.get("files_scanned", 0) >= 1


class TestListFunctions:
    def test_list_functions(self, sample_project):
        from lingclaude.mcp.server import tool_list_functions
        result = tool_list_functions(str(sample_project / "main.py"))
        assert isinstance(result, list)
        assert len(result) >= 2


class TestEvaluateCode:
    def test_evaluate(self, sample_project):
        from lingclaude.mcp.server import tool_evaluate_code
        result = tool_evaluate_code(str(sample_project))
        assert isinstance(result, dict)


class TestCheckTriggers:
    def test_check_defaults(self, sample_project):
        from lingclaude.mcp.server import tool_check_triggers
        result = tool_check_triggers(str(sample_project))
        assert isinstance(result, dict)
        assert "should_optimize" in result


class TestGetAdvice:
    def test_advice(self, sample_project):
        from lingclaude.mcp.server import tool_get_advice
        result = tool_get_advice(target=str(sample_project))
        assert isinstance(result, str)
        assert len(result) > 0
