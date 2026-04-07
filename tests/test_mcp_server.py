"""灵克 MCP Server 集成测试 — 验证26个工具的注册和基本功能。"""

import os

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

    def test_all_26_tools_registered(self, mcp_server):
        tools = mcp_server._tool_manager.list_tools()
        names = {t.name for t in tools}
        assert len(names) == 26

    def test_core_coding_tools(self, mcp_server):
        tools = mcp_server._tool_manager.list_tools()
        names = {t.name for t in tools}
        expected = {
            "read_file", "write_file", "edit_code", "search_code", "run_bash",
            "index_project", "list_functions", "replace_function",
            "glob", "file_create", "file_insert", "file_delete_lines",
            "file_undo", "analyze_full",
        }
        assert expected.issubset(names)

    def test_version_control_tools(self, mcp_server):
        tools = mcp_server._tool_manager.list_tools()
        names = {t.name for t in tools}
        expected = {"git_status", "git_log", "git_diff", "git_blame"}
        assert expected.issubset(names)

    def test_self_optimization_tools(self, mcp_server):
        tools = mcp_server._tool_manager.list_tools()
        names = {t.name for t in tools}
        expected = {"evaluate_code", "run_optimization", "get_advice", "check_triggers"}
        assert expected.issubset(names)

    def test_knowledge_and_session_tools(self, mcp_server):
        tools = mcp_server._tool_manager.list_tools()
        names = {t.name for t in tools}
        expected = {"knowledge_search", "session_list", "stt", "check_and_optimize"}
        assert expected.issubset(names)

    def test_tool_descriptions_use_ling_naming(self, mcp_server):
        tools = mcp_server._tool_manager.list_tools()
        ling_names = {
            "灵读", "灵写", "灵编", "灵查", "灵动", "灵索", "灵析", "灵构",
            "灵态", "灵史", "灵异", "灵评", "灵优", "灵谏", "灵检",
            "灵巡", "灵创", "灵插", "灵删", "灵撤", "灵鉴", "灵溯",
            "灵忆", "灵簿", "灵听", "灵自审",
        }
        descriptions = {t.description for t in tools}
        for name in ling_names:
            assert any(name in d for d in descriptions), f"灵系命名 '{name}' 未出现在任何工具描述中"


class TestReadFile:
    def test_read_existing_file(self, sample_project):
        from lingclaude.mcp.server import tool_read_file
        result = tool_read_file("main.py", working_dir=str(sample_project))
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
        result = tool_write_file("new.py", "print('hello')", working_dir=str(tmp_path))
        assert isinstance(result, dict)
        target = tmp_path / "new.py"
        assert target.exists()
        assert target.read_text() == "print('hello')"


class TestEditCode:
    def test_replace_text(self, tmp_path):
        from lingclaude.mcp.server import tool_write_file, tool_edit_code
        tool_write_file("edit.py", "x = 1\ny = 2\n", working_dir=str(tmp_path))
        result = tool_edit_code("edit.py", "x = 1", "x = 42", working_dir=str(tmp_path))
        assert isinstance(result, dict)
        content = (tmp_path / "edit.py").read_text()
        assert "x = 42" in content


class TestSearchCode:
    def test_search_pattern(self, sample_project):
        from lingclaude.mcp.server import tool_search_code
        result = tool_search_code("def hello", include="*.py", working_dir=str(sample_project))
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

    def test_git_blame(self, sample_project):
        from lingclaude.mcp.server import tool_git_blame
        result = tool_git_blame("main.py", cwd=str(sample_project))
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


class TestGlob:
    def test_glob_py_files(self, sample_project):
        from lingclaude.mcp.server import tool_glob
        result = tool_glob("*.py", working_dir=str(sample_project))
        assert isinstance(result, dict)
        files = result.get("files", [])
        assert len(files) >= 1

    def test_glob_no_match(self, tmp_path):
        from lingclaude.mcp.server import tool_glob
        result = tool_glob("*.xyz", working_dir=str(tmp_path))
        assert isinstance(result, dict)
        assert result.get("files") is not None


class TestFileCreate:
    def test_create_file(self, tmp_path):
        from lingclaude.mcp.server import tool_file_create
        result = tool_file_create("created.py", "x = 1\n", working_dir=str(tmp_path))
        assert isinstance(result, dict)
        assert (tmp_path / "created.py").exists()
        assert (tmp_path / "created.py").read_text() == "x = 1\n"


class TestFileInsert:
    def test_insert_line(self, tmp_path):
        from lingclaude.mcp.server import tool_write_file, tool_file_insert
        tool_write_file("ins.py", "line1\nline3\n", working_dir=str(tmp_path))
        result = tool_file_insert("ins.py", 2, "line2\n", working_dir=str(tmp_path))
        assert isinstance(result, dict)
        content = (tmp_path / "ins.py").read_text()
        assert "line2" in content


class TestFileDeleteLines:
    def test_delete_lines(self, tmp_path):
        from lingclaude.mcp.server import tool_write_file, tool_file_delete_lines
        tool_write_file("del.py", "a\nb\nc\nd\n", working_dir=str(tmp_path))
        result = tool_file_delete_lines("del.py", 2, 3, working_dir=str(tmp_path))
        assert isinstance(result, dict)
        content = (tmp_path / "del.py").read_text()
        assert "c" not in content
        assert "a" in content


class TestFileUndo:
    def test_undo_edit(self, tmp_path):
        from lingclaude.mcp.server import tool_write_file, tool_edit_code, tool_file_undo
        tool_write_file("undo.py", "original\n", working_dir=str(tmp_path))
        tool_edit_code("undo.py", "original", "modified", working_dir=str(tmp_path))
        assert "modified" in (tmp_path / "undo.py").read_text()
        result = tool_file_undo("undo.py", working_dir=str(tmp_path))
        assert result is not None
        assert "original" in (tmp_path / "undo.py").read_text()


class TestAnalyzeFull:
    def test_analyze(self, sample_project):
        from lingclaude.mcp.server import tool_analyze_full
        result = tool_analyze_full(str(sample_project))
        assert isinstance(result, dict)
        assert "avg_class_size" in result or "violations" in result or "findings" in result


class TestKnowledgeSearch:
    def test_search_returns_dict(self):
        from lingclaude.mcp.server import tool_knowledge_search
        result = tool_knowledge_search("test")
        assert isinstance(result, dict)
        assert "keyword" in result
        assert result["keyword"] == "test"


class TestSessionList:
    def test_list_returns_dict(self):
        from lingclaude.mcp.server import tool_session_list
        result = tool_session_list()
        assert isinstance(result, dict)
        assert "total" in result
        assert "sessions" in result


class TestSTT:
    def test_stt_not_available(self):
        from lingclaude.mcp.server import tool_stt
        result = tool_stt()
        assert isinstance(result, dict)
        if not result.get("ok", False):
            assert "error" in result


class TestCheckAndOptimize:
    def test_check_and_optimize_no_trigger(self, sample_project):
        from lingclaude.mcp.server import tool_check_and_optimize
        result = tool_check_and_optimize(target=str(sample_project))
        assert isinstance(result, dict)
        assert "triggered" in result
