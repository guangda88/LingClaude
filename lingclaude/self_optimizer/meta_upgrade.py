"""R3 元层改进跨任务迁移验证器（2026-10-22，Hyperagents/DGM-H 借鉴）。

背景：S0-1（RSI 阶段〇止血）把评估器冻结为「只许人工升级」——方向对，
但缺「验证后可收编」的正门。本模块就是那扇门：对元层改进提案（评估器/
优化器自身的阈值、权重、惩罚系数类改动），在**两类不同任务量具**上各跑
一次金丝雀对比——

  任务A behavior_replay : 行为回放代价（ReplayObjective，越低越好）
  任务B structure_bench : 行为基准题集（BenchmarkEvaluator，越高越好）

两域均不劣化（容忍带内）→ 裁决 adoptable（可收编，收编动作仍人工执行
S0-1 解冻升级）；任一域劣化超容忍或量具故障 → rejected（fail-closed：
验证不了就不许收编）。

纪律边界：
- 本工具**不写任何被控配置**——只产验证裁决并入验证台账
  （core.verify_ledger.record_verify）。「默认关、开门要过双任务验证」
  的验证半边；解冻升级永远是人工动作。
- 双量具全部只读（AST 扫描 + ATTACH 只读），单轮秒级。
- 覆盖面 v1：量化型元层提案（回放权重 / 基准阈值 / 候选参数组）。
  代码级提案（换评分算法）超界——先人工 review 以新版本号入
  evaluator_meta，再对本模块报告的参数面跑双任务验证。

用法::

    from lingclaude.self_optimizer.meta_upgrade import MetaUpgradeVerifier

    v = MetaUpgradeVerifier(target=".")
    report = v.verify(
        proposal="回放 heal_loss 0.8→1.0（加强漏干预惩罚）",
        replay_weights={"heal_loss": 1.0},
    )
    print(report.overall)   # adoptable / rejected

CLI::

    lingclaude daemon meta-upgrade --proposal "..." \
        --replay-heal-loss 1.0 --bench-threshold max_complexity=12 \
        --params tool_repeat_limit=4
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import logging

logger = logging.getLogger(__name__)

#: 单域劣化容忍带（相对基线 %）。劣化在带内视为语料噪声不计。
#: 调严/调宽须人工——治理不变量：自动路径不得放宽对自身改动的验证。
TOLERANCE_PCT = 1.0

_SLUG_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")


@dataclass
class CanaryResult:
    """单任务金丝雀读数。"""

    task: str            # behavior_replay / structure_bench
    baseline: float
    candidate: float
    better_is: str       # lower / higher
    delta_pct: float     # (candidate-baseline)/|baseline| ×100
    verdict: str         # pass / regress / invalid
    detail: str = ""


@dataclass
class MetaUpgradeReport:
    """双任务验证裁决单（JSON 可落盘）。"""

    proposal: str
    overall: str         # adoptable / rejected
    results: list[CanaryResult] = field(default_factory=list)
    ts: str = ""
    tolerance_pct: float = TOLERANCE_PCT

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal": self.proposal,
            "overall": self.overall,
            "ts": self.ts,
            "tolerance_pct": self.tolerance_pct,
            "results": [
                {
                    "task": r.task,
                    "baseline": r.baseline,
                    "candidate": r.candidate,
                    "better_is": r.better_is,
                    "delta_pct": round(r.delta_pct, 3),
                    "verdict": r.verdict,
                    "detail": r.detail,
                }
                for r in self.results
            ],
        }


def _judge(
    task: str, baseline: float, candidate: float, better_is: str, detail: str = ""
) -> CanaryResult:
    """按方向性与容忍带出单任务裁决。

    invalid（基线非有限正数）fail-closed 计为不通过——两把尺子任何一把
    读不出数，提案就不许收编（H17：账目与检查器冲突以检查器为准）。
    """
    if baseline is None or candidate is None:
        return CanaryResult(task, 0.0, 0.0, better_is, 0.0, "invalid",
                            "量具无读数")
    if baseline <= 0 or baseline != baseline or candidate != candidate:  # NaN/非正
        return CanaryResult(task, baseline, candidate, better_is, 0.0,
                            "invalid", f"基线读数非正/非有限: {baseline}")
    delta_pct = (candidate - baseline) / abs(baseline) * 100.0
    worse = delta_pct > TOLERANCE_PCT if better_is == "lower" else delta_pct < -TOLERANCE_PCT
    return CanaryResult(
        task, baseline, candidate, better_is, delta_pct,
        "regress" if worse else "pass", detail,
    )


class MetaUpgradeVerifier:
    """元层提案双任务金丝雀验证器（只读，秒级，人工触发）。"""

    def __init__(
        self,
        target: str = ".",
        replay_db: str | Path | None = None,
        reports_dir: Path | None = None,
    ) -> None:
        self.target = target
        self._replay_db = replay_db
        self._reports_dir = (
            reports_dir
            or Path(target).resolve() / ".lingclaude" / "meta_upgrade_reports"
        )

    # ---- 任务量具（隔离成方法便于测试替身） ----------------------------
    def _replay_read(
        self, params: dict[str, float], weights: dict[str, float] | None
    ) -> float:
        from lingclaude.self_optimizer.replay_objective import ReplayObjective

        kwargs: dict[str, Any] = {}
        if self._replay_db is not None:
            kwargs["db_path"] = self._replay_db
        if weights:
            if "heal_loss" in weights:
                kwargs["heal_loss"] = float(weights["heal_loss"])
            if "nudge_cost" in weights:
                kwargs["nudge_cost"] = float(weights["nudge_cost"])
        obj = ReplayObjective(**kwargs)
        if not obj.traces:
            return float("nan")  # 语料空 → invalid（fail-closed）
        return float(obj.evaluate(params))

    def _bench_read(self, thresholds: dict[str, Any] | None) -> float:
        from lingclaude.self_optimizer.benchmark import BenchmarkEvaluator

        bench = BenchmarkEvaluator(self.target)
        if thresholds:
            bench.adopt_params(dict(thresholds))
        return float(bench.run().score)

    @staticmethod
    def _current_behavior_params() -> dict[str, float]:
        from lingclaude.core.policy_loader import get as _policy_get

        pol = _policy_get("behavior_policy") or {}
        return {
            "consecutive_fail_limit": int(pol.get("consecutive_fail_limit", 2)),
            "tool_repeat_limit": int(pol.get("tool_repeat_limit", 3)),
        }

    # ---- 主入口 --------------------------------------------------------
    def verify(
        self,
        proposal: str,
        *,
        replay_weights: dict[str, float] | None = None,
        bench_thresholds: dict[str, Any] | None = None,
        candidate_params: dict[str, float] | None = None,
    ) -> MetaUpgradeReport:
        """跑双任务金丝雀。任何量具异常 → 该任务 invalid → 整单 rejected。

        提案覆盖语义：replay_weights=回放评分权重覆盖（评估器侧）；
        bench_thresholds=基准阈值覆盖（评估器侧）；candidate_params=
        候选被控参数组（被控侧，双任务同参对照）。至少给一样，
        全空 = 无效提案直接 rejected。
        """
        ts = datetime.now().isoformat(timespec="seconds")
        if not (replay_weights or bench_thresholds or candidate_params):
            return MetaUpgradeReport(
                proposal=proposal, overall="rejected", ts=ts,
                results=[CanaryResult("all", 0.0, 0.0, "lower", 0.0,
                                      "invalid", "提案无任何可验证覆盖面")],
            )

        cur_params = self._current_behavior_params()
        cand_params = dict(candidate_params) if candidate_params else cur_params
        results: list[CanaryResult] = []

        # 任务A：行为回放代价（低好）
        try:
            base_a = self._replay_read(cur_params, None)
            cand_a = self._replay_read(
                cand_params, replay_weights
            ) if replay_weights else self._replay_read(cand_params, None)
            results.append(_judge("behavior_replay", base_a, cand_a, "lower"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[R3] 任务A量具异常: %s", exc)
            results.append(CanaryResult("behavior_replay", 0.0, 0.0, "lower",
                                        0.0, "invalid", f"量具异常: {exc}"))

        # 任务B：基准题集分（高好）
        try:
            base_b = self._bench_read(None)
            cand_b = self._bench_read(bench_thresholds)
            results.append(_judge("structure_bench", base_b, cand_b, "higher"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[R3] 任务B量具异常: %s", exc)
            results.append(CanaryResult("structure_bench", 0.0, 0.0, "higher",
                                        0.0, "invalid", f"量具异常: {exc}"))

        overall = (
            "adoptable"
            if all(r.verdict == "pass" for r in results)
            else "rejected"
        )
        report = MetaUpgradeReport(proposal=proposal, overall=overall,
                                   results=results, ts=ts)
        self._persist(report)
        self._ledger(report)
        return report

    # ---- 副作用（均为增强件，失败不阻塞裁决） --------------------------
    def _persist(self, report: MetaUpgradeReport) -> None:
        try:
            self._reports_dir.mkdir(parents=True, exist_ok=True)
            slug = _SLUG_RE.sub("_", report.proposal)[:40] or "proposal"
            path = self._reports_dir / (
                f"{report.ts.replace(':', '')}_{slug}.json"
            )
            path.write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("[R3] 裁决单落盘失败（不阻塞）", exc_info=True)

    def _ledger(self, report: MetaUpgradeReport) -> None:
        try:
            from lingclaude.core.verify_ledger import record_verify

            lines = "; ".join(
                f"{r.task}={r.verdict}({r.baseline:.2f}->{r.candidate:.2f},"
                f"{r.delta_pct:+.2f}%)" for r in report.results
            )
            record_verify(
                kind="meta_upgrade",
                claim=f"R3 双任务验证[{report.overall}]: {report.proposal}",
                evidence=lines,
                trace_id=f"meta_{report.ts.replace(':', '')}",
                via="MetaUpgradeVerifier",
            )
        except Exception:  # noqa: BLE001
            logger.warning("[R3] 验证台账入账失败（不阻塞）", exc_info=True)
