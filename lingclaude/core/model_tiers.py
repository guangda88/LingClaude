"""model_tiers.py — 模型三档语义层解析器（灵克 2026-10-01，外部 Agent 联审 P3 项）。

借鉴 cc（Claude Code）HAIKU/SONNET/OPUS_MODEL 三档映射：模型当数据分层，
调用方按「快/标准/强」语义取模型，不写死模型名。

接入点：
1. agent_registry 的 AgentDef.model 字段（"fast"/"standard"/"strong"/具体模型名）
   → resolve_tier_model()；具体模型名原样透传（兼容 cc 式 model: sonnet 之外的写法）
2. core/model_adapter.resolve_model_config 的 config.strong 死引用修复：
   幻觉风险 > 0.5 时真正升级到强档（此前 strong 属性无人设置，路径名存实亡）

设计契约：
- 数据源 lingclaude/core/policies/model_tiers.yaml，走 PolicyLoader 热更
- 读失败/缺档/全候选不可用 → 优雅降级（返回 None 或 fallback 档），绝不抛穿调用方
- 候选解析依赖 TaskRouter（provider 存在性 + key 校验），单测用 stub 隔离
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# 三档语义名（与 model_tiers.yaml 顶层键一一对应）
TIER_FAST = "fast"
TIER_STANDARD = "standard"
TIER_STRONG = "strong"
_KNOWN_TIERS = frozenset({TIER_FAST, TIER_STANDARD, TIER_STRONG})

# 档位别名归一（cc 式 sonnet/opus/haiku 语义兼容）
_TIER_ALIASES: dict[str, str] = {
    "haiku": TIER_FAST,
    "sonnet": TIER_STANDARD,
    "opus": TIER_STRONG,
    "fast": TIER_FAST,
    "standard": TIER_STANDARD,
    "strong": TIER_STRONG,
}


class TierResolutionError(RuntimeError):
    """档位解析硬失败（仅 degrade_to_default=false 且候选全灭时抛出）。"""


@dataclass(frozen=True)
class TierCandidate:
    """档位清单中的一个候选（与 config.json task_routes 条目同构）。"""

    provider: str
    model: str


def normalize_tier(name: str | None) -> str | None:
    """档位名归一：sonnet→standard / opus→strong / haiku→fast；未知返回 None。"""
    if not name:
        return None
    return _TIER_ALIASES.get(name.strip().lower())


def _load_tiers_policy() -> dict[str, Any]:
    """读 model_tiers.yaml；读失败返回空 dict（调用方降级）。"""
    from lingclaude.core.policy_loader import get as policy_get

    try:
        data = policy_get("model_tiers")
    except Exception as e:  # noqa: BLE001 — 策略读失败必须降级不能抛穿
        logger.warning("model_tiers 策略读取失败，三档解析降级: %s", e)
        return {}
    return data if isinstance(data, dict) else {}


def _yaml_candidates(tier: str, data: dict[str, Any]) -> list[TierCandidate]:
    """从策略 dict 抽某档候选清单；条目非法即跳过（防脏数据毒化整档）。"""
    raw = data.get(tier)
    if not isinstance(raw, list):
        return []
    out: list[TierCandidate] = []
    for item in raw:
        if isinstance(item, dict) and item.get("provider") and item.get("model"):
            out.append(
                TierCandidate(provider=str(item["provider"]), model=str(item["model"]))
            )
    return out


def available_tiers(data: dict[str, Any] | None = None) -> list[str]:
    """当前策略里配置了的档位名（有清单的才算），供 /model 与调试展示。"""
    d = data if data is not None else _load_tiers_policy()
    return [t for t in (TIER_FAST, TIER_STANDARD, TIER_STRONG) if _yaml_candidates(t, d)]


def resolve_tier_model(
    tier_name: str,
    task_router: Any,
    *,
    degrade_to_default: bool | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> Any | None:
    """按档位语义解析出一个可用 ModelConfig（复用 TaskRouter 候选解析）。

    Args:
        tier_name: fast/standard/strong（或 haiku/sonnet/opus 别名）
        task_router: TaskRouter 实例（用其 _providers 与 _pick_from_route）
        degrade_to_default: 覆盖 yaml 的 resolve.degrade_to_default
        max_tokens / temperature: 透传给生成的 ModelConfig

    Returns:
        ModelConfig（走 TaskRouter 正式解析路径，含 key/base_url）
        - 档位未知/无清单/策略读失败 → None（调用方回落原路由）
        - degrade_to_default=false 且候选全灭 → TierResolutionError

    逐候选语义：走 TaskRouter._pick_from_route 正式路径（F12b 无 key 跳过 /
    探活剔除 / 熔断 / flash 门禁全部继承），不另造解析逻辑。
    """
    data = _load_tiers_policy()
    norm = normalize_tier(tier_name)
    if norm is None:
        # 非档位名（具体模型名等）→ 不归我管，返回 None 让调用方走原路
        return None

    resolve_cfg = data.get("resolve") if isinstance(data.get("resolve"), dict) else {}
    if degrade_to_default is None:
        degrade = bool(resolve_cfg.get("degrade_to_default", True))
    else:
        degrade = degrade_to_default
    fallback_tier = resolve_cfg.get("fallback_tier", TIER_STANDARD)
    if normalize_tier(str(fallback_tier)) is None:
        fallback_tier = TIER_STANDARD

    candidates = _yaml_candidates(norm, data)
    if not candidates and norm != fallback_tier:
        # 档位缺清单 → 落 fallback 档再试一次
        candidates = _yaml_candidates(fallback_tier, data)

    if not candidates:
        if degrade:
            return None
        raise TierResolutionError(f"tier {norm!r} 无候选清单且 degrade_to_default=false")

    if task_router is None:
        if degrade:
            return None
        raise TierResolutionError("task_router 不可用且 degrade_to_default=false")

    # 候选转 _TaskRoute，复用 _pick_from_route 的全部生产级门禁
    # （无 key 跳过 / 探活 / 熔断 / flash 位门禁），不另造解析逻辑。
    from lingclaude.model.task_router import _ModelRef, _TaskRoute

    route = _TaskRoute(
        description=f"tier:{norm}",
        models=[_ModelRef(provider=c.provider, model=c.model) for c in candidates],
    )
    picked = task_router._pick_from_route(
        f"tier:{norm}", route, max_tokens=max_tokens, temperature=temperature
    )
    if picked is not None:
        return picked

    # 候选全灭：按 degrade 契约决定软降级还是硬失败
    if degrade:
        logger.info("tier %r 候选全灭，降级默认路由", norm)
        return None
    raise TierResolutionError(f"tier {norm!r} 候选全灭且 degrade_to_default=false")
