"""P1/P2 调参外置回归测试（2026-09-30）。

覆盖：_tuned 核心语义（回退/覆盖/钳位/类型/热更）+ 12 个消费点接线锚 +
def 时快照消除锚（failure_triage / provider_probe / hot_reload_trigger）。
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from lingclaude.core import policy_loader as pl


@pytest.fixture()
def tuned_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    pl.reset()
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    yield tmp_path
    pl.reset()


def _write_tuning(tuned_dir: Path, body: str) -> None:
    (tuned_dir / "coding_runtime.yaml").write_text(
        f"tuning:\n{body}\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# _tuned 核心语义
# ---------------------------------------------------------------------------


class TestTunedCore:
    def test_missing_falls_back(self, tuned_dir: Path) -> None:
        assert pl._tuned("dedup_threshold", 3) == 3
        assert pl._tuned("poll_interval", 0.5) == 0.5

    def test_null_falls_back(self, tuned_dir: Path) -> None:
        _write_tuning(tuned_dir, "  dedup_threshold: null")
        assert pl._tuned("dedup_threshold", 3) == 3

    def test_value_overrides_as_int(self, tuned_dir: Path) -> None:
        _write_tuning(tuned_dir, "  dedup_threshold: 5")
        v = pl._tuned("dedup_threshold", 3)
        assert v == 5
        assert isinstance(v, int)

    def test_fractional_float_rejected_for_int_default(self, tuned_dir: Path) -> None:
        """yaml 给 5.9、默认 int → 回退（防静默截断歧义）；5.0 整数值接受。"""
        _write_tuning(tuned_dir, "  dedup_threshold: 5.9\n  tool_event_max: 300.0")
        assert pl._tuned("dedup_threshold", 3) == 3
        assert pl._tuned("tool_event_max", 200) == 300

    def test_clamp_lo_hi(self, tuned_dir: Path) -> None:
        _write_tuning(tuned_dir, "  dedup_threshold: 1\n  stream_line_buf_max: 99999999")
        assert pl._tuned("dedup_threshold", 3, lo=2, hi=50) == 2
        assert pl._tuned("stream_line_buf_max", 65536, lo=1024, hi=1_048_576) == 1_048_576

    def test_bool_rejected(self, tuned_dir: Path) -> None:
        """isinstance(True, int) 陷阱：bool 必须明确排除。"""
        _write_tuning(tuned_dir, "  dedup_threshold: true")
        assert pl._tuned("dedup_threshold", 3, lo=2, hi=50) == 3

    def test_bad_value_falls_back(self, tuned_dir: Path) -> None:
        _write_tuning(tuned_dir, "  dedup_threshold: 'abc'")
        assert pl._tuned("dedup_threshold", 3) == 3

    def test_int_value_promotes_to_float_default(self, tuned_dir: Path) -> None:
        _write_tuning(tuned_dir, "  poll_interval: 1")
        v = pl._tuned("poll_interval", 0.5)
        assert v == 1.0
        assert isinstance(v, float)

    def test_hot_update_via_get(self, tuned_dir: Path) -> None:
        """改 yaml → get() mtime watch → _tuned 立即吃到新值（真热更锚）。"""
        _write_tuning(tuned_dir, "  dedup_threshold: 5")
        assert pl._tuned("dedup_threshold", 3) == 5
        time.sleep(0.05)
        _write_tuning(tuned_dir, "  dedup_threshold: 7")
        assert pl._changed(tuned_dir / "coding_runtime.yaml") is True
        assert pl.get("coding_runtime").get("tuning", {}).get("dedup_threshold") == 7
        assert pl._tuned("dedup_threshold", 3) == 7


# ---------------------------------------------------------------------------
# 消费点接线（P1 阈值/节流 + P2 缓冲/探针）
# ---------------------------------------------------------------------------


class TestConsumers:
    def test_verify_cadence_reads_tuning(self, tuned_dir: Path) -> None:
        from lingclaude.engine.verify_cadence import VerifyCadenceHook

        _write_tuning(tuned_dir, "  dedup_threshold: 2")
        hook = VerifyCadenceHook()
        results = [hook.observe_call("mcp__x__y", {"k": 1}) for _ in range(2)]
        assert any("重复调用" in (r or "") for r in results), "阈值=2 应在第 2 次触发"

    def test_verify_cadence_default_when_absent(self, tuned_dir: Path) -> None:
        from lingclaude.engine.verify_cadence import VerifyCadenceHook

        hook = VerifyCadenceHook()
        results = [hook.observe_call("mcp__x__y", {"k": 1}) for _ in range(3)]
        assert not any("重复调用" in (r or "") for r in results[:2])
        assert any("重复调用" in (r or "") for r in results[2:3])

    def test_failure_triage_none_resolves_at_call(self, tuned_dir: Path) -> None:
        """def 时快照消除锚：None 在函数体内按当轮 yaml 解析。"""
        from lingclaude.engine import failure_triage

        _write_tuning(tuned_dir, "  doom_loop_threshold: 2")
        v = failure_triage.triage_failure("err", ["err"])
        assert v.triage_class == failure_triage.TriageClass.DOOM_LOOP

    def test_failure_triage_explicit_wins(self, tuned_dir: Path) -> None:
        from lingclaude.engine import failure_triage

        _write_tuning(tuned_dir, "  doom_loop_threshold: 2")
        v = failure_triage.triage_failure("err", ["err"], loop_threshold=10)
        assert v.triage_class != failure_triage.TriageClass.DOOM_LOOP

    def test_provider_probe_none_resolves(self, tuned_dir: Path) -> None:
        from lingclaude.model.provider_probe import ProviderProbe

        _write_tuning(tuned_dir, "  provider_probe_timeout: 1.5")
        assert ProviderProbe()._timeout == 1.5

    def test_provider_probe_explicit_wins(self, tuned_dir: Path) -> None:
        from lingclaude.model.provider_probe import ProviderProbe

        _write_tuning(tuned_dir, "  provider_probe_timeout: 1.5")
        assert ProviderProbe(timeout=9.0)._timeout == 9.0

    def test_hot_reload_trigger_defaults_from_tuning(self, tuned_dir: Path) -> None:
        from lingclaude.engine.hot_reload_trigger import HotReloadTrigger

        _write_tuning(tuned_dir, "  scan_interval: 5.5\n  scan_config_interval: 7.7")
        t = HotReloadTrigger(tools_dir=tuned_dir / "t", agents_dir=tuned_dir / "a")
        assert t._scan_interval == 5.5
        assert t._config_check_interval == 7.7

    def test_trigger_explicit_zero_respected(self, tuned_dir: Path) -> None:
        """测试注入 0.0（假值）也必须原样生效——只 None 才走 yaml。"""
        from lingclaude.engine.hot_reload_trigger import HotReloadTrigger

        _write_tuning(tuned_dir, "  scan_interval: 5.5")
        t = HotReloadTrigger(
            tools_dir=tuned_dir / "t", agents_dir=tuned_dir / "a", scan_interval=0.0
        )
        assert t._scan_interval == 0.0

    def test_model_call_interval_reads_tuning(self, tuned_dir: Path) -> None:
        from lingclaude.core import model_call

        _write_tuning(tuned_dir, "  hot_reload_interval: 12.0")
        assert model_call._hot_reload_interval() == 12.0

    def test_background_poll_interval(self, tuned_dir: Path) -> None:
        from lingclaude.engine import background

        _write_tuning(tuned_dir, "  poll_interval: 0.2")
        assert background._poll_interval() == 0.2

    def test_llm_probe_timeout(self, tuned_dir: Path) -> None:
        from lingclaude.core import llm_probe

        _write_tuning(tuned_dir, "  llm_probe_timeout: 9")
        assert llm_probe._probe_timeout() == 9.0

    def test_plugin_forge_headless_timeout(self, tuned_dir: Path) -> None:
        from lingclaude.plugins.tools import plugin_forge

        _write_tuning(tuned_dir, "  headless_probe_timeout: 45")
        assert plugin_forge._headless_probe_timeout() == 45.0

    def test_full_tui_paste_max_reads_tuning(self, tuned_dir: Path) -> None:
        from lingclaude.cli import full_tui

        _write_tuning(tuned_dir, "  paste_store_max: 50")
        v = full_tui.policy_loader._tuned(
            "paste_store_max", full_tui._PASTE_STORE_MAX, lo=20, hi=5000
        )
        assert v == 50

    def test_tripartite_explicit_wins_and_none_path(self, tuned_dir: Path) -> None:
        from lingclaude.engine import tripartite

        _write_tuning(tuned_dir, "  drift_reset_threshold: 0.9")
        # 显式传参优先：路径不存在也先过 _tuned 分支再走 missing-dir 分支
        out = tripartite.task_type_drift(threshold=0.1, snap_dir=Path("/nonexistent"))
        assert out.get("reset_recommended") is False
        # None → yaml 路径（同样落在 missing-dir 返回，但执行了 _tuned）
        out2 = tripartite.task_type_drift(snap_dir=Path("/nonexistent"))
        assert out2.get("observed") is False


# ---------------------------------------------------------------------------
# get() 自身节流间隔（防自指递归）
# ---------------------------------------------------------------------------


class TestSelfWatchInterval:
    def test_default_when_absent(self, tuned_dir: Path) -> None:
        assert pl._self_watch_interval() == pl._WATCH_INTERVAL

    def test_reads_cached_tuning(self, tuned_dir: Path) -> None:
        _write_tuning(tuned_dir, "  watch_interval: 10")
        pl.get("coding_runtime")  # 装缓存
        assert pl._self_watch_interval() == 10.0

    def test_clamped(self, tuned_dir: Path) -> None:
        _write_tuning(tuned_dir, "  watch_interval: 0.001")
        pl.get("coding_runtime")
        assert pl._self_watch_interval() == 1.0
        _write_tuning(tuned_dir, "  watch_interval: 99999")
        pl.hot_update()
        assert pl._self_watch_interval() == 600.0
