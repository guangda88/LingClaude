"""灵元 P3: 轻通道插片 —— PolicyLoader 配置读穿 + digest 惰性重建。

4 个无状态对象（verification_gate / pattern_recognizer / loop_detector /
verify_cadence）走轻通道：不占槽（无资源持有），策略改动经 PolicyLoader
mtime watch 热更，调用点惰性重建（无 lease/排空，纯 digest 判据变化即重建）。

对位主方案 §3.2：轻通道 = PolicyLoader 配置读穿 + digest 变化惰性重建。
策略文件：lingclaude/core/policies/coding_runtime.yaml
"""

from __future__ import annotations

import logging
from typing import Any

from lingclaude.core import policy_loader
from lingclaude.core.slot import config_digest

logger = logging.getLogger(__name__)

_POLICY_NAME = "coding_runtime"


def _policy() -> dict[str, Any]:
    """读 coding_runtime 策略（PolicyLoader 缓存 + mtime watch 热更）。"""
    return policy_loader.get(_POLICY_NAME)


def _section(key: str) -> dict[str, Any]:
    data = _policy()
    sec = data.get(key)
    return sec if isinstance(sec, dict) else {}


# ---------------------------------------------------------------------------
# verification_gate：VerificationConfig 外置 + digest 重建
# ---------------------------------------------------------------------------
def _resolve(base_val: Any, strat_val: Any) -> Any:
    """单字段叠加：策略值非 null 则覆盖，否则沿用 base（config）值。"""
    return base_val if strat_val is None else strat_val


def build_verification_config(base: Any = None) -> Any:
    """叠加策略到 base VerificationConfig：策略非 null 字段覆盖 config 值。

    P3 设计：config.verification（P0 主干通路）是基线，策略文件的
    verification_gate 段是外置叠加层 —— 策略字段填具体值则覆盖（策略调参零
    重启），留 null/缺省沿用 config 值。这样同时满足 P0（config.yaml
    verification 生效）+ P3（策略外置热更）。base=None 退化为 config.py 默认值。
    """
    from lingclaude.core.config import VerificationConfig

    sec = _section("verification_gate")
    base = base if base is not None else VerificationConfig()

    def _tuple_str(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
        val = _resolve(None, sec.get(key))
        if isinstance(val, list):
            return tuple(str(x) for x in val)
        return default

    return VerificationConfig(
        enabled=bool(_resolve(base.enabled, sec.get("enabled"))),
        syntax_check=bool(_resolve(base.syntax_check, sec.get("syntax_check"))),
        test_run=bool(_resolve(base.test_run, sec.get("test_run"))),
        test_command=str(_resolve(base.test_command, sec.get("test_command"))),
        blocked_extensions=_tuple_str("blocked_extensions", base.blocked_extensions),
        allowed_write_roots=_tuple_str("allowed_write_roots", base.allowed_write_roots),
        max_tool_calls_per_session=int(
            _resolve(base.max_tool_calls_per_session, sec.get("max_tool_calls_per_session"))
        ),
    )


def verification_digest(base: Any = None) -> str:
    """叠加后 digest：config 基线 + 策略段共同决定（任一变化即重建）。"""
    return config_digest({"base": repr(base), "strategy": _section("verification_gate")})


# ---------------------------------------------------------------------------
# verify_cadence：env 开关迁入 YAML（env 优先级保留）
# ---------------------------------------------------------------------------
def build_verify_cadence() -> Any:
    """从策略构建 VerifyCadenceHook；env LINGCLAUDE_VERIFY_CADENCE 优先。"""
    from lingclaude.engine.verify_cadence import VerifyCadenceHook

    sec = _section("verify_cadence")
    enabled = sec.get("enabled")
    if enabled is None:
        return VerifyCadenceHook()  # 内部读 env，默认开
    return VerifyCadenceHook(enabled=bool(enabled))


def verify_cadence_digest() -> str:
    return config_digest(_section("verify_cadence"))


# ---------------------------------------------------------------------------
# loop_detector：阈值外置（streak 计数声明易失，swap 清零可接受）
# ---------------------------------------------------------------------------
def build_loop_detector() -> Any:
    """从策略构建 _ToolLoopDetector（当前无可外置构造参数，digest 仅作重建判据）。"""
    from lingclaude.engine.loop.tool_loop_detector import _ToolLoopDetector

    return _ToolLoopDetector()


def loop_detector_digest() -> str:
    return config_digest(_section("loop_detector"))


# ---------------------------------------------------------------------------
# pattern_recognizer：detectors 注册数据化
# ---------------------------------------------------------------------------
_DETECTOR_REGISTRY: dict[str, str] = {
    "long_method": "lingclaude.self_optimizer.learner.patterns.LongMethodDetector",
    "unused_variable": "lingclaude.self_optimizer.learner.patterns.UnusedVariableDetector",
    "hardcoded_secret": "lingclaude.self_optimizer.learner.patterns.HardcodedSecretDetector",
    "duplicate_code": "lingclaude.self_optimizer.learner.patterns.DuplicateCodeDetector",
    "empty_block": "lingclaude.self_optimizer.learner.patterns.EmptyBlockDetector",
    "complexity": "lingclaude.self_optimizer.learner.patterns.ComplexityDetector",
}


def _build_detector(name: str) -> Any:
    dotted = _DETECTOR_REGISTRY.get(name)
    if dotted is None:
        logger.warning("pattern_recognizer: 未知 detector %r（跳过）", name)
        return None
    mod_path, _, cls_name = dotted.rpartition(".")
    try:
        import importlib

        cls = getattr(importlib.import_module(mod_path), cls_name)
        return cls()
    except Exception:  # noqa: BLE001 — detector 构建失败跳过（fail-soft）
        logger.warning("pattern_recognizer: detector %s 构建失败（跳过）", name, exc_info=True)
        return None


def build_pattern_recognizer() -> Any:
    """从策略 detectors 列表构建 PatternRecognizer（注册数据化）。"""
    from lingclaude.self_optimizer.learner.patterns import PatternRecognizer

    sec = _section("pattern_recognizer")
    names = sec.get("detectors")
    if not isinstance(names, list) or not names:
        return PatternRecognizer()  # 默认全 detector
    detectors = [d for d in (_build_detector(str(n)) for n in names) if d is not None]
    return PatternRecognizer(detectors=detectors)


def pattern_recognizer_digest() -> str:
    return config_digest(_section("pattern_recognizer"))
