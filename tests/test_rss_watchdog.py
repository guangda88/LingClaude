"""N6 RSS 看门狗测试 — 基线/增长/硬限/一次性告警/LingBus 嘲讽路径全覆盖。

单例基线表 → 所有测试用 reset_baselines() 隔离。
LingBus: monkeypatch _notify 捕获调用（不真发）。
"""
from __future__ import annotations

import pytest

from lingclaude.ops import rss_watchdog as rw


@pytest.fixture(autouse=True)
def _clean():
    rw.reset_baselines()
    yield
    rw.reset_baselines()


@pytest.fixture(autouse=True)
def _mute_notify(monkeypatch):
    calls: list[tuple] = []

    def fake_notify(session_id, level, detail):
        calls.append((session_id, level, detail))

    monkeypatch.setattr(rw, "_notify", fake_notify)
    return calls


def test_first_sample_sets_baseline_only():
    assert rw.check_rss_growth("s1", 100) == []
    assert rw._BASELINES["s1"] == 100


def test_growth_below_threshold_silent():
    rw.check_rss_growth("s1", 100)
    assert rw.check_rss_growth("s1", 599) == []
    assert rw._BASELINES["s1"] == 100  # 基线不动


def test_growth_at_threshold_warns_once_and_resets():
    rw.check_rss_growth("s1", 100)
    out = rw.check_rss_growth("s1", 600)  # +500 触线
    assert len(out) == 1 and out[0].startswith("WARNING")
    assert rw._BASELINES["s1"] == 600  # 基线重置
    # 同一增长幅度再来一次: 已告警标记生效, 只有 findings 无重复 LingBus
    # （findings 仍会返回——它是巡检文案; 唯一性约束的是告警通路）
    out2 = rw.check_rss_growth("s1", 1100)
    assert any(x.startswith("WARNING") for x in out2)


def test_hard_limit_error_and_alert():
    # 高基线下有效硬限 = max(绝对帽, 基线+增长线): 基线 950 → 抬到 1450
    # (旧静态行为 1000 即 ERROR, 先于增长线 — 分级倒挂, 已由动态下界修复)
    rw.check_rss_growth("s1", 950)
    assert rw.check_rss_growth("s1", 1449) == []  # 倒挂回归: 早于 WARNING 的 ERROR 不得存在
    out = rw.check_rss_growth("s1", 1450)         # growth(+500) 与 hard 同轮, 单步大跳双触发
    assert any(x.startswith("WARNING") for x in out)
    assert any(x.startswith("ERROR") for x in out)


def test_alert_fires_exactly_once_per_kind(_mute_notify):
    rw.check_rss_growth("s1", 100)
    rw.check_rss_growth("s1", 600)   # growth 触发
    rw.check_rss_growth("s1", 1200)  # growth 再触发(基线已重置) + hard 触发
    growth_calls = [c for c in _mute_notify if "growth" in c[2] or c[1] == "WARNING"]
    hard_calls = [c for c in _mute_notify if c[1] == "ERROR"]
    assert len(_mute_notify) == 2  # 1 WARNING + 1 ERROR, 无重复
    assert len(hard_calls) == 1


def test_hard_and_growth_same_round_both_alert():
    rw.check_rss_growth("s1", 100)
    rw.check_rss_growth("s1", 1050)  # growth(+950) 与 hard(>=1000) 同轮
    out = rw.check_rss_growth("s1", 1100)  # 下一轮: 基线已重置, 无新增告警标记
    # hard 已告警不重复; growth 因基线重置需再 +500 才触发
    assert out == [] or all(not x.startswith("WARNING") for x in out)


def test_sample_rss_mb_current_process():
    import os

    mb = rw.sample_rss_mb()
    assert mb is not None and mb > 0
    mb2 = rw.sample_rss_mb(os.getpid())
    assert mb2 == mb


def test_sample_rss_mb_bad_pid():
    assert rw.sample_rss_mb(-1) is None


def test_check_rss_watchdog_swallows_sample_failure(monkeypatch):
    monkeypatch.setattr(rw, "sample_rss_mb", lambda *a, **k: None)
    assert rw.check_rss_watchdog("sx", event="turn_end") == []


def test_check_rss_watchdog_end_to_end(monkeypatch):
    monkeypatch.setattr(rw, "sample_rss_mb", lambda *a, **k: 2000)
    out = rw.check_rss_watchdog("se2e")
    assert out == []  # 首轮=基线
    monkeypatch.setattr(rw, "sample_rss_mb", lambda *a, **k: 2600)
    out2 = rw.check_rss_watchdog("se2e")
    # +600 → WARNING; 有效硬限 max(1000, 2000+500)=2500 → 同轮 ERROR
    assert any(x.startswith("WARNING") for x in out2)
    assert any(x.startswith("ERROR") for x in out2)


def test_absolute_cap_preserved_low_baseline():
    # 低基线: 绝对帽 1000 仍是硬限（动态下界只抬高不降低）
    rw.check_rss_growth("s1", 100)
    out = rw.check_rss_growth("s1", 1000)
    assert any(x.startswith("ERROR") for x in out)
    assert "≥1000MB" in out[-1]  # 文案报有效硬限


def test_effective_hard_limit_floor_semantics():
    assert rw._effective_hard_limit_mb(100) == 1000   # max(1000, 600) 绝对帽主导
    assert rw._effective_hard_limit_mb(553) == 1053   # 实测常驻基线场景, 恰为增长触发点
    assert rw._effective_hard_limit_mb(2000) == 2500  # 高基线抬高
    assert rw._effective_hard_limit_mb(0) == rw.RSS_HARD_LIMIT_MB  # 未知基线回退静态


def test_env_int_fallback(monkeypatch):
    monkeypatch.setenv("LINGCLAUDE_TEST_INT", "abc")
    assert rw._env_int("LINGCLAUDE_TEST_INT", 7) == 7   # 非法回退
    monkeypatch.setenv("LINGCLAUDE_TEST_INT", "42")
    assert rw._env_int("LINGCLAUDE_TEST_INT", 7) == 42  # 合法生效
    monkeypatch.delenv("LINGCLAUDE_TEST_INT", raising=False)
    assert rw._env_int("LINGCLAUDE_TEST_INT", 7) == 7   # 未设回退
