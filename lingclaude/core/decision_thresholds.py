"""NanoJev rules 层：决策阈值统一读口（thresholds.yaml 对应物）。

设计（2026-10-27）:
  - 唯一消费口 = get_thresholds(): dict，经 PolicyLoader.get("decision_thresholds")
    获得热更（mtime watch，30s 节流；改 yaml 不重启生效）。
  - fail-open 双层兜底: PolicyLoader 读失败返回 {} → 本模块再回退内置缺省
    _DEFAULTS；调用方按 .get() 取键，任何一层缺键都不炸。
  - 阈值边界纪律（契约「模型记忆不作数」）: 上限钳制在代码侧，yaml 抬得再高
    也过不了 _clamp——阈值表是运营调参面，不是提权面。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 内置缺省（yaml 缺失/缺键时回退；与 policies/decision_thresholds.yaml 注释同步）
_DEFAULTS: dict = {
    "success": {"gray_base": 0.5},
    "route": {"fast_min": 0.5},
    "limits": {"max_actions_per_session": 500, "warn_ratio": 0.9},
}

# 钳制上界：防 yaml 被误配成天文数字或负数（fail-safe，非 fail-open 的部分）
_CLAMPS: dict = {
    ("success", "gray_base"): (0.0, 0.95),
    ("route", "fast_min"): (0.0, 0.95),
    ("limits", "max_actions_per_session"): (1, 100000),
    ("limits", "warn_ratio"): (0.0, 1.0),
}


def get_thresholds() -> dict:
    """读决策阈值表（热更）；读失败回退内置缺省。永不抛异常。"""
    try:
        from lingclaude.core.policy_loader import get as policy_get

        data = policy_get("decision_thresholds")
    except Exception as e:  # noqa: BLE001 — 阈值读口绝不阻断主流程
        logger.warning("decision_thresholds load failed, fallback defaults: %s", e)
        return {k: dict(v) for k, v in _DEFAULTS.items()}
    if not isinstance(data, dict) or not data:
        return {k: dict(v) for k, v in _DEFAULTS.items()}
    return data


def get_value(section: str, key: str):
    """取单值：yaml 值 → 钳制 → 缺键回退缺省。返回 float。"""
    raw = get_thresholds().get(section, {}).get(key)
    if raw is None:
        raw = _DEFAULTS.get(section, {}).get(key)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        raw_default = _DEFAULTS.get(section, {}).get(key)
        val = float(raw_default) if raw_default is not None else 0.0
    lo, hi = _CLAMPS.get((section, key), (0.0, 1.0))
    return min(max(val, lo), hi)
