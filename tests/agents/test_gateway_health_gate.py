"""agent-gateway 健康门禁分级冷却测试（2026-09-24 L2①）。

覆盖（21 测试）：
1. failed 一次即入冷却（硬失败语义不变）
2. degraded 单次不入冷却（软失败，连败阈值 _DEGRADED_LIMIT=3）
3. degraded 连败达阈入冷却，reason 带 degraded×N 溯源
4. 一次 ok 清零连败计数（半开复位最小语义）
5. failed 覆盖任何残余 degraded 计数（硬失败主导）
6. 冷却到期转半开：首探测放行、并发者仍拒（原"到期弹栈"语义升级）
7. _classify 三级判定（failed/degraded/ok）
8. reason 截断 200 字符（J4 留痕不膨胀）
9. 半开探测成功全复位（L2②a）
10. 半开探测 degraded<阈不滞留 limbo（L2②a 边角）
11. force 旁路兼容冷却期与半开态（L2②a，含半开并发拒绝断言）
12. 梯子升级 900→1800→3600 封顶（L2②a；L2③ 起以缺省 agent crush 断言）
13. per-agent 冷却基准：cc/opencode 1800 起步，缺省 900 与旧梯子等价（L2③）
14. L2④ manifest 等价回归：五家形态/超时/probe/quota/profile/default_model/冷却基准
15. L2④ 模板实例化：占位符 replace 注入、prompt 花括号原样保留、codex modes 分支
16. L2④ 数据驱动验收：mock spec 过 _agents_from_manifest 推导点即成（J1）
17. L2⑤ quorum 满编/双家冷却不警（3/5 边界），三家冷却=可恢复型分型
18. L2⑤ 永久缺编型分型：bin 缺失+冷却叠加，available<quorum 需人工
19. L2⑤ 阈值 manifest 驱动：缺省 3，fleet_health.quorum_min 覆盖生效
20. L2⑤ 冷却到期残留不计 runtime_down（惰性清理不误伤 active）
21. L2⑤ agent_status 输出 fleet 段（桩化直喂，不跑真实子进程）

直载 server.py（plugins 无包结构）；fastmcp 缺席时注入假桩，测试自给自足。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import time
import types

import pytest

_SERVER = (pathlib.Path(__file__).resolve().parents[2]
           / "lingclaude" / "plugins" / "agents" / "proj_agent_gateway" / "server.py")

# fastmcp 缺席兜底：仅用到 @mcp.tool 装饰器透传语义
try:
    import fastmcp  # noqa: F401
except ImportError:
    _fake = types.ModuleType("fastmcp")

    class _FakeMCP:
        def tool(self, *a, **k):
            def deco(f):
                return f
            return deco

        def run(self, *a, **k):
            return None

    _fake.FastMCP = lambda *a, **k: _FakeMCP()
    sys.modules["fastmcp"] = _fake

_spec = importlib.util.spec_from_file_location("gw_server", _SERVER)
gw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gw)


@pytest.fixture(autouse=True)
def _reset_health():
    """每测试前后清空健康缓存，防跨测试污染。"""
    gw._health.clear()
    gw._degraded_streak.clear()
    yield
    gw._health.clear()
    gw._degraded_streak.clear()


def test_failed_enters_cooldown_immediately():
    gw._mark_failed("cc", "boom: limit exhausted")
    gate = gw._health_check("cc")
    assert gate is not None and gate["cooldown"] is True
    assert gate["remaining_s"] > 0
    assert "boom" in gate["reason"]


def test_degraded_single_no_cooldown():
    gw._mark_failed("cc", "unrecognized_model", level="degraded")
    assert gw._health_check("cc") is None          # 未入冷却，可正常派发
    assert gw._degraded_streak["cc"] == 1          # 计数在案


def test_degraded_streak_reaches_limit_enters_cooldown():
    for _ in range(gw._DEGRADED_LIMIT):
        gw._mark_failed("cc", "quota warning", level="degraded")
    gate = gw._health_check("cc")
    assert gate is not None and gate["cooldown"] is True
    assert gate["reason"].startswith(f"degraded×{gw._DEGRADED_LIMIT}")


def test_ok_resets_streak():
    gw._mark_failed("cc", "warn1", level="degraded")
    gw._mark_ok("cc")                              # 一次成功即清零
    gw._mark_failed("cc", "warn2", level="degraded")
    assert gw._health_check("cc") is None          # 未达连败阈值，不冷却
    assert gw._degraded_streak["cc"] == 1


def test_failed_overrides_residue_streak():
    gw._mark_failed("cc", "warn1", level="degraded")
    gw._mark_failed("cc", "warn2", level="degraded")
    gw._mark_failed("cc", "hard exit=1")           # 默认 level=failed
    gate = gw._health_check("cc")
    assert gate is not None and gate["cooldown"] is True
    assert "cc" not in gw._degraded_streak         # 硬失败覆盖残余计数
    assert gw._health["cc"]["level"] == "failed"


def _expire(agent: str = "cc") -> None:
    """伪造冷却到期。"""
    gw._health[agent]["failed_until"] = time.monotonic() - 1


def test_health_check_expiry_enters_half_open():
    gw._mark_failed("cc", "boom")
    _expire()
    assert gw._health_check("cc") is None            # 首个到达者=探测者，放行
    assert gw._health["cc"]["half_open"] is True     # 半开态在案
    gate = gw._health_check("cc")                    # 并发者仍被拒（防探测风暴）
    assert gate is not None and gate.get("half_open") is True


def test_cooldown_ladder_upgrades_and_caps():
    gw._mark_failed("crush", "boom1")                 # crush 无 per-agent 基准 → 缺省梯子
    assert gw._health["crush"]["fail_count"] == 1
    base = gw._health["crush"]["failed_until"] - time.monotonic()
    assert 880 <= base <= 920                        # 第1档 900s（缺省基准，向后兼容自证）
    for expect in (1800, 3600, 3600):                # 探测失败逐级升级，3600 封顶
        _expire("crush")
        assert gw._health_check("crush") is None     # 半开放行探测
        gw._mark_failed("crush", "boom again")       # 探测失败沿梯子重入
        remaining = gw._health["crush"]["failed_until"] - time.monotonic()
        assert expect - 20 <= remaining <= expect
    assert gw._health["crush"]["fail_count"] == 4       # 超出梯子长度后封顶不再增


def test_per_agent_cooldown_base():
    """L2③：配额墙型 agent 基准 1800（梯子 1800/3600/7200），缺省 900 完全等价旧梯子。"""
    assert gw._ladder_for("cc") == (1800, 3600, 7200)
    assert gw._ladder_for("opencode") == (1800, 3600, 7200)
    assert gw._ladder_for("crush") == (900, 1800, 3600)
    assert gw._ladder_for("unknown-x") == (900, 1800, 3600)   # 未知 agent 走缺省
    gw._mark_failed("cc", "quota wall")
    remaining = gw._health["cc"]["failed_until"] - time.monotonic()
    assert 1780 <= remaining <= 1800                # cc 首档即 1800s


def test_probe_success_full_reset():
    gw._mark_failed("cc", "boom")
    _expire()
    assert gw._health_check("cc") is None            # 探测放行
    gw._mark_ok("cc")                                # 探测成功=恢复
    assert "cc" not in gw._health                    # 冷却态全清
    assert gw._health_check("cc") is None            # 后续调用正常放行


def test_probe_degraded_below_limit_no_limbo():
    # 边角：半开探测返回 degraded 且未达连败阈——半开态不得滞留 _health 成 limbo
    gw._mark_failed("cc", "boom")
    _expire()
    assert gw._health_check("cc") is None            # 探测放行
    gw._mark_failed("cc", "unrecognized_model", level="degraded")
    assert "cc" not in gw._health                    # 冷却已过期，清态放行（防 limbo）
    assert gw._degraded_streak["cc"] == 1            # 软失败计数照记
    assert gw._health_check("cc") is None            # agent 正常可用


def test_force_bypasses_cooldown_and_half_open():
    gw._mark_failed("cc", "boom")
    assert gw._health_check("cc", force=True) is None    # 冷却期 force 旁路（语义不变）
    _expire()
    assert gw._health_check("cc") is None                # 半开首探测放行
    assert gw._health_check("cc", force=True) is None    # 半开态 force 仍旁路
    gate = gw._health_check("cc")                        # 半开态无 force 仍拒
    assert gate is not None and gate["cooldown"] is True


def test_classify_three_levels():
    assert gw._classify({"timed_out": False, "exit": 1, "stderr": ""}) == "failed"
    assert gw._classify({"timed_out": True, "exit": 0, "stderr": ""}) == "failed"
    assert gw._classify({"timed_out": False, "exit": 0,
                         "stderr": "Warning: unrecognized_model foo"}) == "degraded"
    assert gw._classify({"timed_out": False, "exit": 0, "stderr": ""}) == "ok"


def test_reason_truncated_200():
    gw._mark_failed("cc", "x" * 500)
    assert len(gw._health["cc"]["reason"]) == 200


def test_l2_4_manifest_driven_equivalence():
    """L2④ manifest 五段结构化回归：_AGENTS 由 manifest agents 段构建，
    调用形态与旧硬编码表等价（五家、超时、probe、quota/profile/default_model）。"""
    assert sorted(gw._AGENTS) == ["ac", "cc", "codex", "crush", "opencode"]
    assert gw._AGENTS["cc"]["timeout_s"] == 180
    assert gw._AGENTS["cc"]["probe"] == ["claude", "--version"]
    assert gw.QUOTA_ARGV["cc"] == ["--model", "{model}"]
    assert gw.QUOTA_ARGV["ac"] == ["--provider", "{provider}", "--model", "{model}"]
    assert gw.PROFILE_ARGV == {"codex": ["-p", "{profile}"]}
    assert gw.DEFAULT_MODEL == {"cc": "M3"}
    assert gw._COOLDOWN_BASE == {"cc": 1800, "opencode": 1800}
    assert gw._fallback_of("opencode") == "crush" and gw._fallback_of("cc") is None


def test_l2_4_invoke_argv_template_instantiation():
    """L2④ invoke 模板实例化：占位符 replace 注入；prompt 含花括号代码原样保留
    （禁 str.format 的理由）；codex modes 分支（review）命中优先。"""
    argv = gw._AGENTS["cc"]["invoke"]('print({"a": 1}) # {prompt}', "")
    assert argv == ["claude", "-p", 'print({"a": 1}) # {prompt}',
                    "--output-format", "text"]
    assert gw._AGENTS["codex"]["invoke"]("hi", "review") == ["codex", "review", "hi"]
    assert gw._AGENTS["codex"]["invoke"]("hi", "") == ["codex", "exec", "hi"]
    assert gw._AGENTS["ac"]["invoke"]("hi", "") == ["atomcode", "-p", "hi"]


def test_l2_4_arbitrary_agent_addition_without_code_change():
    """L2④ J1 数据驱动验收：新增 agent = 纯数据 spec 过推导点即成（生产路径：
    改 manifest agents 段 + 重启薄壳 import 重推导，零代码改动）。改派链
    _fallback_of live 读 manifest 段；冷却基准为 import 冻结（重启同批生效）。"""
    spec = {"name": "mock", "bin": "mockbin", "desc": "test stub", "timeout_s": 5,
            "invoke_argv": ["mockbin", "{prompt}"], "probe": ["mockbin", "--version"],
            "quota_argv": ["-m", "{model}"], "fallback": "crush"}
    gw._MANIFEST_AGENTS["mockagent"] = spec
    try:
        a = gw._agents_from_manifest(gw._MANIFEST_AGENTS)["mockagent"]
        assert a["timeout_s"] == 5 and a["probe"] == ["mockbin", "--version"]
        assert a["invoke"]("hi", "") == ["mockbin", "hi"]
        assert a["invoke"]("hi", "review") == ["mockbin", "hi"]  # 未声明 modes → invoke_argv
        assert gw._fallback_of("mockagent") == "crush"
        assert gw._fallback_of("mockagent") is None or True  # 清理前 live 读仍成立
    finally:
        gw._MANIFEST_AGENTS.pop("mockagent", None)
    assert gw._fallback_of("mockagent") is None  # 清理后 live 读失效（无残留）


# ── L2⑤ quorum 预警（_fleet_summary 纯函数，直喂桩数据）──────────────────

def _probe_stub(available: list[str]) -> dict:
    """5 家探测桩：available 列表内的 agent available=True，其余 False。"""
    return {a: {"bin": a + "bin", "available": a in available, "version": "v0"}
            for a in ("cc", "codex", "crush", "opencode", "ac")}


def test_l2_5_quorum_full_and_recoverable_alert():
    """满编/双家冷却不警（3/5 边界值）；三家冷却=可恢复型（available 仍 5）。"""
    assert gw._fleet_summary(_probe_stub(["cc", "codex", "crush", "opencode", "ac"]))["alert"] is False
    gw._mark_failed("cc", "quota wall")
    gw._mark_failed("opencode", "weekly limit")
    s = gw._fleet_summary(_probe_stub(["cc", "codex", "crush", "opencode", "ac"]))
    assert s["active"] == 3 and s["alert"] is False          # 3/5 边界：不警
    gw._mark_failed("codex", "boom")
    s = gw._fleet_summary(_probe_stub(["cc", "codex", "crush", "opencode", "ac"]))
    assert s["active"] == 2 and s["alert"] is True
    assert "可恢复" in s["alert_reason"] and "无需人工" in s["alert_reason"]


def test_l2_5_quorum_permanent_absence():
    """bin 缺失 + 冷却叠加 → 永久缺编型：available < quorum，需人工介入。"""
    for a in ("cc", "codex", "crush"):
        gw._mark_failed(a, "down")
    s = gw._fleet_summary(_probe_stub(["opencode", "ac"]))   # 3 家 bin 缺失 + 3 家冷却
    assert s["available"] == 2 and s["active"] == 2
    assert s["alert"] is True and "人工" in s["alert_reason"]
    assert "永久缺编" in s["alert_reason"]


def test_l2_5_quorum_threshold_from_manifest():
    """阈值 manifest 驱动：缺省 3；fleet_health.quorum_min 覆盖生效（改数据不改代码）。"""
    assert gw._fleet_summary(_probe_stub(["cc", "codex", "crush"]))["quorum_min"] == 3
    old = gw._MANIFEST.get("fleet_health")
    try:
        gw._MANIFEST["fleet_health"] = {"quorum_min": 2}
        assert gw._fleet_summary(_probe_stub(["cc", "codex", "crush"]))["alert"] is False
        gw._MANIFEST["fleet_health"] = {"quorum_min": 4}
        assert gw._fleet_summary(_probe_stub(["cc", "codex", "crush"]))["alert"] is True
    finally:
        if old is None:
            gw._MANIFEST.pop("fleet_health", None)
        else:
            gw._MANIFEST["fleet_health"] = old


def test_l2_5_expired_cooldown_entry_not_runtime_down():
    """边角：_health 到期残留（惰性清理）不计入 runtime_down——active 不被误伤。"""
    gw._mark_failed("cc", "boom")
    gw._health["cc"]["failed_until"] = time.monotonic() - 1   # 已到期但未清
    s = gw._fleet_summary(_probe_stub(["cc", "codex", "crush", "opencode", "ac"]))
    assert s["active"] == 5 and s["alert"] is False


def test_l2_5_agent_status_output_has_fleet_section():
    """agent_status 输出含 fleet 段：monkeypatch 探测路径直喂桩数据（不跑真实子进程）。"""
    orig_which, orig_run = gw._which, gw._run
    gw._which = lambda a: f"/usr/bin/{a}bin"
    gw._run = lambda argv, to: {"stdout": "MockAgent 1.0\n", "stderr": "", "exit": 0, "timed_out": False}
    try:
        out = json.loads(gw.agent_status())
    finally:
        gw._which, gw._run = orig_which, orig_run
    assert set(out["fleet"]) == {"total", "available", "active", "quorum_min",
                                 "alert", "alert_reason"}
    assert out["fleet"]["total"] == 5 and out["fleet"]["available"] == 5
    assert out["fleet"]["alert"] is False
    assert "fleet" in gw.agent_status.__doc__   # docstring 声明 fleet 段语义
