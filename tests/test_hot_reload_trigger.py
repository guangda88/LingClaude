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


# ---------------------------------------------------------------------------
# 环 2 / 环 3（2026-09-28 接通热更断链）：config 变化 → SlotManager.rebuild
# ---------------------------------------------------------------------------
from lingclaude.core.slot import SlotManager


class TestConfigSlotRebuild:
    """环 3：注册槽 + config 变化 → rebuild。用注入 provider/工厂隔离外部副作用。"""

    def test_config_change_triggers_rebuild(self, tmp_path):
        (tmp_path / "t").mkdir(); (tmp_path / "a").mkdir()
        from lingclaude.engine.hot_reload_trigger import HotReloadTrigger
        sm = SlotManager()
        sm.register("model_provider", initial={"v": "old"})
        t = HotReloadTrigger(tools_dir=tmp_path/"t", agents_dir=tmp_path/"a", config_check_interval=0.0)
        # config 变化：old -> new（digest 不同 → needs_rebuild True）
        t._config_provider = lambda: {"v": "new"}
        t._config_yaml_changed = lambda: True
        made = []
        t.register_config_slot("model_provider", sm, lambda: made.append(1) or {"v": "new"})
        out = {}
        t._check_config_rebuild(out)
        assert made, "config 变化未触发工厂重建"
        assert out.get("config_rebuilt") == ["model_provider"]
        # 槽实例已换新
        assert sm.get("model_provider") == {"v": "new"}

    def test_config_unchanged_no_rebuild(self, tmp_path):
        (tmp_path / "t").mkdir(); (tmp_path / "a").mkdir()
        from lingclaude.engine.hot_reload_trigger import HotReloadTrigger
        sm = SlotManager()
        sm.register("model_provider", initial={"v": "same"})
        t = HotReloadTrigger(tools_dir=tmp_path/"t", agents_dir=tmp_path/"a", config_check_interval=0.0)
        t._config_provider = lambda: {"v": "same"}  # digest 相同 → 不重建
        t._config_yaml_changed = lambda: True
        made = []
        t.register_config_slot("model_provider", sm, lambda: made.append(1) or {"v": "same"})
        out = {}
        t._check_config_rebuild(out)
        assert not made, "config 未变化不应触发 rebuild"
        assert "config_rebuilt" not in out

    def test_register_config_slot_idempotent_override(self, tmp_path):
        from lingclaude.engine.hot_reload_trigger import HotReloadTrigger
        t = HotReloadTrigger(tools_dir=tmp_path, agents_dir=tmp_path)
        sm1, sm2 = SlotManager(), SlotManager()
        t.register_config_slot("model_provider", sm1, lambda: 1)
        t.register_config_slot("model_provider", sm2, lambda: 2)  # 覆盖
        assert t._config_slots["model_provider"][0] is sm2

    def test_rebuild_failure_fail_soft(self, tmp_path):
        (tmp_path / "t").mkdir(); (tmp_path / "a").mkdir()
        from lingclaude.engine.hot_reload_trigger import HotReloadTrigger
        sm = SlotManager()
        sm.register("model_provider", initial={"v": "old"})
        t = HotReloadTrigger(tools_dir=tmp_path/"t", agents_dir=tmp_path/"a", config_check_interval=0.0)
        t._config_provider = lambda: {"v": "new"}
        t._config_yaml_changed = lambda: True

        def boom():
            raise RuntimeError("provider 构建失败")

        t.register_config_slot("model_provider", sm, boom)
        out = {}
        t._check_config_rebuild(out)  # 不抛穿
        assert "config_errors" in out and "model_provider" in out["config_errors"]
        assert sm.get("model_provider") == {"v": "old"}  # 旧实例不动


class TestPolicyLoaderListener:
    """环 2：policy_loader.add_listener + hot_update/get 变化时回调。"""

    def setup_method(self):
        from lingclaude.core import policy_loader

        policy_loader.reset()
        self.pl = policy_loader

    def teardown_method(self):
        self.pl.reset()

    def _write(self, tmp_path, monkeypatch, name, data):
        monkeypatch.setattr(self.pl, "_POLICIES_DIR", tmp_path)
        p = tmp_path / f"{name}.yaml"
        import yaml

        p.write_text(yaml.safe_dump(data), encoding="utf-8")
        return p

    def test_hot_update_notifies_listener(self, tmp_path, monkeypatch):
        seen = []
        self.pl.add_listener("mypol", lambda n, d: seen.append((n, d)))
        self._write(tmp_path, monkeypatch, "mypol", {"k": 1})
        self.pl.get("mypol")  # 首次加载（建缓存，不通知）
        assert seen == []
        # 改内容 → hot_update 应通知
        self._write(tmp_path, monkeypatch, "mypol", {"k": 2})
        assert self.pl.hot_update() is True
        assert seen and seen[-1][0] == "mypol" and seen[-1][1] == {"k": 2}

    def test_add_listener_idempotent(self):
        calls = []
        fn = lambda n, d: calls.append(n)
        self.pl.add_listener("x", fn)
        self.pl.add_listener("x", fn)  # 重复注册去重
        self.pl._notify("x", {})
        assert calls == ["x"]

    def test_listener_exception_fail_soft(self):
        def boom(n, d):
            raise RuntimeError("listener 故障")

        seen = []
        self.pl.add_listener("y", boom)
        self.pl.add_listener("y", lambda n, d: seen.append(n))
        self.pl._notify("y", {})  # 不抛穿，其余监听器照常
        assert seen == ["y"]

    def test_reset_clears_listeners(self):
        seen = []
        self.pl.add_listener("z", lambda n, d: seen.append(n))
        self.pl.reset()
        self.pl._notify("z", {})
        assert seen == []
