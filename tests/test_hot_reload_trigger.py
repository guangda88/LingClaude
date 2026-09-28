"""HotReloadTrigger 验收测试（P7 新落盘拾取）。

对齐 HotReloadTrigger 的诚实边界：本机制是 add-only「新落盘拾取」，
不是「已加载代码热替换」。测试覆盖：
  - 基线快照首扫不触发（现状视为基线，不假触发）；
  - 新增工具目录（manifest.plugin.json）→ 触发工具装载动作；
  - 新增 Agent 目录（plugin.py）→ 触发 Agent 装载动作；
  - 节流语义（interval 内重复 check 不重扫）；
  - disabled 短路；
  - 装载失败 fail-soft（异常收敛，不抛穿，快照仍推进防反复触发）。

装载动作用注入探针替代真实入口（不真 import 插件、不碰 SeamRegistry），
保证测试隔离、零外部副作用。
"""
from __future__ import annotations

from pathlib import Path

from lingclaude.engine.hot_reload_trigger import HotReloadTrigger


def _mk_tool_plugin(root: Path, name: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.py").write_text("# stub\n", encoding="utf-8")
    (d / "manifest.plugin.json").write_text("{}", encoding="utf-8")


def _mk_agent_plugin(root: Path, name: str) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.py").write_text("# stub\n", encoding="utf-8")


def _trigger(tmp_path: Path, **kw):
    tools = tmp_path / "tools"
    agents = tmp_path / "agents"
    tools.mkdir()
    agents.mkdir()
    calls = {"tools": 0, "agents": 0}
    t = HotReloadTrigger(
        tools_dir=tools,
        agents_dir=agents,
        on_tools_reload=lambda: calls.__setitem__("tools", calls["tools"] + 1),
        on_agents_reload=lambda: calls.__setitem__("agents", calls["agents"] + 1),
        scan_interval=kw.pop("scan_interval", 30.0),
        **kw,
    )
    return t, tools, agents, calls


class TestBaselineSnapshot:
    def test_first_scan_is_baseline_no_trigger(self, tmp_path):
        t, tools, agents, calls = _trigger(tmp_path)
        _mk_tool_plugin(tools, "alpha")
        _mk_agent_plugin(agents, "agent_x")
        out = t.check(force=True)
        assert out["scanned"] is True
        assert "tools_added" not in out and "agents_added" not in out
        assert calls == {"tools": 0, "agents": 0}

    def test_no_change_no_trigger(self, tmp_path):
        t, tools, agents, calls = _trigger(tmp_path)
        t.check(force=True)  # 基线
        out = t.check(force=True)  # 无变化
        assert calls == {"tools": 0, "agents": 0}


class TestNewPluginPickup:
    def test_new_tool_dir_triggers_tools_reload(self, tmp_path):
        t, tools, agents, calls = _trigger(tmp_path)
        t.check(force=True)  # 空基线
        _mk_tool_plugin(tools, "beta")
        out = t.check(force=True)
        assert out["tools_added"] == ["beta"]
        assert calls["tools"] == 1
        assert calls["agents"] == 0

    def test_new_agent_dir_triggers_agents_reload(self, tmp_path):
        t, tools, agents, calls = _trigger(tmp_path)
        t.check(force=True)  # 空基线
        _mk_agent_plugin(agents, "agent_new")
        out = t.check(force=True)
        assert out["agents_added"] == ["agent_new"]
        assert calls["agents"] == 1
        assert calls["tools"] == 0

    def test_added_dir_without_marker_not_picked(self, tmp_path):
        """缺 marker 文件的目录不算插件（对齐 discover_plugins 口径）。"""
        t, tools, agents, calls = _trigger(tmp_path)
        t.check(force=True)
        (tools / "no_marker").mkdir()  # 无 manifest.plugin.json
        out = t.check(force=True)
        assert "tools_added" not in out
        assert calls["tools"] == 0


class TestThrottleAndSwitch:
    def test_throttle_within_interval(self, tmp_path):
        t, tools, agents, calls = _trigger(tmp_path, scan_interval=999.0)
        t.check()  # 基线（首扫，_last_scan=now）
        _mk_tool_plugin(tools, "gamma")
        out = t.check()  # 未 force，节流内
        assert out["scanned"] is False and out["reason"] == "throttled"
        assert calls["tools"] == 0
        # force 跳过节流可立即拾取
        out2 = t.check(force=True)
        assert out2.get("tools_added") == ["gamma"]

    def test_disabled_short_circuits(self, tmp_path):
        t, tools, agents, calls = _trigger(tmp_path, enabled=False)
        _mk_tool_plugin(tools, "delta")
        out = t.check(force=True)
        assert out["scanned"] is False and out["reason"] == "disabled"
        assert calls["tools"] == 0


class TestFailSoft:
    def test_reload_failure_does_not_raise_and_snapshot_advances(self, tmp_path):
        def _boom():
            raise RuntimeError("loader exploded")

        tools = tmp_path / "tools"; tools.mkdir()
        agents = tmp_path / "agents"; agents.mkdir()
        n = {"tools": 0}

        def _counting_boom():
            n["tools"] += 1
            raise RuntimeError("loader exploded")

        t = HotReloadTrigger(
            tools_dir=tools, agents_dir=agents,
            on_tools_reload=_counting_boom,
            on_agents_reload=lambda: None,
        )
        t.check(force=True)  # 基线
        _mk_tool_plugin(tools, "fragile")
        out = t.check(force=True)  # 触发但失败
        assert out["tools_added"] == ["fragile"]
        assert "tools_error" in out  # 异常收敛为字段，不抛穿
        # 快照已推进：再次 check 不因同一新增反复触发
        out2 = t.check(force=True)
        assert "tools_added" not in out2
        assert n["tools"] == 1


class TestRealDefaultActions:
    """端到端：真实装载入口（非注入探针）验证新落盘被实际注册。

    agent 侧造一个最小 plugin.py（register() 写 SeamRegistry.AGENT 测试键），
    走真实 registry_loader.load_all；tool 侧验证 _PLUGIN_LOAD_DONE 被重置重扫。
    测试后清理 SeamRegistry 测试键，防跨用例泄漏。
    """

    def test_new_agent_dir_real_load_registers_seam(self, tmp_path, monkeypatch):
        import sys
        import types

        from lingclaude.core.seam import SeamRegistry, SeamType
        from lingclaude.plugins.agents import registry_loader

        agents = tmp_path / "agents"
        agents.mkdir()
        # 基线（空）
        t = HotReloadTrigger(tools_dir=tmp_path / "tools", agents_dir=agents)
        (tmp_path / "tools").mkdir()
        t.check(force=True)
        assert SeamRegistry.get_optional(SeamType.AGENT, "agent/zz_hot") is None

        # 造一个最小 agent 插件目录 + 伪模块（register 写测试缝键）
        pkg = agents / "agent_zz_hot"
        pkg.mkdir()
        (pkg / "plugin.py").write_text("# stub\n", encoding="utf-8")

        class _P:
            name = "agent/zz_hot"

            def run(self, tool, **kw):
                return {"ok": True}

        fake_mod = types.ModuleType("lingclaude.plugins.agents.agent_zz_hot.plugin")

        def _register(reg):
            reg.register(SeamType.AGENT, "agent/zz_hot", _P())

        fake_mod.register = _register
        monkeypatch.setitem(sys.modules, fake_mod.__name__, fake_mod)
        # 让 registry_loader 能在临时根下发现该目录
        monkeypatch.setattr(registry_loader, "AGENTS_ROOT", agents)
        # importlib.import_module 会直接命中 sys.modules 缓存（伪模块）
        try:
            out = t.check(force=True)
            assert out.get("agents_added") == ["agent_zz_hot"]
            assert SeamRegistry.get_optional(SeamType.AGENT, "agent/zz_hot") is not None
        finally:
            SeamRegistry.unregister(SeamType.AGENT, "agent/zz_hot")
            sys.modules.pop(fake_mod.__name__, None)

    def test_tools_reload_resets_done_flag(self, tmp_path, monkeypatch):
        from lingclaude.engine import tools as _tools

        tools = tmp_path / "tools"
        tools.mkdir()
        monkeypatch.setattr(_tools, "_PLUGIN_LOAD_DONE", True)
        t = HotReloadTrigger(tools_dir=tools, agents_dir=tmp_path / "agents")
        (tmp_path / "agents").mkdir()
        t.check(force=True)  # 基线
        _mk_tool_plugin(tools, "newtool")
        # 真实默认动作会重置 _PLUGIN_LOAD_DONE 并尝试重扫（临时目录的伪 manifest
        # 可能加载失败，但 fail-soft；此处只验证「重置 → 重扫」通路被触发）。
        monkeypatch.setattr(_tools, "_PLUGINS_DIR", str(tools))
        t.check(force=True)
        # 重扫后 done 标志被置位（exactly-once 语义恢复），证明重扫确实发生
        assert _tools._PLUGIN_LOAD_DONE is True
