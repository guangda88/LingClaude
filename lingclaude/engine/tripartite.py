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

from lingclaude.core import policy_loader

LEDGER = Path("data/arch_ledger")
ERR_LEDGER = Path("data/ledger/verification-errors-20260925.md")
STALE_AFTER_HOURS = 168  # 7d：账本死掉的前兆（proxy3 断供前科）
COVERAGE_RED_BELOW = 1.0  # 候选归宿覆盖率 <100% 即红（候选消失即红）

# ── 借鉴①（docs/research/self-evolution-assessment-20260925.md §四.4）：回收档位化 ──
# DGM stepping-stone 对标：劣解是垫脚石永不真丢。轻回收=撤运行时+归档可复活（有
# revival_clause 即 light）；重回收=彻底删除须技术债理由。仪表层只透传档位，不裁决。
RECYLE_TIERS = {
    "light": "轻回收——撤运行时+归档可复活（record 须带 revival_clause）",
    "heavy": "重回收——彻底删除，须技术债理由入账",
}

# ── 借鉴②：显式 fitness（AlphaEvolve evaluator 对标）——归宿的分数化，防「有辨别无执法」 ──
# 迁移/回收为正贡献；观察期弱正（期权保留，DGM 开放式探索）；翻案负分（机制自我修正成本）；
# pending 无分。只观测不裁决：分数低于阈值仅入仪表，不触发自动处置。
FITNESS_WEIGHTS = {
    "migrated": 1.0,
    "recycled": 0.8,
    "observing": 0.3,
    "reverted": -1.0,
    "pending": 0.0,
}

# ── 借鉴④：观察期多样性保留（任务类型分布漂移 → 观察期重置信号，只观测）──
M6_SNAPSHOT_DIR = LEDGER / "arch_m6_snapshot"
DRIFT_RESET_THRESHOLD = 0.35  # 相邻快照归一化分布差超此值 → reset_recommended=True


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
    """健康度四指标——只观测不裁决。fitness/task_drift 为借鉴②④的扩展指标。"""
    freshness_hours: float
    disposition_coverage: float
    same_actor_violations: list[str] = field(default_factory=list)
    cycle_report: dict[str, int] = field(default_factory=dict)
    red_flags: list[str] = field(default_factory=list)
    fitness: dict[str, Any] = field(default_factory=dict)
    task_drift: dict[str, Any] = field(default_factory=dict)


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
        d_rec = _load_json(seam_recycled)
        # 档位优先读 record 固化字段（recycle_tier），缺字段时按 revival_clause 兜底推断
        tier = d_rec.get("recycle_tier") or ("light" if d_rec.get("revival_clause") else "heavy")
        candidates.append({"item": "SeamType.TRANSPORT", "disposition": "recycled",
                           "evidence": str(seam_recycled), "tier": tier})
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
                    "recycled": "recycled", "removed": "recycled"}.get(state, "pending")
            candidates.append({"item": d.get("file", p.name), "disposition": disp,
                               "evidence": str(p), "guard": d.get("guard")})
            report[disp] += 1

    # 去重：同一 item 可能被多源记录（ratchet 用裸名如 event_exempt，exemption 用 core/X.py 全路径），
    # 归一键 = 文件基名（去 core/ 前缀与 .py 后缀）；保留更强归宿（recycled/migrated > observing > pending），
    # removed 状态 = 回收（recycled），证据路径合并——防双计、防状态映射盲区伪 pending。
    # 2026-09-25 棘轮二格（9666f90）暴露的碰撞 bug：五桥 file=plugins/memory/<桥>/bridge.py，
    # 裸 basename 全撞成 bridge（5 件并 1）——通用载体文件名改用父目录名做归一键。
    _generic = {"bridge.py", "plugin.py", "__init__.py", "main.py"}

    def _norm(item: str) -> str:
        parts = item.split("/")
        base = parts[-1]
        if base in _generic and len(parts) >= 2:
            return parts[-2].removesuffix(".py")
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



def fitness_for_candidate(c: dict[str, Any]) -> float:
    """借鉴②：单候选显式 fitness（AlphaEvolve evaluator 对标）。只算分不裁决。"""
    return FITNESS_WEIGHTS.get(c.get("disposition", "pending"), 0.0)


def fitness_summary(candidates: list[dict[str, Any]], cycle_report: dict[str, int]) -> dict[str, Any]:
    """候选 fitness 总分 + 与 cycle_report 的互证（防台账口径漂移）。

    互证规则：按 cycle_report 计数的加权期望必须等于逐候选求和——两者不等说明
    candidates 与 report 脱钩（台账口径漂移），返回 mismatch=True 供上层亮红。
    """
    total = sum(fitness_for_candidate(c) for c in candidates)
    expected = sum(n * FITNESS_WEIGHTS[k] for k, n in cycle_report.items())
    return {
        "score": round(total, 2),
        "expected_from_report": round(expected, 2),
        "mismatch": abs(total - expected) > 1e-6,
        "weights": dict(FITNESS_WEIGHTS),
    }


def task_type_drift(snap_dir: Path | None = None, threshold: float | None = None) -> dict[str, Any]:
    """借鉴④：任务类型分布漂移观测（DGM 开放式探索对标）。

    数据源 = arch_m6_snapshot 的 all_events（datalog 聚合，4 类事件）。
    相邻两份快照 L1 归一化差超阈值 → 观察期重置建议（只观测不裁决，不自动重置）。
    快照不足两份 → observed=False（如实：无数据不是零漂移）。
    """
    snap_dir = snap_dir or M6_SNAPSHOT_DIR
    # E13 外置（2026-09-30）：调用方显式传参优先；None → tuning.drift_reset_threshold
    # （yaml 热更），未配置回退代码常量 DRIFT_RESET_THRESHOLD
    if threshold is None:
        threshold = policy_loader._tuned(
            "drift_reset_threshold", DRIFT_RESET_THRESHOLD, lo=0.01, hi=1.0
        )
    out: dict[str, Any] = {"observed": False, "reset_recommended": False}
    if not snap_dir.exists():
        out["note"] = f"snapshot dir missing: {snap_dir}"
        return out
    snaps = sorted(snap_dir.glob("*.json"))
    if len(snaps) < 2:
        out["note"] = f"insufficient snapshots: {len(snaps)}"
        return out
    dists = []
    for p in snaps[-2:]:
        try:
            ev = _load_json(p).get("all_events") or {}
        except (json.JSONDecodeError, OSError):
            out["note"] = f"unreadable snapshot: {p.name}"
            return out
        total = sum(ev.values())
        dists.append({k: v / total for k, v in ev.items()} if total else {})
    a, b = dists
    keys = set(a) | set(b)
    l1 = sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys)
    out.update(observed=True, l1_distance=round(l1, 4), threshold=threshold,
               old=snaps[-2].name, new=snaps[-1].name,
               reset_recommended=bool(l1 > threshold))
    return out

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
    fit = fitness_summary(inputs["candidates"], report)
    if fit["mismatch"]:
        red.append("fitness-mismatch(candidates-vs-report)")
    drift = task_type_drift()
    return MetricsRecord(freshness_hours=round(freshness, 2), disposition_coverage=round(coverage, 4),
                         same_actor_violations=violations, cycle_report=report, red_flags=red,
                         fitness=fit, task_drift=drift)


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
