"""P1① Hook 生命周期注册表 + 三注入点 + 哨兵迁移等价性测试。

锚点：
- 哨兵迁移等价：credential_leak→block / net_probe→ask 行为与迁移前一致
- PRE_TOOL block 短路、priority 排序、fail-open
- 用户插件装载（LC_HOOKS_DIR tmpdir，幂等）
- AFTER_TOOL 成功/失败双路径通知、fail-open
- POST_RESPONSE payload 传递
"""
from __future__ import annotations

import logging

import pytest

from lingclaude.core import hook_registry as hr
from lingclaude.core.hook_registry import HookPoint, HookResult, register_hook, run_hooks, unregister_hook


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    """每个用例独占注册表与装载状态（模块级单例，必须隔离）。"""
    monkeypatch.setattr(hr, "_REGISTRY", {p: [] for p in HookPoint})
    monkeypatch.setattr(hr, "_LOADED_HOOK_FILES", {})
    monkeypatch.setattr(hr, "_LOAD_DONE", True)  # 默认关掉惰性装载（用户插件用例单独开）


class TestRegistryCore:
    def test_register_and_run_annotate(self):
        register_hook(HookPoint.AFTER_TOOL, "obs", lambda **kw: HookResult(hook_name="obs", reason="ok"))
        rs = run_hooks(HookPoint.AFTER_TOOL, tool_name="read", args={}, summary={})
        assert len(rs) == 1 and rs[0].reason == "ok"

    def test_block_short_circuits_pre_tool(self):
        ran = []
        register_hook(HookPoint.PRE_TOOL, "first",
                      lambda **kw: HookResult(hook_name="first", action="block", reason="no"), priority=10)
        register_hook(HookPoint.PRE_TOOL, "second", lambda **kw: ran.append(1), priority=20)
        rs = run_hooks(HookPoint.PRE_TOOL, tool_name="bash", tool_args={})
        assert ran == []  # 后续没跑
        assert rs == [HookResult(hook_name="first", action="block", reason="no")] or rs[0].action == "block"

    def test_priority_ordering(self):
        order = []
        register_hook(HookPoint.PRE_TOOL, "late", lambda **kw: order.append("late") or None, priority=90)
        register_hook(HookPoint.PRE_TOOL, "early", lambda **kw: order.append("early") or None, priority=5)
        run_hooks(HookPoint.PRE_TOOL, tool_name="x", tool_args={})
        assert order == ["early", "late"]

    def test_fail_open_on_exception(self):
        register_hook(HookPoint.PRE_TOOL, "boom",
                      lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")), priority=10)
        register_hook(HookPoint.PRE_TOOL, "fine",
                      lambda **kw: HookResult(hook_name="fine", reason="ok"), priority=20)
        rs = run_hooks(HookPoint.PRE_TOOL, tool_name="x", tool_args={})
        assert [r.hook_name for r in rs] == ["fine"]  # 崩的跳过，好的继续

    def test_non_hook_result_ignored(self, caplog):
        register_hook(HookPoint.AFTER_TOOL, "weird", lambda **kw: "not-a-result")
        with caplog.at_level(logging.WARNING):
            rs = run_hooks(HookPoint.AFTER_TOOL, tool_name="x", args={}, summary={})
        assert rs == [] and any("non-HookResult" in r.message for r in caplog.records)

    def test_unregister(self):
        register_hook(HookPoint.AFTER_TOOL, "obs", lambda **kw: None)
        assert unregister_hook(HookPoint.AFTER_TOOL, "obs") is True
        assert unregister_hook(HookPoint.AFTER_TOOL, "obs") is False

    def test_empty_point_returns_empty(self):
        assert run_hooks(HookPoint.POST_RESPONSE, response_text="", meta={}) == []


class TestUserHookPlugins:
    def _write_hook(self, d, name):
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(
            "from lingclaude.core.hook_registry import HookPoint, HookResult\n"
            "def register(api):\n"
            "    api.on(HookPoint.AFTER_TOOL, 'user-obs', _fn)\n"
            "def _fn(**kw):\n"
            "    return HookResult(hook_name='user-obs', reason='seen')\n",
            encoding="utf-8",
        )

    def test_load_and_fire(self, tmp_path, monkeypatch):
        fired = []
        self._write_hook(tmp_path, "my_hook.py")
        monkeypatch.setattr(hr, "_LOAD_DONE", False)
        monkeypatch.setenv("LC_HOOKS_DIR", str(tmp_path))
        status = hr.load_user_hooks(tmp_path)
        assert status == {"my_hook.py": "loaded"}
        # 惰性装载已被 _LOAD_DONE 关闭场景之外：run_hooks 只在未装载时扫目录，
        # 这里直接验证注册表已生效
        rs = run_hooks(HookPoint.AFTER_TOOL, tool_name="read", args={}, summary={})
        assert len(rs) == 1 and rs[0].reason == "seen"

    def test_idempotent_load(self, tmp_path):
        self._write_hook(tmp_path, "my_hook.py")
        first = hr.load_user_hooks(tmp_path)
        second = hr.load_user_hooks(tmp_path)
        assert first == second == {"my_hook.py": "loaded"}

    def test_bad_plugin_skipped(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "broken.py").write_text("raise RuntimeError('bad')\n", encoding="utf-8")
        (tmp_path / "no_register.py").write_text("x = 1\n", encoding="utf-8")
        status = hr.load_user_hooks(tmp_path)
        assert status["broken.py"].startswith("error:")
        assert status["no_register.py"] == "no-register"

    def test_underscore_files_skipped(self, tmp_path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "_private.py").write_text("raise RuntimeError('should not load')\n", encoding="utf-8")
        assert hr.load_user_hooks(tmp_path) == {}

    def test_no_dir_is_noop(self, tmp_path):
        assert hr.load_user_hooks(tmp_path / "missing") == {}


class TestSentinelMigrationEquivalence:
    """迁移等价性：注册表路径与旧直调行为逐项一致。"""

    def test_credential_leak_blocks_via_registry(self, monkeypatch):
        import lingclaude.core.tool_auth_hook as tah
        PAT = "sk" + "-[A-Za-z0-9]{20,}"
        monkeypatch.setattr(tah, "_get_policy", lambda: {
            "credential_leak_guard": {"enabled": True, "block_patterns": [PAT]},
            "tiers": {"auto": {"tools": ["bash"], "description": ""}},
            "audit_enabled": False,
        })
        d = tah.check_tool_call("bash", {"command": "echo sk-abcdefghijklmnopqrstuvwxyz"})
        assert d.tier is tah.Tier.BLOCK
        assert d.policy_id.startswith("hook:credential_leak_guard")
        assert d.audit_written is True  # 哨兵内部已写台账，detail 透传

    def test_net_probe_asks_via_registry(self, monkeypatch):
        import lingclaude.core.tool_auth_hook as tah
        monkeypatch.setattr(tah, "_get_policy", lambda: {"tiers": {}, "audit_enabled": False})
        d = tah.check_tool_call("bash", {"command": "curl http://x"})
        assert d.tier is tah.Tier.ASK
        assert d.policy_id == "hook:net_misdiag_guard"
        assert "net_channels" in d.reason

    def test_normal_tool_hits_tier_matrix(self, monkeypatch):
        import lingclaude.core.tool_auth_hook as tah
        monkeypatch.setattr(tah, "_get_policy", lambda: {
            "tiers": {"auto": {"tools": ["read"], "description": "read-only"}},
            "audit_enabled": False,
        })
        d = tah.check_tool_call("read", {"path": "/tmp/x"})
        assert d.tier is tah.Tier.AUTO and d.policy_id != "hook:"

    def test_registry_exception_falls_back_to_direct(self, monkeypatch):
        """注册表 run_hooks 崩溃 → 保底直调，行为等价旧链。"""
        import lingclaude.core.tool_auth_hook as tah

        def _boom(point, **kw):
            raise RuntimeError("registry down")

        monkeypatch.setattr(tah, "_get_policy", lambda: {
            "credential_leak_guard": {"enabled": True, "block_patterns": ["sk" + "-[A-Za-z0-9]{20,}"]},
            "tiers": {}, "audit_enabled": False,
        })

        def _boom(point, **kw):
            raise RuntimeError("registry down")

        monkeypatch.setattr("lingclaude.core.hook_registry.run_hooks", _boom)
        d = tah.check_tool_call("bash", {"command": "echo " + "sk-abcdefghijklmnopqrstuvwxyz"})
        assert d.tier is tah.Tier.BLOCK  # 直调路径照样拦


class TestPipelineAfterTool:
    """AFTER_TOOL 双路径通知（复用 M1-b 的 pipeline 语义）。"""

    def _mk_pipeline(self, after_cb=None, handler=None):
        """真实构造器 + 完整 fake registry（get 带 .name + finalize_result）。

        教训注：曾试图用子类绕开 __init__ 只接两个部件——execute 路径属性
        面远比想象宽（_critical/_timeout/...），逐个补属是打地鼠。真实
        构造器天然装配全部属性，fake 只需满足 registry 接口。"""
        from types import SimpleNamespace
        from lingclaude.engine.tool_pipeline import ToolPipeline

        if handler is None:
            handler = lambda **kw: {"ok": True}  # noqa: E731

        class _Reg:
            def get(self, name):
                return SimpleNamespace(is_ok=True, data=SimpleNamespace(name=name, handler=handler))

            def finalize_result(self, name, args, raw):
                return None

        return ToolPipeline(_Reg(), write_scoped_tools={"write"}, after_tool_callback=after_cb)

    def test_called_on_success(self):
        seen = []
        p = self._mk_pipeline(lambda n, a, s: seen.append((n, s)))
        p.execute("read", {"path": "/tmp/x.txt"})
        assert len(seen) == 1
        n, s = seen[0]
        assert n == "read" and s["success"] is True

    def test_called_on_error_path(self):
        seen = []
        p = self._mk_pipeline(lambda n, a, s: seen.append((n, s)))

        def _fail(**kw):
            raise RuntimeError("tool blew up")

        p2 = self._mk_pipeline(lambda n, a, s: seen.append((n, s)), handler=_fail)
        p2.execute("read", {"path": "/tmp/x.txt"})
        assert len(seen) == 1 and seen[0][1]["success"] is False

    def test_callback_exception_fail_open(self):
        def _boom(n, a, s):
            raise RuntimeError("hook exploded")

        p = self._mk_pipeline(_boom)
        res = p.execute("read", {"path": "/tmp/x.txt"})
        assert res.get("result") == {"ok": True} or res.get("ok") is True  # 不反噬

    def test_registry_fallback_when_no_callback(self, monkeypatch):
        called = {}
        monkeypatch.setattr(
            "lingclaude.core.hook_registry.run_hooks",
            lambda point, **kw: called.setdefault("point", point),
        )
        p = self._mk_pipeline(None)  # 无显式 callback → 走注册表
        p.execute("read", {"path": "/tmp/x.txt"})
        assert called.get("point") is HookPoint.AFTER_TOOL


class TestPostResponsePayload:
    def test_payload_shape(self):
        got = {}
        register_hook(HookPoint.POST_RESPONSE, "learner",
                      lambda response_text, meta: got.update(text=response_text, meta=meta) or None)
        run_hooks(HookPoint.POST_RESPONSE, response_text="hello", meta={"tool_calls": 2})
        assert got == {"text": "hello", "meta": {"tool_calls": 2}}
