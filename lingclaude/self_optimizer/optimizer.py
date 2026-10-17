from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
from typing import Any

# 跨仓契约：显式声明依赖 lingminopt 仓库（灵元「跨仓 = 显式插片契约」）。
# env 可覆盖 LINGMINOPT_PATH，不依赖隐式 editable 安装布局。
from lingclaude.lacp.cross_repo_seam import ensure_import_path as _seam_import

_seam_import("lingminopt")

# lingminopt 未安装时降级（S1 多机无状态部署 2026-10-14）：
# CLI 导入链不许炸；goal=behavior 本地穷举不受影响，其余 goal 在 optimize() 内明确报失败。
try:
    from lingminopt import (
        MinimalOptimizer,
        SearchSpace,
        ExperimentConfig,
    )
    from lingminopt.core.models import OptimizationResult as LMOptResult
except ImportError:
    MinimalOptimizer = None
    SearchSpace = None
    ExperimentConfig = None
    LMOptResult = None
import itertools
import logging
import time as _t

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OptimizationRequest:
    target: str
    goal: str
    params: dict[str, Any]
    config: dict[str, Any]


@dataclass(frozen=True)
class OptimizationResult:
    success: bool
    best_params: dict[str, Any]
    best_score: float
    experiments: int
    duration: float
    error: str = ""
    history: tuple[dict[str, Any], ...] = ()


def _build_search_space(goal: str):
    if SearchSpace is None:  # lingminopt 未安装降级（S1 多机部署）
        return None
    space = SearchSpace()

    if goal == "structure":
        # S0-1 (2026-10-17) RSI round1 阶段〇止血【cc-P0-1，用户已批】：
        # 停用 structure 自调量具回路。原分支把评估量具（max_class_size 等
        # 结构阈值）当搜索旋钮，且经 benchmark._PARAM_TO_CHECK 直通题集判定
        # 阈值——学生自改评分标准，Goodhart 空转（2352→2351 三连实证）。
        # 治理不变量：评估器只许人工升级。返回 None → optimize 管线按
        # 「无搜索空间」跳过本轮（success=False，daemon 失败入档不重试）。
        # 恢复条件：仅当评估器与被搜索对象解耦（终案阶段一 artifact 状态机
        # + 留出集）后经人工评审重启，注释保留以防误判为遗漏。
        logger.warning(
            "[S0-1] goal=structure 自调量具回路已停用（RSI 阶段〇止血）；"
            "本轮不优化。结构阈值现为固定常量，只许人工修改。"
        )
        return None
    space = SearchSpace()
    if goal == "performance":
        space.add_discrete("cache_size", [10, 50, 100, 500])
        space.add_discrete("parallelism", [1, 2, 4])
        space.add_discrete("timeout", [5, 10, 30, 60])
    elif goal == "simplicity":
        space.add_discrete("complexity_threshold", [5, 10, 15])
        space.add_discrete("duplication_penalty", [0.5, 1.0, 2.0])
        space.add_discrete("max_line_length", [80, 100, 120])
    elif goal == "behavior":
        # F1 (2026-09-22): 搜索空间=被控对象 behavior_policy.yaml 的真实阈值
        # （原 structure 空间调的是 benchmark 题集自身阈值 → Goodhart 空转）。
        # F6 (2026-09-23): 双边事件流接入，churn 反力项激活，rl 钳位放开
        # [3,6]→[2,8]（纯误差单边时 churn 项归零，仍安全）。
        space.add_discrete("consecutive_fail_limit", [1, 2, 3, 4, 5, 6])
        space.add_discrete("tool_repeat_limit", [2, 3, 4, 5, 6, 7, 8])

    return space


def _convert_result(lm_result: LMOptResult, duration: float) -> OptimizationResult:
    history: list[dict[str, Any]] = []
    for exp in lm_result.history:
        history.append({
            "experiment_id": exp.experiment_id,
            "params": exp.params,
            "score": exp.score,
        })

    return OptimizationResult(
        success=True,
        best_params=lm_result.best_params,
        best_score=lm_result.best_score,
        experiments=lm_result.total_experiments,
        duration=duration,
        history=tuple(history),
    )


class SynchronousOptimizer:
    def optimize(self, request: OptimizationRequest) -> OptimizationResult:
        start = time.monotonic()
        try:
            if request.goal == "behavior":
                # F1 (2026-09-22): 回放目标函数——error_log 历史序列离线
                # 回放，评估 behavior_policy.yaml 阈值参数。返回值语义
                # （越小越好）与 lingminopt direction="minimize" 契约兼容。
                from lingclaude.self_optimizer.replay_objective import ReplayObjective

                # db_path 可由 config 注入（测试/多环境隔离），默认项目库
                _db = request.config.get("db_path")
                evaluator: Any = (
                    ReplayObjective(db_path=_db) if _db else ReplayObjective()
                )
            else:
                from lingclaude.self_optimizer.evaluator import StructureEvaluator

                evaluator = StructureEvaluator(request.target)
            space = _build_search_space(request.goal)

            # S0-1: structure 已停用（或 lingminopt 未装且非 behavior）→
            # 显式失败短路，不进 MinimalOptimizer 炸 TypeError。
            # daemon 侧走 result.success=False → 失败入档 + 优雅跳轮。
            if space is None and request.goal != "behavior":
                return OptimizationResult(
                    success=False,
                    best_params={},
                    best_score=0,
                    experiments=0,
                    duration=time.monotonic() - start,
                    error=(
                        "[S0-1] goal=%s 无可用搜索空间（structure 自调量具回路"
                        "已于 2026-10-17 停用；评估器只许人工升级）" % request.goal
                    ),
                )

            # F1: goal=behavior 走本地穷举——回放目标函数确定性、纯本地
            # 无 LLM；小离散空间(6×4)随机+早停会漏真最优（2026-09-22
            # 实测 50 trials 早停返回 fl=1，全曲线最优 fl=2）。穷举天然
            # 全覆盖，历史含每个组合，产出与 lingminopt 契约同构。
            if request.goal == "behavior":

                grid = [
                    {"consecutive_fail_limit": fl, "tool_repeat_limit": rl}
                    for fl, rl in itertools.product(
                        [1, 2, 3, 4, 5, 6], [3, 4, 5, 6]
                    )
                ]
                history: list[dict[str, Any]] = []
                best_params: dict[str, Any] = {}
                best_score = float("inf")
                for exp_id, params in enumerate(grid):
                    t0 = _t.monotonic()
                    score = float(evaluator.evaluate(params))
                    history.append(
                        {
                            "experiment_id": str(exp_id),
                            "params": params,
                            "score": score,
                            "duration": _t.monotonic() - t0,
                        }
                    )
                    if score < best_score:
                        best_score, best_params = score, params
                return OptimizationResult(
                    success=True,
                    best_params=best_params,
                    best_score=best_score,
                    experiments=len(grid),
                    duration=time.monotonic() - start,
                    history=tuple(history),
                )

            strategy = request.config.get("strategy", "random")
            max_trials = request.config.get("max_experiments", 20)

            experiment_config = ExperimentConfig(
                max_experiments=max_trials,
                direction="minimize",
                early_stopping_patience=10,
                time_budget=300.0,
            )

            # P2-9a (2026-10-17)【RSI 阶段二-件9】：seed 不再硬编码——
            # 由 daemon 按 cycle 序号派生传入（可复现实验：同 seed 同结果，
            # 换 seed 才是新采样）。缺省回退 42 保持旧行为兼容。
            seed = int(request.config.get("seed", 42))
            opt = MinimalOptimizer(
                evaluate=evaluator.evaluate,
                search_space=space,
                config=experiment_config,
                search_strategy=strategy,
                seed=seed,
            )

            lm_result = opt.run()
            duration = time.monotonic() - start
            return _convert_result(lm_result, duration)

        except Exception as e:
            return OptimizationResult(
                success=False,
                best_params={},
                best_score=0,
                experiments=0,
                duration=time.monotonic() - start,
                error=str(e) + "\n" + traceback.format_exc(),
            )
