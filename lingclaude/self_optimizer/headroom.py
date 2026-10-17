"""HCI-lite headroom 向量（P1-6，2026-10-17）：改进边际收益判据。

对应 FINAL_synthesis.md 阶段一-6【cc-P0-2 + codex 向量修正】：
  - cc 语义：headroom 低则不触发（定时优化空转烧预算）
  - codex 修正：**不造单一总分**（单机造总分会造出第二个 Goodhart 面），
    用可解释向量 + 分量阈值表决，观测「价值量」而非「内部量」

三分量（全部来自已有廉价数据源，headroom 模块本身零 IO、纯函数）：
  - pass_headroom      任务通过率空间 = 1 - pass_rate
                       （源：daemon 周期性 P0 基准分，_last_benchmark_score）
  - recurrence_headroom 修复不粘率 = 30d rolled_back / settled
                       （源：实验台账——回滚率高=修复没粘住=仍有真问题）
  - cost_headroom      单位任务成本（未接入——无单位任务计价数据源，
                       恒 None 不参与表决；接入后填数即可，不造假数据）

表决语义（边际收益 > 0 才触发定时优化）：
  任一已知分量越过其阈值 → True（还有值得花的改进空间）；
  分量未知 → 不表决（弃权）；全部分量未知 → True（保持旧定时行为，
  防首轮死锁）。**永不聚合为单值。**

使用方：trigger._check_time 经 context["headroom"] 消费（纯定时触发
的闸门）；daemon 在 check_all_conditions 前装配。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 分量触发阈值（可解释、可单独调；故意不进 config.yaml——
#: 调阈值=改量具，走「评估器只许人工升级」同一治理路径）
PASS_HEADROOM_TRIGGER = 0.05       # 通过率距满分 ≥5% → 有空间
RECURRENCE_HEADROOM_TRIGGER = 0.30  # 30d 回滚率 ≥30% → 修复不粘


@dataclass
class HeadroomVector:
    """三分量 headroom 向量。None = 该分量数据未知（弃权）。"""

    pass_headroom: float | None = None
    recurrence_headroom: float | None = None
    cost_headroom: float | None = None  # 预留，当前恒 None
    details: dict[str, Any] = field(default_factory=dict)

    def marginal_benefit_positive(self) -> bool:
        """任一已知分量越阈值 → True；全未知 → True（保持旧定时行为）。"""
        votes: list[bool] = []
        if self.pass_headroom is not None:
            votes.append(self.pass_headroom >= PASS_HEADROOM_TRIGGER)
        if self.recurrence_headroom is not None:
            votes.append(
                self.recurrence_headroom >= RECURRENCE_HEADROOM_TRIGGER
            )
        if self.cost_headroom is not None:
            votes.append(self.cost_headroom > 0.0)  # 预留：接入后启用
        if not votes:
            return True  # 全未知 → 不设闸（防首轮死锁，退化=旧行为）
        return any(votes)

    def summary(self) -> str:
        """人话摘要（进 trigger reason / 台账，可解释性落点）。"""
        parts = []
        if self.pass_headroom is not None:
            parts.append(f"pass={self.pass_headroom:.3f}")
        else:
            parts.append("pass=?")
        if self.recurrence_headroom is not None:
            parts.append(f"recur={self.recurrence_headroom:.3f}")
        else:
            parts.append("recur=?")
        parts.append("cost=N/A")
        return "headroom[" + ",".join(parts) + "]"


def compute_headroom(
    *,
    benchmark_score: float | None = None,
    benchmark_total: float = 100.0,
    rollback_rate_30d: float | None = None,
) -> HeadroomVector:
    """从廉价输入装配向量。零 IO——取数责任在调用方（daemon）。"""
    pass_headroom: float | None = None
    if benchmark_score is not None and benchmark_total > 0:
        pass_headroom = max(0.0, 1.0 - benchmark_score / benchmark_total)
    return HeadroomVector(
        pass_headroom=pass_headroom,
        recurrence_headroom=rollback_rate_30d,
        cost_headroom=None,
        details={
            "benchmark_score": benchmark_score,
            "benchmark_total": benchmark_total,
            "rollback_rate_30d": rollback_rate_30d,
        },
    )
