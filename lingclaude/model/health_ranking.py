"""P2-5a (2026-09-19, 幻觉调研第三批) — 降级链实测化排序评分核心。

调研 §5 裁决：降级链排序按「可用率 × 幻觉率倒数」实测化，每月
model_health_probe 输出真实可用率，产出排序建议 + CI gate 输入。

分工：
  - 本模块：纯函数评分/报告组装（无网络、无 IO 副作用）——可测试、
    可被 scripts/model_health_probe.py 与未来灵族共享 eval harness 复用。
  - scripts/model_health_probe.py：探活执行 + CLI（网络交互薄壳）。

公式（调研 §5）：
    score = available_rate × (1 - hallucination_rate)
  - available_rate: ok=1.0，429/408 限流=0.5（节点活着但挤，不与 ok 等权），
    其余（hard_4xx/网络错误/未知）=0.0（unknown 不定罪但也不得分）
  - hallucination_rate: 幻觉 bench 最近报告的 per-provider 幻觉率；
    无数据按 0（仅按可用率排序，不虚构数据）
"""
from __future__ import annotations

import json
import time
from pathlib import Path

# 429 在可用率中的折算权重：节点活着但限流，半健康
RATE_LIMITED_CREDIT = 0.5


def compute_available_rate(status: str, *, bypass_round: bool) -> float:
    """探活结论 → 可用率折算。

    与 provider_probe.ProbeResult 语义对齐：ok=1.0，rate_limited=0.5，
    hard_4xx/network_error/server_error/unknown_http=0.0。
    """
    if status == "ok":
        return 1.0
    if bypass_round or status == "rate_limited":
        return RATE_LIMITED_CREDIT
    return 0.0


def hallucination_rates(bench_report: Path | None) -> dict[str, float]:
    """读幻觉 bench 最近报告，返回 {provider: 幻觉率 0~1}。

    报告不存在/结构不符 → 空表（仅按可用率排序，不虚构数据）。
    报告结构（tests/hallucination_bench.py --live 产出）：
        {"per_provider": {provider: {"total": N, "hallucinated": M}}}
    """
    if bench_report is None or not Path(bench_report).exists():
        return {}
    try:
        data = json.loads(Path(bench_report).read_text(encoding="utf-8"))
        out: dict[str, float] = {}
        for provider, entry in (data.get("per_provider") or {}).items():
            if not isinstance(entry, dict):
                continue
            total = int(entry.get("total", 0))
            if total > 0:
                out[provider] = min(1.0, max(0.0, float(entry.get("hallucinated", 0)) / total))
        return out
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def build_report(
    routes: dict[str, list[dict]],
    probe_results: dict[str, dict],
    hallu: dict[str, float],
) -> dict:
    """组装实测化报告：provider 健康分 + 每个 route 的重排建议。

    Args:
        routes: {route_key: [{provider, model}, ...]}（保持配置序）
        probe_results: {provider: {status, http_code, detail, available_rate, excluded}}
            （由探活执行层产出；available_rate 已经折算）
        hallu: {provider: 幻觉率 0~1}
    """
    scores: dict[str, float] = {}
    for name, pr in probe_results.items():
        h_rate = hallu.get(name, 0.0)
        scores[name] = round(pr["available_rate"] * (1.0 - h_rate), 4)

    route_suggestions: dict[str, dict] = {}
    dead_in_routes: list[dict] = []
    for key, models in routes.items():
        ranked = []
        for m in models:
            pname = m.get("provider", "")
            pr = probe_results.get(pname, {})
            ranked.append({
                "provider": pname,
                "model": m.get("model", ""),
                "score": scores.get(pname),
                "probe_status": pr.get("status", "not_probed"),
                "excluded": pr.get("excluded", False),
            })
            if pr.get("excluded"):
                dead_in_routes.append({"route": key, **ranked[-1]})
        # 重排建议：仅按 score 降序（stable 排序，同分保持配置序）
        suggested = sorted(ranked, key=lambda x: -(x["score"] if x["score"] is not None else 0.0))
        current_ids = [f"{m['provider']}/{m['model']}" for m in ranked]
        suggested_ids = [f"{m['provider']}/{m['model']}" for m in suggested]
        route_suggestions[key] = {
            "current_order": current_ids,
            "suggested_order": suggested_ids,
            "reorder_needed": suggested_ids != current_ids,
        }
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "formula": "score = available_rate × (1 - hallucination_rate)；429 计 0.5",
        "providers": {
            name: {**probe_results[name], "score": scores[name]}
            for name in probe_results
        },
        "hallucination_rates": hallu,
        "route_suggestions": route_suggestions,
        "dead_providers_in_routes": dead_in_routes,
    }
