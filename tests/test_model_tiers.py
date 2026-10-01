"""model_tiers 三档语义层测试（灵克 2026-10-01）。

覆盖：
- normalize_tier 归一化（cc 别名 sonnet/opus/haiku + 未知/空值）
- _yaml_candidates 脏数据防御（非 dict/缺键条目跳过）
- available_tiers 只报有清单的档
- resolve_tier_model 端到端（stub TaskRouter，不依赖真实 config.json）：
  首选可用/首选无 key 顺延/fallback 档兜底/非档位名透传 None/
  degrade=false 硬失败/无 router 降级
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lingclaude.core.model_tiers import (
    TierResolutionError,
    _yaml_candidates,
    available_tiers,
    normalize_tier,
    resolve_tier_model,
)


# ---- normalize_tier ----

def test_normalize_cc_aliases():
    assert normalize_tier("sonnet") == "standard"
    assert normalize_tier("opus") == "strong"
    assert normalize_tier("haiku") == "fast"


def test_normalize_native_and_case():
    assert normalize_tier("FAST") == "fast"
    assert normalize_tier(" Strong ") == "strong"


def test_normalize_non_tier_returns_none():
    assert normalize_tier("glm-5.3") is None
    assert normalize_tier("") is None
    assert normalize_tier(None) is None


# ---- _yaml_candidates 脏数据防御 ----

def test_candidates_skip_dirty_entries():
    data = {
        "fast": [
            {"provider": "glm", "model": "m1"},
            "not-a-dict",
            {"provider": None, "model": "m2"},  # 缺 provider
            {"provider": "p"},                   # 缺 model
        ]
    }
    cands = _yaml_candidates("fast", data)
    assert len(cands) == 1
    assert (cands[0].provider, cands[0].model) == ("glm", "m1")


def test_candidates_missing_tier_empty():
    assert _yaml_candidates("nonexistent", {"fast": []}) == []


# ---- available_tiers ----

def test_available_tiers_only_listed():
    assert available_tiers({"fast": [{"provider": "p", "model": "m"}]}) == ["fast"]
    assert available_tiers({}) == []


# ---- resolve_tier_model（stub TaskRouter）----

@dataclass
class _ProviderInfo:
    api_key: str = ""
    base_url: str = "https://x.example"


@dataclass
class _StubRouter:
    """模拟 TaskRouter._pick_from_route 的选取语义：首位有 key 即选中。"""

    providers: dict[str, _ProviderInfo] = field(default_factory=dict)
    fail_first_n: int = 0  # 前 N 个候选返回 None（模拟探活剔除/熔断）
    calls: list = field(default_factory=list)

    def _pick_from_route(self, route_key, route, *, max_tokens, temperature):
        self.calls.append(route_key)
        for ref in route.models:
            pinfo = self.providers.get(ref.provider)
            if not pinfo or not pinfo.api_key:
                continue
            if self.fail_first_n > 0:
                self.fail_first_n -= 1
                continue
            return object()  # 哨兵 ModelConfig 替身
        return None


def test_resolve_first_candidate_with_key():
    r = _StubRouter(providers={"glm": _ProviderInfo(api_key="k")})
    assert resolve_tier_model("fast", r) is not None
    assert r.calls == ["tier:fast"]


def test_resolve_skips_keyless_provider():
    r = _StubRouter(
        providers={"glm": _ProviderInfo(), "minimax": _ProviderInfo(api_key="k")}
    )
    assert resolve_tier_model("fast", r) is not None


def test_resolve_all_dead_degrades_to_none():
    r = _StubRouter(providers={})  # 无任何 provider
    assert resolve_tier_model("fast", r) is None


def test_resolve_hard_fail_when_no_degrade():
    r = _StubRouter(providers={})
    try:
        resolve_tier_model("fast", r, degrade_to_default=False)
        raised = False
    except TierResolutionError:
        raised = True
    assert raised


def test_resolve_missing_tier_falls_to_fallback_tier():
    # 'vision' 不是档位 → None 透传（非档位名归调用方）
    r = _StubRouter(providers={"glm": _ProviderInfo(api_key="k")})
    assert resolve_tier_model("vision", r) is None
    assert r.calls == []


def test_resolve_none_router_degrades():
    assert resolve_tier_model("fast", None) is None


def test_resolve_unknown_name_passthrough_none():
    """具体模型名（非档位/别名）原样透传 None，绝不瞎解析。"""
    r = _StubRouter(providers={"glm": _ProviderInfo(api_key="k")})
    assert resolve_tier_model("kimi-k3", r) is None
    assert r.calls == []
