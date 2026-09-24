"""agent-gateway 健康门禁分级冷却测试（2026-09-24 L2①）。

覆盖（13 测试）：
1. failed 一次即入冷却（硬失败语义不变）
2. degraded 单次不入冷却（软失败，连败阈值 _DEGRADED_LIMIT=3）
3. degraded 连败达阈入冷却，reason 带 degraded×N 溯源
4. 一次 ok 清零连败计数（半开复位最小语义）
5. failed 覆盖任何残余 degraded 计数（硬失败主导）
6. 冷却到期转半开：首探测放行、并发者仍拒（原"到期弹栈"语义升级）
7. _classify 三级判定（failed/degraded/ok）
8. reason 截断 200 字符（J4 留痕不膨胀）
9. 梯子升级 900→1800→3600 封顶（L2②a）
10. 半开探测成功全复位（L2②a）
11. 半开探测 degraded<阈不滞留 limbo（L2②a 边角）
12. force 旁路兼容冷却期与半开态（L2②a，含半开并发拒绝断言）

直载 server.py（plugins 无包结构）；fastmcp 缺席时注入假桩，测试自给自足。
"""
from __future__ import annotations

import importlib.util
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
    gw._mark_failed("cc", "boom1")
    assert gw._health["cc"]["fail_count"] == 1
    base = gw._health["cc"]["failed_until"] - time.monotonic()
    assert 880 <= base <= 920                        # 第1档 900s
    for expect in (1800, 3600, 3600):                # 探测失败逐级升级，3600 封顶
        _expire()
        assert gw._health_check("cc") is None        # 半开放行探测
        gw._mark_failed("cc", "boom again")          # 探测失败沿梯子重入
        remaining = gw._health["cc"]["failed_until"] - time.monotonic()
        assert expect - 20 <= remaining <= expect
    assert gw._health["cc"]["fail_count"] == 4       # 超出梯子长度后封顶不再增


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
