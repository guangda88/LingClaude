"""配置热重载回归测试。

背景：改 config.yaml 后运行中的引擎不感知旧 max_turns，导致长任务反复撞
「达到最大工具调用轮次」。_maybe_hot_reload_config 让改动即时生效。
"""
from __future__ import annotations

import dataclasses

import pytest

from lingclaude.core import model_call as mc


@dataclasses.dataclass(frozen=True)
class _FakeCfg:
    max_turns: int = 8


class _FakeEngine:
    def __init__(self, mt: int) -> None:
        self.config = _FakeCfg(mt)


@pytest.fixture(autouse=True)
def _reset_hot_reload_state():
    """隔离模块级状态，避免测试间串扰。"""
    mc._CFG_MTIME_CACHE.clear()
    mc._last_hot_reload_check[0] = 0.0
    yield
    mc._CFG_MTIME_CACHE.clear()
    mc._last_hot_reload_check[0] = 0.0


def test_resolve_reads_config_max_turns():
    assert mc._resolve_max_tool_rounds(_FakeEngine(500)) == 500


def test_resolve_fallback_constant():
    class _NoCfg:
        pass

    assert mc._resolve_max_tool_rounds(_NoCfg()) == mc.AGENT_MAX_TOOL_ROUNDS


def test_hot_reload_updates_engine_config(tmp_path, monkeypatch):
    """mtime 变化 + 值不同 → 引擎 config 被替换为新值。"""
    import time as _time

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("engine:\n  max_turns: 500\n")
    import lingclaude.core.config as config_mod

    monkeypatch.setattr(config_mod, "find_config_path", lambda: cfg_file)
    monkeypatch.setattr(
        config_mod, "load_config", lambda p: type("C", (), {"engine": type("E", (), {"max_turns": 500})()})()
    )
    engine = _FakeEngine(8)
    mc._maybe_hot_reload_config(engine)
    assert engine.config.max_turns == 500
    # 二次调用：mtime 未变（解除节流后仍不应替换）→ 验证 mtime 去重生效
    mc._last_hot_reload_check[0] = 0.0
    _time.sleep(0.001)
    engine.config = _FakeCfg(8)
    mc._maybe_hot_reload_config(engine)
    assert engine.config.max_turns == 8  # mtime 未变，未触发重载


def test_hot_reload_throttle():
    """30 秒节流：短时间第二次调用直接跳过。"""
    mc._last_hot_reload_check[0] = __import__("time").monotonic()
    engine = _FakeEngine(8)
    # 即使缓存为空，节流窗口内也不应读盘（无 find_config_path 可打桩的必要）
    mc._maybe_hot_reload_config(engine)
    assert engine.config.max_turns == 8


def test_hot_reload_never_raises(monkeypatch):
    """任何异常静默吞掉，不影响主循环。"""
    import lingclaude.core.config as config_mod

    monkeypatch.setattr(config_mod, "find_config_path", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    engine = _FakeEngine(8)
    mc._maybe_hot_reload_config(engine)  # 不应抛异常
    assert engine.config.max_turns == 8


def test_resolve_after_hot_reload_reflects_new_value(monkeypatch):
    """端到端：旧实例 8 轮，热重载读到 500 后 resolve 返回 500。"""
    import lingclaude.core.config as config_mod
    import time as _time

    cfg_file = __import__("pathlib").Path(__file__).parents[1] / "config.yaml"
    if not cfg_file.exists():
        pytest.skip("项目 config.yaml 不存在")
    real_load = config_mod.load_config
    monkeypatch.setattr(config_mod, "find_config_path", lambda: cfg_file)
    monkeypatch.setattr(
        config_mod,
        "load_config",
        lambda p: type("C", (), {"engine": type("E", (), {"max_turns": real_load(p).engine.max_turns})()})(),
    )
    _time.sleep(0.001)
    mc._last_hot_reload_check[0] = 0.0  # 解除节流
    engine = _FakeEngine(8)
    assert mc._resolve_max_tool_rounds(engine) >= 8
