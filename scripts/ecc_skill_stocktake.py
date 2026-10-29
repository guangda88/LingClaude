#!/usr/bin/env python3
"""⑩ ECC skill_stocktake：一次性对照脚本（2026-10-29 五方评审第4批）。

把 ECC 的 skills/agents 清单按关键词映射到 lc 六层 security_gate
（lingmemory/security_gate.py GateLayer: command/data/message/interface/model/changeset），
输出「ECC 覆盖面 vs lc 6 层 gate」缺口表——只对照、不导入。

用途：识别 ECC 生态里 lc 六层尚未显式覆盖的安全相关 skill 类别，
为「选摘」提供依据（不做全量导入——五方共识）。

用法: python3 scripts/ecc_skill_stocktake.py [--ecc-dir /tmp/ecc_stocktake]
退出码: 0=正常产出
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# ECC skill 名/描述 → lc GateLayer 的关键词映射（按首个命中归类）
# lc 六层: command/data/message/interface/model/changeset
LAYER_KEYWORDS: dict[str, list[str]] = {
    "command": ["shell", "bash", "command", "exec", "terminal", "sandbox",
                "script", "hook", "permission", "dangerous"],
    "data": ["data", "database", "sql", "schema", "privacy", "pii",
             "secret", "credential", "token", "mask", "redact"],
    "message": ["message", "chat", "notify", "webhook", "email", "relay",
                "communicat", "collaborat"],
    "interface": ["ui", "ux", "accessibility", "frontend", "web", "mobile",
                  "css", "design", "api-design"],
    "model": ["llm", "model", "prompt", "inference", "context", "token-budget",
              "eval", "benchmark", "hallucination", "alignment"],
    "changeset": ["git", "commit", "review", "patch", "diff", "merge",
                  "audit", "test", "ci", "release", "security-hardening"],
}

# 明确与安全/治理相关的 ECC 目录名前缀（决定「缺口表」关注子集）
SECURITY_RELEVANT = [
    "security", "audit", "permission", "sandbox", "secret", "credential",
    "privacy", "pii", "review", "validation", "hardening", "policy",
]


def classify_dir(name: str) -> str | None:
    n = name.lower()
    for layer, kws in LAYER_KEYWORDS.items():
        if any(k in n for k in kws):
            return layer
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ecc-dir", default="/tmp/ecc_stocktake")
    args = ap.parse_args()

    ecc = Path(args.ecc_dir)
    skills_dir = ecc / "skills"
    agents_dir = ecc / "agents"
    if not skills_dir.is_dir():
        print(f"ECC 目录不存在: {skills_dir}（先 sparse clone）", file=sys.stderr)
        return 2

    skills = sorted(p.name for p in skills_dir.iterdir() if p.is_dir())
    agents = sorted(p.stem for p in agents_dir.glob("*.md")) if agents_dir.is_dir() else []

    layer_counts: Counter[str] = Counter()
    unmapped: list[str] = []
    for s in skills:
        layer = classify_dir(s)
        if layer:
            layer_counts[layer] += 1
        else:
            unmapped.append(s)

    sec_relevant = [s for s in skills
                    if any(k in s.lower() for k in SECURITY_RELEVANT)]

    report = {
        "meta": {
            "source": "https://github.com/affaan-m/ECC (sparse: skills+agents)",
            "generated": "2026-10-29",
            "purpose": "一次性对照（不导入）——五方评审第4批⑩",
        },
        "ecc_totals": {"skills": len(skills), "agents": len(agents)},
        "mapped_to_lc_gate_layers": dict(layer_counts),
        "unmapped_sample": unmapped[:25],
        "unmapped_total": len(unmapped),
        "security_relevant_skills": sec_relevant,
        "lc_gate_layers": ["command", "data", "message", "interface",
                           "model", "changeset"],
        "note": (
            "lc 六层 gate 是运行时执行点（每工具调用必过），ECC skills 是"
            "静态知识资产（按需加载）——对照只回答『哪类风险 ECC 有显式"
            "资产而 lc gate 未显式命名』，不构成导入建议。"
        ),
    }

    out = Path("data/spill/ecc_stocktake_20261029.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"OK report={out}")
    print(f"  ECC: {len(skills)} skills / {len(agents)} agents")
    print(f"  mapped: {dict(layer_counts)}")
    print(f"  unmapped: {len(unmapped)} (sample: {unmapped[:8]})")
    print(f"  security_relevant: {len(sec_relevant)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
