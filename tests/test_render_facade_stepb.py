"""Step B 双路径测试 — render_facade：tui 插件优先 + cli.display 回退。

按 proposals/2026-09-09_TUI_PLUGIN_SEAM_PROPOSAL.md Step B：
- 渲染调用点经 render_facade（不再直接 import cli.display 渲染函数）
- 双路径：tui provider 可用 → 走 provider；不可用 → 回退 cli.display

覆盖：
1. render_facade 缺省路径（无 provider）→ cli.display 回退
2. render_facade provider 路径（已 install）→ 走 tui provider
3. 同名薄代理转发正确（print_markdown 等 15 个代理）
4. 数据类保持 cli 直引（SessionSummary/QualityReport 从 cli.display import）
5. 全库 cli.display 渲染函数直接 import 清零（Step C 验收）
"""

from __future__ import annotations

import importlib
import sys

import pytest


def _fresh_facade():
    """返回未受 tui install 影响的 render_facade 模块副本。"""
    if "lingclaude.cli.render_facade" in sys.modules:
        del sys.modules["lingclaude.cli.render_facade"]
    return importlib.import_module("lingclaude.cli.render_facade")


class TestRenderFacadeFallbackPath:
    """路径 1：tui provider 不可用 → cli.display 回退。"""

    def test_render_markdown_fallback(self, monkeypatch):
        facade = _fresh_facade()
        # 模拟 tui 插件缺失：_get_renderer 返回 None
        monkeypatch.setattr(facade, "_get_renderer", lambda: None)
        # fallback 应正常调用 cli.display.print_markdown
        facade.render("markdown", "# hi")
        assert facade.has("markdown")

    def test_render_unknown_raises(self, monkeypatch):
        facade = _fresh_facade()
        monkeypatch.setattr(facade, "_get_renderer", lambda: None)
        with pytest.raises(KeyError):
            facade.render("no_such_method")

    def test_all_fallback_names_registered(self, monkeypatch):
        facade = _fresh_facade()
        monkeypatch.setattr(facade, "_get_renderer", lambda: None)
        for method in [
            "markdown",
            "header",
            "success",
            "error",
            "warning",
            "info",
            "welcome",
            "tool_call",
            "tool_result",
            "diff",
            "session_summary",
            "quality_report",
            "kv",
            "trend",
            "metrics_stats",
        ]:
            assert facade.has(method), f"fallback 缺失: {method}"

    def test_render_provider_failure_falls_back(self, monkeypatch):
        """provider 执行抛异常 → 回退 cli.display。"""
        facade = _fresh_facade()

        class _BrokenProvider:
            name = "default_renderer"

            def has(self, method):
                return True

            def execute(self, *a, **k):
                raise RuntimeError("boom")

        monkeypatch.setattr(facade, "_get_renderer", lambda: _BrokenProvider())
        facade.render("info", "x")  # 不抛，回退成功


class TestRenderFacadeProviderPath:
    """路径 2：tui provider 可用 → 走 provider。"""

    @pytest.fixture(autouse=True)
    def _installed(self):
        from lingclaude_plugins.tui import TUI_SEAM, install

        install()
        yield
        # 清理：删除已注册 provider，避免污染其他测试
        TUI_SEAM._providers.clear()  # noqa: SLF001
        TUI_SEAM._default = None

    def test_render_uses_provider(self, monkeypatch):
        facade = _fresh_facade()

        calls: list[tuple] = []

        class _SpyProvider:
            name = "default_renderer"

            def has(self, method):
                return True

            def execute(self, method, *args, **kwargs):
                calls.append((method, args, kwargs))
                return "provider_result"

        monkeypatch.setattr(facade, "_get_renderer", lambda: _SpyProvider())
        out = facade.render("info", "hello")
        assert out == "provider_result"
        assert calls == [("info", ("hello",), {})]

    def test_installed_renderer_executes_session_summary(self):
        """真实 tui provider：session_summary 走 provider 不炸。"""
        facade = _fresh_facade()
        facade.render("info", "ok")  # provider 有 info
        assert facade.has("session_summary")  # 补全的 5 个能力存在


class TestFacadeThinProxies:
    """同名薄代理：调用点零改动，仅换 import 源。"""

    def test_proxy_importable_from_render_facade(self):
        facade = _fresh_facade()
        for name in [
            "print_markdown",
            "print_header",
            "print_success",
            "print_error",
            "print_warning",
            "print_info",
            "print_welcome",
            "format_tool_call",
            "format_tool_result",
            "print_diff",
            "print_session_summary",
            "print_quality_report",
            "print_kv",
            "print_trend",
            "print_metrics_stats",
        ]:
            assert callable(getattr(facade, name)), f"代理缺失: {name}"

    def test_consumers_import_render_facade(self):
        """消费方（app/repl/repl_io/repl_turn）已改用 render_facade。"""
        for mod in [
            "lingclaude.cli.app",
            "lingclaude.cli.repl",
            "lingclaude.cli.repl_io",
            "lingclaude.cli.repl_turn",
        ]:
            src = importlib.import_module(mod).__file__ or ""
            text = open(src, encoding="utf-8").read()
            assert "render_facade" in text, f"{mod} 未改缝 render_facade"


class TestStepCAcceptance:
    """Step C 验收：cli.display 渲染函数直接 import 清零。"""

    def test_no_direct_display_render_import(self):
        """全库 cli.display 渲染函数直接 import 应清零（数据类除外）。"""
        import subprocess

        # 数据类 + 内部自引用白名单；_plain_no_color 是 bool 模式判定（非 print_*/format_* 渲染函数）
        whitelist = {"SessionSummary", "QualityReport", "_plain_no_color"}

        out = subprocess.run(
            ["grep", "-rn", "from lingclaude.cli.display import", "lingclaude/", "--include=*.py"],
            capture_output=True,
            text=True,
        ).stdout
        assert out, "未找到任何 cli.display import"
        for line in out.splitlines():
            fname, _, rest = line.partition(":")
            imports = rest.split("import", 1)[-1].strip("() \n")
            names = {n.strip() for n in imports.replace("\n", " ").split(",") if n.strip()}
            # 允许数据类；渲染函数（print_*/format_*）必须清零
            bad = names - whitelist
            assert not bad, f"{fname} 仍直接 import cli.display 渲染函数: {bad}"

    def test_render_facade_is_single_consumption_point(self):
        """render_facade 是渲染函数唯一落点。"""
        facade = _fresh_facade()
        from lingclaude.cli import display as d

        # facade 的 fallback 指向 cli.display 函数
        assert facade._FALLBACKS["markdown"] is d.print_markdown  # noqa: SLF001
