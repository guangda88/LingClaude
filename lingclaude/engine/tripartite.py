# 三机制咬合（辨别-执法-纠错）健康度仪表与周期落账
#
# 设计来源：2026-09-25 会话五条机制设计（数据咬合/节奏咬合/错误传导/权限分离/健康度仪表），
# ERR-04 事故后从设计稿真实重建——设计是真实资产，实现必须真实落盘。
# 数据源全部为既有实测 record：arch_m3_redlist_baseline / arch_exemption / arch_seam_recycled /
# arch_m3_redlist_baseline/ratchet-* / arch_law_revision / data/ledger/verification-errors-*。
# 铁律：只观测不裁决（仪表层）；三落款字段必填；候选必须有归宿（消失即红）。
"""Tripartite cycle (identify-enforce-correct) health gauge + cycle ledger."""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LEDGER = Path("data/arch_ledger")
ERR_LEDGER = Path("data/ledger/verification-errors-20260925.md")
STALE_AFTER_HOURS = 168  # 7d：账本死掉的前兆（proxy3 断供前科）
COVERAGE_RED_BELOW = 1.0  # 候选归宿覆盖率 <100% 即红（候选消失即红）


@dataclass
class CycleRecord:
    """单条周期记录——三落款（identified_by/enforced_by/corrected_by）必填。"""
    cycle_id: str
    as_of: str
    identified_by: str
    enforced_by: str
    corrected_by: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    cycle_report: dict[str, int] = field(default_factory=lambda: {
        "recycled": 0, "migrated": 0, "observing": 0, "reverted": 0, "pending": 0})


@dataclass
class MetricsRecord:
    """健康度四指标——只观测不裁决。"""
    freshness_hours: float
    disposition_coverage: float
    same_actor_violations: list[str] = field(default_factory=list)
    cycle_report: dict[str, int] = field(default_factory=dict)
    red_flags: list[str] = field(default_factory=list)


def _mtime_hours(p: Path, now: _dt.datetime) -> float:
    return max(0.0, (now - _dt.datetime.fromtimestamp(p.stat().st_mtime)).total_seconds() / 3600.0)


def _load_json(p: Path) -> dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def collect_cycle001_inputs() -> dict[str, Any]:
    """从既有真实 record 收集 cycle-001 的候选与归宿（不编数据，缺源即记 pending）。"""
    candidates: list[dict[str, Any]] = []
    report = {"recycled": 0, "migrated": 0, "observing": 0, "reverted": 0, "pending": 0}

    seam_recycled = LEDGER / "arch_seam_recycled" / "recycled-transport-seam-20260925.json"
    if seam_recycled.exists():
        candidates.append({"item": "SeamType.TRANSPORT", "disposition": "recycled",
                           "evidence": str(seam_recycled)})
        report["recycled"] += 1

    ratchet = LEDGER / "arch_m3_redlist_baseline" / "ratchet-001-four-buildgate-plugins.json"
    if ratchet.exists():
        d = _load_json(ratchet)
        for item in d.get("moved", []):
            name = item if isinstance(item, str) else item.get("file", str(item))
            candidates.append({"item": name, "disposition": "migrated", "evidence": str(ratchet)})
            report["migrated"] += 1

    exemptions_dir = LEDGER / "arch_exemption" / "M1:core"
    if exemptions_dir.exists():
        for p in sorted(exemptions_dir.glob("*.json")):
            d = _load_json(p)
            state = d.get("state", "pending")
            disp = {"migrated": "migrated", "active": "observing",
                    "removed": "recycled"}.get(state, "pending")
            candidates.append({"item": d.get("file", p.name), "disposition": disp,
                               "evidence": str(p), "guard": d.get("guard")})
            report[disp] += 1

    # 去重：同一 item 可能被多源记录（ratchet 用裸名如 event_exempt，exemption 用 core/X.py 全路径），
    # 归一键 = 文件基名（去 core/ 前缀与 .py 后缀）；保留更强归宿（recycled/migrated > observing > pending），
    # removed 状态 = 回收（recycled），证据路径合并——防双计、防状态映射盲区伪 pending。
    def _norm(item: str) -> str:
        base = item.rsplit("/", 1)[-1]
        return base.removesuffix(".py")

    strength = {"migrated": 2, "recycled": 2, "observing": 1, "pending": 0}
    merged: dict[str, dict[str, Any]] = {}
    for c in candidates:
        c = dict(c)
        c["item"] = _norm(c["item"])
        cur = merged.get(c["item"])
        if cur is None:
            merged[c["item"]] = c
        else:
            if strength[c["disposition"]] > strength[cur["disposition"]]:
                cur["disposition"] = c["disposition"]
                cur["evidence"] = f"{cur['evidence']}+{c['evidence']}"
            elif cur["disposition"] != c["disposition"]:
                cur["evidence"] = f"{cur['evidence']}+{c['evidence']}"
    dedup = list(merged.values())
    fixed = {"recycled": 0, "migrated": 0, "observing": 0, "reverted": 0, "pending": 0}
    for c in dedup:
        fixed[c["disposition"]] += 1
    return {"candidates": dedup, "cycle_report": fixed,
            "dedup_note": f"{len(candidates)} entries -> {len(dedup)} unique items"}


def snap_tripartite_health(now: _dt.datetime | None = None) -> MetricsRecord:
    """健康度四指标：新鲜度 / 归宿覆盖率 / 同体违规 / 四态分布。缺源如实计红，不静默。

    覆盖率语义：「消失即红」测的是无归宿（pending）候选占比——observing 是合法归宿
    （豁免带期限观察中，J4 同日 2026-11-30 到期强制复审），不计红。
    """
    now = now or _dt.datetime.now()
    inputs = collect_cycle001_inputs()
    report = inputs["cycle_report"]
    total = sum(report.values())
    coverage = 1.0 if total == 0 else 1.0 - report["pending"] / total

    sources = [
        LEDGER / "arch_m3_redlist_baseline" / "baseline-ff17eb2.json",
        LEDGER / "arch_m3_redlist_baseline" / "ratchet-001-four-buildgate-plugins.json",
        ERR_LEDGER,
    ]
    existing = [p for p in sources if p.exists()]
    freshness = max((_mtime_hours(p, now) for p in existing), default=float("inf"))

    cyc = CycleRecord(
        cycle_id="001", as_of=now.date().isoformat(),
        identified_by="arch_m3_redlist_baseline(877f000)+M6辨别框架",
        enforced_by="d36e5b9(棘轮首格迁移)+4b9149a(TRANSPORT撤缝)",
        corrected_by="ERR台账(b59c1a1..c32a6fc)+arch_law_revision",
        candidates=inputs["candidates"], cycle_report=report)

    violations: list[str] = []
    tri = [cyc.identified_by, cyc.enforced_by, cyc.corrected_by]
    if len(set(tri)) < 3:
        violations.append("tripartite-attribution-coincident")
    if not ERR_LEDGER.exists():
        violations.append("err-ledger-missing")
    if any(v == "pending" for v in ("",)) or report["pending"] > 0:
        violations.append("pending-candidates-undisposed")

    red: list[str] = []
    if freshness > STALE_AFTER_HOURS:
        red.append(f"freshness>{STALE_AFTER_HOURS}h")
    if coverage < COVERAGE_RED_BELOW:
        red.append("coverage<1.0(candidates-vanished)")
    if violations:
        red.append(";".join(violations))
    return MetricsRecord(freshness_hours=round(freshness, 2), disposition_coverage=round(coverage, 4),
                         same_actor_violations=violations, cycle_report=report, red_flags=red)


def save_tripartite_cycle(out_dir: Path | None = None, now: _dt.datetime | None = None) -> Path:
    """周期记录 + 健康度指标落账为双 JSON record。"""
    now = now or _dt.datetime.now()
    cyc_dir = out_dir or (LEDGER / "arch_tripartite_cycle")
    cyc_dir.mkdir(parents=True, exist_ok=True)
    metrics = snap_tripartite_health(now)
    cyc = CycleRecord(
        cycle_id="001", as_of=now.date().isoformat(),
        identified_by="arch_m3_redlist_baseline(877f000)+M6辨别框架",
        enforced_by="d36e5b9(棘轮首格迁移)+4b9149a(TRANSPORT撤缝)",
        corrected_by="ERR台账(b59c1a1..c32a6fc)+arch_law_revision",
        candidates=collect_cycle001_inputs()["candidates"], cycle_report=metrics.cycle_report)
    payload = {"type": "arch_tripartite_cycle", "key": "cycle-001", "as_of": cyc.as_of,
               "created": now.isoformat(timespec="seconds"),
               "design_source": "2026-09-25 五条机制设计（ERR-04 后从设计稿真实重建）",
               "cycle": asdict(cyc), "metrics": asdict(metrics)}
    out = cyc_dir / "cycle-001.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return out
