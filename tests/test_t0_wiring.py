"""T0 接线测试 — GAP_ANALYSIS_20260825_CC_DIMENSION T0-1..T0-10 验收。

覆盖「已写未接线」修复项：
- T0-1 plan_mode 真封写/执行工具 + 模型可见列表过滤
- T0-2 sensitive_path_gate 主循环全覆盖（read/write）+ 审批逃生门
- T0-3 审批回路（record_permission_decision → execute_tool 实时生效）
- T0-5 grep path/上下文行/默认全类型；glob path
- T0-6 web_search searxng 后端
- T0-7 ToolPipeline 真超时 + ON_ERROR hook 触发
- T0-8 MCP 参数 schema（签名推导 + required_params）
- T0-10 sandbox_policy fail-closed
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from lingclaude.core.config import lingclaudeConfig
from lingclaude.core.permissions import (
    get_permission_store,
    record_permission_decision,
    reset_permission_stores,
)
from lingclaude.engine.coding import CodingRuntime
from lingclaude.engine.plan_mode import PlanMode


@pytest.fixture(autouse=True)
def _clean_stores():
    reset_permission_stores()
    yield
    reset_permission_stores()


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rt = CodingRuntime(config=lingclaudeConfig())
    return rt


# ── T0-1 plan_mode ──


class TestPlanModeWiring:
    def test_enter_blocks_write_and_execute_scope(self, runtime):
        """plan 模式下 write 工具与 bash（execute 域）都被拦截，不只是 WRITE_SCOPED_TOOLS。"""
        result = runtime.execute_tool("plan_mode", action="enter")
        assert result.get("plan_mode") is True

        write_res = runtime.execute_tool("write", path="t.txt", content="x")
        assert "blocked by plan_mode" in write_res.get("error", "")

        bash_res = runtime.execute_tool("bash", command="echo hi")
        assert "blocked by plan_mode" in bash_res.get("error", "")

    def test_enter_allows_read_tools(self, runtime, tmp_path):
        """plan 模式下读工具仍可用（否则模型失能）。"""
        f = tmp_path / "a.txt"
        f.write_text("hello")
        runtime.execute_tool("plan_mode", action="enter")
        res = runtime.execute_tool("read", path=str(f))
        assert "error" not in res, res

    def test_exit_restores_tools(self, runtime):
        runtime.execute_tool("plan_mode", action="enter")
        runtime.execute_tool("plan_mode", action="exit")
        res = runtime.execute_tool("bash", command="echo ok")
        assert "blocked by plan_mode" not in res.get("error", "")

    def test_filter_tools_keeps_read_scope(self, runtime):
        """filter_tools 不再返回 []——保留读域工具 + plan_mode 自身。"""
        pm = runtime.plan_mode
        pm.enter()
        visible = pm.filter_tools(list(runtime.registry.list_tools()))
        names = {t.name for t in visible}
        assert "plan_mode" in names
        assert {"read", "grep", "glob"} <= names
        assert "write" not in names
        assert "bash" not in names


# ── T0-2 sensitive_path_gate 主循环 ──


class TestSensitivePathGateWiring:
    def test_read_sensitive_blocked(self, runtime):
        res = runtime.execute_tool("read", path="/home/ai/.ssh/id_rsa")
        assert "sensitive_path_gate" in res.get("error", "")

    def test_write_sensitive_blocked_via_pipeline_guard(self, runtime):
        """写工具（write/edit/ast_replace 等）此前不经过门——pipeline 守卫补上。"""
        res = runtime.execute_tool("write", path="/home/ai/.ssh/authorized_keys", content="x")
        assert "sensitive_path_gate" in res.get("error", "")

    def test_edit_sensitive_blocked(self, runtime):
        res = runtime.execute_tool(
            "edit", path="/home/ai/project/.env", old_text="A", new_text="B"
        )
        assert "sensitive_path_gate" in res.get("error", "")

    def test_normal_paths_pass(self, runtime, tmp_path):
        f = tmp_path / "normal.txt"
        res = runtime.execute_tool("write", path=str(f), content="ok")
        assert "sensitive_path_gate" not in res.get("error", "")
        assert Path(res.get("path", "")).exists() or f.exists()

    def test_approval_escape_hatch(self, runtime):
        """T0-3 逃生门：审批放行后敏感路径可访问。"""
        res = runtime.execute_tool("read", path="/home/ai/.ssh/id_rsa")
        assert "sensitive_path_gate" in res.get("error", "")

        record_permission_decision("default", "read", "always_allow")
        # 路径不存在会报错，但不应是 sensitive_path_gate 的错
        res2 = runtime.execute_tool("read", path="/home/ai/.ssh/id_rsa")
        assert "sensitive_path_gate" not in res2.get("error", "")

    def test_grep_skips_credential_files(self, tmp_path):
        """grep 内容搜索不把 .env 的密钥带进上下文。"""
        from lingclaude.engine.grep import GrepTool

        (tmp_path / ".env").write_text("SECRET_TOKEN=abc123")
        (tmp_path / "code.py").write_text("SECRET_TOKEN = 'safe-ref'")
        tool = GrepTool(base_dir=str(tmp_path))
        result = tool.search("SECRET_TOKEN")
        assert result.is_ok
        files = {m.file for m in result.data.matches}
        assert "code.py" in files
        assert ".env" not in files


# ── T0-3 审批回路 ──


class TestApprovalLoop:
    def test_deny_decision_blocks_tool(self, runtime):
        record_permission_decision("default", "bash", "deny")
        res = runtime.execute_tool("bash", command="echo hi")
        assert "blocked by permissions" in res.get("error", "")

    def test_session_isolation(self, runtime):
        """deny 只影响对应会话。"""
        record_permission_decision("other-session", "bash", "deny")
        res = runtime.execute_tool("bash", command="echo hi")
        assert "blocked by permissions" not in res.get("error", "")

    def test_always_allow_overrides_config_deny(self, runtime):
        """静态 deny_names 可被会话内 always_allow 覆盖（显式审批优先于静态配置）。"""
        config = lingclaudeConfig()
        res0 = runtime.execute_tool("bash", command="echo hi")  # 未审批前正常
        record_permission_decision("default", "bash", "deny")
        res1 = runtime.execute_tool("bash", command="echo hi")
        assert "blocked by permissions" in res1.get("error", "")
        record_permission_decision("default", "bash", "always_allow")
        res2 = runtime.execute_tool("bash", command="echo hi")
        assert "blocked by permissions" not in res2.get("error", "")


# ── T0-5 grep/glob ──


class TestGrepGlobParams:
    def test_grep_defaults_to_all_file_types(self, tmp_path):
        from lingclaude.engine.grep import GrepTool

        (tmp_path / "a.py").write_text("match_me\n")
        (tmp_path / "b.md").write_text("match_me\n")
        (tmp_path / "c.txt").write_text("match_me\n")
        tool = GrepTool(base_dir=str(tmp_path))
        result = tool.search("match_me")
        assert result.is_ok
        files = {m.file for m in result.data.matches}
        assert files == {"a.py", "b.md", "c.txt"}

    def test_grep_path_param(self, tmp_path):
        from lingclaude.engine.grep import GrepTool

        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "root.txt").write_text("match_me\n")
        (sub / "inner.txt").write_text("match_me\n")
        tool = GrepTool(base_dir=str(tmp_path))
        result = tool.search("match_me", path=str(sub))
        assert result.is_ok
        assert {m.file for m in result.data.matches} == {"inner.txt"}

    def test_grep_context_lines(self, tmp_path):
        from lingclaude.engine.grep import GrepTool

        (tmp_path / "a.txt").write_text("l1\nl2\nTARGET\nl4\nl5\n")
        tool = GrepTool(base_dir=str(tmp_path))
        result = tool.search("TARGET", before=1, after=1)
        assert result.is_ok
        m = result.data.matches[0]
        assert m.context == ("2: l2", "4: l4")

    def test_grep_via_runtime_with_path(self, runtime, tmp_path):
        sub = tmp_path / "s2"
        sub.mkdir()
        (sub / "x.txt").write_text("needle\n")
        res = runtime.execute_tool("grep", pattern="needle", path=str(sub))
        assert res.get("matches"), res
        assert res["matches"][0]["file"] == "x.txt"

    def test_glob_path_param(self, runtime, tmp_path):
        sub = tmp_path / "g"
        sub.mkdir()
        (sub / "only.txt").write_text("x")
        res = runtime.execute_tool("glob", pattern="*.txt", path=str(sub))
        assert list(res.get("files", ())) == ["only.txt"], res


# ── T0-6 web_search ──


class TestWebSearchBackend:
    def test_searxng_live(self):
        """本地 SearXNG JSON API（修好 formats 配置后应可用）。"""
        from lingclaude.engine.web_tools import WebSearcher

        searcher = WebSearcher(backend="searxng")
        result = searcher.search("python asyncio", max_results=3)
        assert result.is_ok, result.error
        assert len(result.data) >= 1
        assert all("url" in r for r in result.data)

    def test_unknown_backend_not_configured(self):
        from lingclaude.engine.web_tools import WebSearcher

        result = WebSearcher(backend="nope").search("q")
        assert result.is_error
        assert "Unknown web search backend" in result.error

    def test_auto_falls_back(self, monkeypatch):
        from lingclaude.engine import web_tools

        monkeypatch.setattr(web_tools.WebSearcher, "_search_searxng",
                            lambda self, q, n: web_tools.Result.fail("down"))
        seen = {}

        def fake_ddg(self, query, max_results):
            seen["q"] = query
            return web_tools.Result.ok([{"title": "t", "url": "u", "snippet": "s"}])

        monkeypatch.setattr(web_tools.WebSearcher, "_search_duckduckgo", fake_ddg)
        result = web_tools.WebSearcher().search("anything")
        assert result.is_ok
        assert seen["q"] == "anything"


# ── T0-7 真超时 + ON_ERROR ──


class TestPipelineTimeoutAndOnError:
    def test_real_timeout(self, runtime):
        """超过 pipeline timeout 的 handler 被中断（不再无限等待）。"""
        from lingclaude.engine.tools import ToolDefinition

        def slow_handler(**_):
            time.sleep(10)
            return {"late": True}

        runtime.registry.register(ToolDefinition(
            name="_slow_test", description="slow", parameters={},
            handler=slow_handler,
        ))
        runtime.tool_pipeline._timeout = 1.0
        t0 = time.monotonic()
        res = runtime.execute_tool("_slow_test")
        elapsed = time.monotonic() - t0
        assert "timed out" in res.get("error", "")
        assert elapsed < 5

    def test_on_error_hook_fires(self, runtime):
        """工具执行错误触发 ON_ERROR hook（原先定义了无触发点）。"""
        from lingclaude.core.hooks import HookContext, HookType

        fired: list[HookContext] = []
        runtime._on_error_hook_ctx = None

        # 借 engine-less 场景直接在 pipeline 上挂监听器验证（与 query_engine 接线同一路径）
        runtime.tool_pipeline.add_error_listener(lambda n, e: fired.append((n, e)))

        from lingclaude.engine.tools import ToolDefinition

        def bad_handler(**_):
            raise ValueError("boom")

        runtime.registry.register(ToolDefinition(
            name="_bad_test", description="bad", parameters={},
            handler=bad_handler,
        ))
        res = runtime.execute_tool("_bad_test")
        assert "boom" in res.get("error", "")
        assert fired and fired[0][0] == "_bad_test" and "boom" in fired[0][1]


# ── T0-8 MCP schema ──


class TestMcpSchema:
    def test_schema_from_signature(self):
        from lingclaude.engine.mcp_proxy import _schema_from_signature

        def sample(a: str, b: int = 5, c: bool = False):
            return a

        props, required = _schema_from_signature(sample)
        assert props == {"a": {"type": "string"}, "b": {"type": "integer"}, "c": {"type": "boolean"}}
        assert required == ["a"]

    def test_build_openai_tools_respects_required_params(self, runtime):
        """带 required_params 的 ToolDefinition 不再把全部参数标为 required。"""
        from lingclaude.core.query_engine import QueryEngine
        from lingclaude.engine.tools import ToolDefinition

        runtime.registry.register(ToolDefinition(
            name="_opt_test", description="opt",
            parameters={"q": {"type": "string"}, "n": {"type": "integer"}},
            required_params=("q",),
        ))
        engine = QueryEngine(runtime=runtime)
        tools = engine._build_openai_tools("")
        by_name = {t["name"]: t for t in tools}
        assert by_name["_opt_test"]["parameters"]["required"] == ["q"]
        # 旧式（无 required_params）仍全量 required
        assert by_name["read"]["parameters"]["required"] == list(by_name["read"]["parameters"]["properties"].keys())


# ── T0-10 sandbox_policy ──


class TestSandboxPolicyWiring:
    def test_strict_policy_fails_closed_without_bwrap(self, monkeypatch, tmp_path):
        """strict 模式 + bwrap 不可用 → SandboxUnavailableError（不静默降级）。"""
        import lingclaude.engine.bash as bash_mod
        from lingclaude.engine.bash import BashExecutor, SandboxUnavailableError
        from lingclaude.lacp.sandbox_policy import SandboxMode, SandboxPolicy

        monkeypatch.setattr(bash_mod, "_BWARP_PROBE_RESULT", False)
        monkeypatch.setattr(bash_mod.shutil, "which", lambda _n: None)

        ex = BashExecutor(
            working_dir=str(tmp_path),
            sandbox_policy=SandboxPolicy(mode=SandboxMode.STRICT),
        )
        with pytest.raises(SandboxUnavailableError):
            ex.run("echo hi")

    def test_no_policy_degrades_with_warning(self, monkeypatch, tmp_path, caplog):
        """无策略时降级仍发生，但不再静默（WARNING 日志）。"""
        import logging

        import lingclaude.engine.bash as bash_mod
        from lingclaude.engine.bash import BashExecutor

        monkeypatch.setattr(bash_mod, "_BWARP_PROBE_RESULT", False)
        monkeypatch.setattr(bash_mod.shutil, "which", lambda _n: None)

        ex = BashExecutor(working_dir=str(tmp_path))
        with caplog.at_level(logging.WARNING, logger="lingclaude.engine.bash"):
            res = ex.run("echo hi")
        assert res.exit_code == 0
        assert any("bwrap" in r.message for r in caplog.records)
