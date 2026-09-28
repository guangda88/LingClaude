"""P3 红→绿验收：轻通道 4 件 PolicyLoader digest 惰性重建。

对应主方案 §3.2 / §五：轻通道 = 配置读穿 + digest 变化惰性重建，策略调参零重启。

本测试验证轻通道策略层（light_channel + LightChannelRuntime）独立于主干接入：
- 4 个构建函数从 policies/coding_runtime.yaml 读配置
- 策略 digest 变化 → 惰性重建（不调用手动 swap）
- detectors 注册数据化（策略列表驱动）

注：主干 CodingRuntime 的类型安全接入（保 verification_gate 等属性类型）
在 P4 双轨制消灭时统一处理 —— 本阶段先立策略层 + 惰性重建协议。
"""

from __future__ import annotations

from lingclaude.core import light_channel as lc
from lingclaude.engine.light_channel_runtime import LightChannelRuntime


# ---------------------------------------------------------------------------
# 4 个构建函数读策略
# ---------------------------------------------------------------------------
class TestBuildFromPolicy:
    def test_verification_config_from_policy(self) -> None:
        vc = lc.build_verification_config()
        assert vc.enabled is True
        assert vc.syntax_check is True
        assert ".py" in vc.blocked_extensions

    def test_verify_cadence_from_policy(self) -> None:
        hook = lc.build_verify_cadence()
        assert hook.enabled is True

    def test_loop_detector_from_policy(self) -> None:
        det = lc.build_loop_detector()
        assert det is not None

    def test_pattern_recognizer_detectors_from_policy(self) -> None:
        pr = lc.build_pattern_recognizer()
        # 策略列了 6 个 detector
        assert len(pr.detectors) == 6


# ---------------------------------------------------------------------------
# digest 判据 + 惰性重建
# ---------------------------------------------------------------------------
class TestDigestAndLazyRebuild:
    def test_verification_digest_changes_with_policy(self, monkeypatch) -> None:
        d1 = lc.verification_digest()
        # 模拟策略变化：monkeypatch section 返回不同 dict
        monkeypatch.setattr(lc, "_section", lambda k: {"enabled": False} if k == "verification_gate" else {})
        d2 = lc.verification_digest()
        assert d1 != d2

    def test_lazy_channel_rebuilds_on_digest_change(self, monkeypatch) -> None:
        rt = LightChannelRuntime()
        gate1 = rt.verification_gate.get()
        assert gate1.enabled is True
        # 策略变化（enabled 改 false）→ digest 变 → 下次 get 重建
        monkeypatch.setattr(lc, "_section", lambda k: {"enabled": False} if k == "verification_gate" else {})
        gate2 = rt.verification_gate.get()
        assert gate2 is not gate1  # 重建了新实例
        assert gate2.enabled is False

    def test_lazy_channel_caches_when_digest_unchanged(self) -> None:
        rt = LightChannelRuntime()
        g1 = rt.verification_gate.get()
        g2 = rt.verification_gate.get()
        assert g1 is g2  # digest 未变 → 复用（不重建）


# ---------------------------------------------------------------------------
# detectors 注册数据化
# ---------------------------------------------------------------------------
class TestDetectorRegistration:
    def test_registry_covers_policy_names(self) -> None:
        for name in ("long_method", "unused_variable", "hardcoded_secret",
                     "duplicate_code", "empty_block", "complexity"):
            assert name in lc._DETECTOR_REGISTRY

    def test_unknown_detector_skipped(self, monkeypatch) -> None:
        monkeypatch.setattr(
            lc, "_section",
            lambda k: {"detectors": ["long_method", "nonexistent"]} if k == "pattern_recognizer" else {},
        )
        pr = lc.build_pattern_recognizer()
        # 未知 detector 跳过，只建出 1 个
        assert len(pr.detectors) == 1
