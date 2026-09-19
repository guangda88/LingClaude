#!/usr/bin/env python3
"""P2-5a (2026-09-19, 幻觉调研第三批) — 降级链实测化排序探活器（CLI 薄壳）。

调研 §5 裁决：降级链排序按「可用率 × 幻觉率倒数」实测化，每月
model_health_probe 输出真实可用率，产出排序建议 + CI gate 输入。

设计（与运行时 P1-F1 探活的分工）：
  - P1-F1（lingclaude/model/provider_probe.py）：运行时路由前置探活，
    每次路由前 GET /models，管「此刻能不能用」——低 TTL、热路径。
  - 本脚本：离线批量探活（全 provider），管「这一版的清单排序对不对」——
    输出 JSON 报告 + 重排建议，供人工确认后改 /home/ai/lingcode/config.json
    的 task_routes；评分核心在 lingclaude/model/health_ranking.py（纯函数）。

Usage:
    python scripts/model_health_probe.py                  # 探活全清单 → 报告
    python scripts/model_health_probe.py --config X.json  # 指定配置
    python scripts/model_health_probe.py --out PATH       # 报告输出路径
    python scripts/model_health_probe.py --bench-report P # 附幻觉率项（bench --live 产出）
    python scripts/model_health_probe.py --strict         # 有 hard_4xx 死节点时 exit 1（CI gate）

Exit codes:
    0 — 无清单级死节点（或全部探活 unknown：网络受限不误杀）
    1 — 存在 hard_4xx 死节点仍在清单（--strict）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lingclaude.model.health_ranking import build_report, hallucination_rates  # noqa: E402
from lingclaude.model.provider_probe import ProviderProbe  # noqa: E402

DEFAULT_CONFIG = Path(os.environ.get("LINGCODE_CONFIG", "/home/ai/lingcode/config.json"))
DEFAULT_OUT = Path("data/hallucination_survey_20260919/model_health_report.json")


def load_routes(config_path: Path) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """读 lingcode config，返回 (providers, task_routes)。

    providers: {name: {base_url, api_key}}（api_key 经 TaskRouter 同源解析，
    覆盖 ${VAR} 引用与知名 provider env 映射）
    task_routes: {route_key: [{provider, model}, ...]}（保持配置序）
    """
    from lingclaude.model.task_router import TaskRouter

    raw = json.loads(config_path.read_text(encoding="utf-8"))
    routing = raw.get("routing", {})
    providers: dict[str, dict] = {}
    for name, pdef in routing.get("providers", {}).items():
        if isinstance(pdef, str):
            pdef = {"type": pdef}
        if not isinstance(pdef, dict):
            continue
        if pdef.get("enabled", True) is False:
            continue
        providers[name] = {
            "base_url": pdef.get("base_url", ""),
            "api_key": pdef.get("api_key", ""),
        }
    # api_key 解析与 TaskRouter 同源（env var 引用 ${VAR} / 已知 provider 映射）
    router = TaskRouter(config_path=config_path)
    for name in providers:
        pinfo = router._providers.get(name)
        if pinfo is not None:
            providers[name]["api_key"] = pinfo.api_key
    routes: dict[str, list[dict]] = {}
    for key, tr in routing.get("task_routes", {}).items():
        if isinstance(tr, dict):
            routes[key] = list(tr.get("models", []) or [])
    return providers, routes


def probe_all(providers: dict[str, dict]) -> dict[str, dict]:
    """对全部 provider 探活，返回评分核心所需的 probe_results。

    每个 provider 只探一次（provider 级 /models 端点；模型级差异由
    运行时真实调用暴露，不在本脚本逐模型探——配额成本不成比例）。
    """
    from lingclaude.model.health_ranking import compute_available_rate

    probe = ProviderProbe()
    results: dict[str, dict] = {}
    for name, info in providers.items():
        r = probe.check(name, info["base_url"], info["api_key"])
        results[name] = {
            "status": r.status,
            "http_code": r.http_code,
            "detail": r.detail[:120],
            "available_rate": compute_available_rate(r.status, bypass_round=r.bypass_round),
            "excluded": r.excluded,
        }
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="降级链实测化排序探活器（P2-5a）")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--bench-report", type=Path, default=None,
                    help="幻觉 bench 报告路径（提供幻觉率项；缺省仅按可用率排序）")
    ap.add_argument("--strict", action="store_true",
                    help="CI gate：存在 hard_4xx 死节点仍在清单时 exit 1")
    args = ap.parse_args()

    providers, routes = load_routes(args.config)
    if not providers:
        print(json.dumps({"error": f"no providers in {args.config}"}, ensure_ascii=False))
        return 1

    print(f"探活 {len(providers)} 个 provider ...", file=sys.stderr)
    probe_results = probe_all(providers)
    hallu = hallucination_rates(args.bench_report)
    report = build_report(routes, probe_results, hallu)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"报告已写入 {args.out}", file=sys.stderr)

    # 摘要
    for name in sorted(probe_results):
        pr = probe_results[name]
        mark = "✗" if pr["excluded"] else ("~" if pr["status"] == "rate_limited" else "✓")
        score = report["providers"][name]["score"]
        print(f"  {mark} {name}: {pr['status']} http={pr['http_code'] or '-'} score={score}")

    dead = report["dead_providers_in_routes"]
    if args.strict and dead:
        print(f"\n[strict] {len(dead)} 个清单级死节点仍在路由清单 — gate 红灯", file=sys.stderr)
        for d in dead:
            print(f"  route={d['route']} provider={d['provider']} http={d['http_code']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
