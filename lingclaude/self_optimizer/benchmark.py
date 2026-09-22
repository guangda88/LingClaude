"""行为基准评测器（P0 实证门禁，2026-09-17）。

对标 DGM/SICA 的实证验证思想：优化器选出的 best_params 不得只凭代理指标
（结构违规数）收账，必须过一道**行为基准**——固定廉价任务集上的得分
不回退才允许应用。

设计约束（灵元铁律 J1/J2）：
- 本模块是插片，不改 StructureEvaluator 主干；daemon 按需启用。
- 基准集默认内置 12 题静态任务（AST 可判定，无需 LLM 调用 → 零成本零
  网络，daemon 周期内可安全跑）；benchmark_path 可指向自定义题集 JSON。
- 分数语义：0-100，越高越好。缓存最近一次分数避免重复计算。

题集格式（JSON）：[{"id": "b01", "desc": "...", "check": "ast_class_count<=N"}]
内置题集是对 lingclaude 自身代码库的结构健康度抽样，判定规则固定，
params 变化会影响判定阈值——这正是"参数变化 → 行为分变化"的可测通道。
"""
from __future__ import annotations

import ast
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 内置基准题：对目标库做 12 项结构健康检查（每题 0/1 分 → 汇总为 0-100）。
# 判定阈值有意取自"合理参数区间"：params 调得越宽松分数越高，
# 调得越严苛分数越低——形成与 best_params 联动的可优化信号。
_BUILTIN_CHECKS: list[dict[str, str]] = [
    {"id": "b01", "desc": "无超 600 行大类", "check": "max_class_lines<=600"},
    {"id": "b02", "desc": "无超 30 方法大类", "check": "max_method_count<=30"},
    {"id": "b03", "desc": "平均圈复杂度 <= 12", "check": "avg_complexity<=12"},
    {"id": "b04", "desc": "高复杂方法占比 <= 15%", "check": "complex_ratio<=0.15"},
    {"id": "b05", "desc": "无语法不可解析文件", "check": "parse_errors==0"},
    {"id": "b06", "desc": "大类平均尺寸 <= 150 行", "check": "avg_class_lines<=150"},
    {"id": "b07", "desc": "方法平均数 <= 12", "check": "avg_method_count<=12"},
    {"id": "b08", "desc": "文件数规模合理(<=800)", "check": "total_files<=800"},
    {"id": "b09", "desc": "类总数规模合理(<=300)", "check": "total_classes<=300"},
    {"id": "b10", "desc": "无空大类(0方法)", "check": "empty_classes==0"},
    {"id": "b11", "desc": "复杂方法数 <= 60", "check": "complex_methods<=60"},
    {"id": "b12", "desc": "大类数 <= 30", "check": "large_classes<=30"},
]


# ── P1-2（2026-09-22）：概率校准指标（Jev 8 项评估矩阵"校准"维度）──
# 数据源说明（codex 修订 2026-09-21 沿用）：内置 12 题是 0/1 结构判定无概率输出，
# 本函数面向 Laya provider 输出（归一化熵 1 - H(p)/log(K)）+ 可选人工标注子集，
# **不覆盖 lc 全链路**。与 daemon P0 实证门禁联动：校准误差不回退才允许应用。


def eval_calibration(predictions: list[float], actuals: list[bool], n_bins: int = 10) -> float:
    """Expected Calibration Error = sum_b (|b|/N) * |acc(b) - conf(b)|。

    **修订 2026-09-21（响应审查）**：原版本 bin_preds 同时当 predictions 和
    actuals 用，导致 ECE 恒等于 0。正确做法：分桶分别存 predictions 和
    actuals，计算桶内均值时从两个独立列表取。

    Args:
        predictions: 模型输出的预测置信度（0.0-1.0），如 Laya 归一化熵 1 - H(p)/log(K)
        actuals: 真实结果（bool），True = 预测正确
        n_bins: 分桶数（默认 10）

    桶号约定（交接文档 P1-2 修订）：pred=0.1 → 桶 1（即 idx=1 的语义修正——
    0.0 归桶 0，非零下界归后桶；等宽桶 int(pred*n_bins) 截断到 n_bins-1）。
    """
    if not predictions or len(predictions) != len(actuals):
        return 0.0
    # 关键：分别追踪 predictions 和 actuals（两个独立列表）
    bins: list[tuple[list[float], list[float]]] = [
        ([], []) for _ in range(n_bins)
    ]  # 每个 bin 存 (predictions 列表, actuals 列表)

    for pred, act in zip(predictions, actuals):
        pred = min(max(pred, 0.0), 1.0)  # 越界截断（防御 provider 异常输出）
        idx = min(int(pred * n_bins), n_bins - 1)
        bins[idx][0].append(pred)                 # 预测概率入 predictions 列
        bins[idx][1].append(1.0 if act else 0.0)  # 真实结果入 actuals 列

    ece = 0.0
    total = len(predictions)
    for pred_list, act_list in bins:
        if not pred_list:  # 空桶跳过
            continue
        bin_acc = sum(act_list) / len(act_list)     # 真实准确率（actuals 列）
        bin_conf = sum(pred_list) / len(pred_list)  # 平均预测概率（predictions 列）
        ece += (len(pred_list) / total) * abs(bin_acc - bin_conf)
    return ece


@dataclass(frozen=True)
class BenchmarkResult:
    score: float          # 0-100
    passed: int
    total: int
    details: dict[str, bool]  # check_id -> pass/fail


class BenchmarkEvaluator:
    """行为基准评测：固定题集 + 参数联动判定 → 0-100 分。

    score 不回退门禁的裁决依据。零 LLM 调用，单轮 <2s（AST 扫描缓存）。
    """

    def __init__(
        self,
        target_path: str = ".",
        benchmark_path: str | Path | None = None,
    ) -> None:
        self.target_path = Path(target_path)
        # 注意: 必须 dict 级拷贝 —— list(_BUILTIN_CHECKS) 是浅拷贝, 各实例共享
        # 同一批 dict, adopt_params 改写阈值时会污染模块级常量（跨实例串味）。
        self._checks: list[dict[str, str]] = [dict(c) for c in _BUILTIN_CHECKS]
        if benchmark_path is not None:
            custom = json.loads(Path(benchmark_path).read_text(encoding="utf-8"))
            if isinstance(custom, list) and custom:
                self._checks = custom
        # 「复杂方法」判定线（_scan 用）: 默认 15, 可被 adopt_params 映射为
        # config 的 triggers.max_complexity —— 打通 参数→分数 耦合通道。
        self._complex_threshold: float = 15.0
        self._last_result: BenchmarkResult | None = None

    # ---- 内部：一次 AST 扫描提取全部指标（题集共享，避免重复遍历） ----

    def _scan(self) -> dict[str, float]:
        import warnings

        total_classes = large_classes = empty_classes = 0
        total_methods = complex_methods = 0
        complexity_sum = 0
        class_lines: list[int] = []
        method_counts: list[int] = []
        max_class_lines = 0
        parse_errors = 0
        total_files = 0

        skip = StructureEvaluatorLike._SKIP_DIRS  # 复用同一排除集
        for py_file in self.target_path.rglob("*.py"):
            if any(part in skip for part in py_file.parts):
                continue
            total_files += 1
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
                    tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError, PermissionError, OSError):
                parse_errors += 1
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                total_classes += 1
                body_fns = [n for n in node.body if isinstance(n, ast.FunctionDef)]
                lines = (node.body[-1].end_lineno or node.lineno) - node.lineno + 1 if node.body else 0
                max_class_lines = max(max_class_lines, lines)
                class_lines.append(lines)
                method_counts.append(len(body_fns))
                if lines > 600:
                    large_classes += 1
                if not body_fns:
                    empty_classes += 1
                total_methods += len(body_fns)
                for fn in body_fns:
                    c = StructureEvaluatorLike.complexity(fn)
                    complexity_sum += c
                    if c > self._complex_threshold:
                        complex_methods += 1

        n_cls = len(class_lines) or 1
        n_mth = total_methods or 1
        return {
            "max_class_lines": float(max_class_lines),
            "max_method_count": float(max(method_counts) if method_counts else 0),
            "avg_complexity": complexity_sum / n_mth,
            "complex_ratio": complex_methods / n_mth,
            "parse_errors": float(parse_errors),
            "avg_class_lines": sum(class_lines) / n_cls,
            "avg_method_count": sum(method_counts) / n_cls,
            "total_files": float(total_files),
            "total_classes": float(total_classes),
            "empty_classes": float(empty_classes),
            "complex_methods": float(complex_methods),
            "large_classes": float(large_classes),
        }

    # ---- 对外：跑基准 → 0-100 分 ----

    # best_params 键 → (题集 id, 新阈值)。打通 参数→判定阈值 耦合通道：
    # 未列出的参数键不影响分数（保持既定阈值）。
    _PARAM_TO_CHECK: dict[str, tuple[str, str]] = {
        "max_class_size": ("b01", "max_class_lines<={:.0f}"),
        "max_method_count": ("b02", "max_method_count<={:.0f}"),
        "max_complexity": ("b03", "avg_complexity<={:.0f}"),
        "coupling_limit": ("b04", "complex_ratio<={:.4f}"),
    }

    def adopt_params(self, params: dict) -> None:
        """把优化器候选参数映射进题集判定阈值（b01-b04）。

        这是 benchmark 模块 docstring 声称的「参数变化 → 行为分变化」
        可测通道的实际接线（2026-09-17）：此前阈值硬编码，_apply_params
        只写 config.yaml，分数与参数零耦合 → daemon P0 门禁恒等分死门。
        max_complexity 额外驱动 _scan 的「复杂方法」判定线（原硬编码 15）。
        """
        for key, value in params.items():
            mapping = self._PARAM_TO_CHECK.get(key)
            if mapping is not None:
                check_id, tmpl = mapping
                for chk in self._checks:
                    if chk.get("id") == check_id:
                        chk["check"] = tmpl.format(float(value))
                        break
            if key == "max_complexity":
                self._complex_threshold = float(value)

    def run(self) -> BenchmarkResult:
        m = self._scan()
        details: dict[str, bool] = {}
        for chk in self._checks:
            cid, expr = chk["id"], chk["check"]
            # 表达式形如 "metric<=N" / "metric==N"
            for op in ("<=", "==", ">="):
                if op in expr:
                    key, _, rhs = expr.partition(op)
                    val = m.get(key.strip())
                    limit = float(rhs)
                    if val is None:
                        details[cid] = False
                    elif op == "<=":
                        details[cid] = val <= limit
                    elif op == ">=":
                        details[cid] = val >= limit
                    else:
                        details[cid] = val == limit
                    break
            else:
                details[cid] = False
        passed = sum(1 for v in details.values() if v)
        result = BenchmarkResult(
            score=round(passed / len(self._checks) * 100, 1),
            passed=passed,
            total=len(self._checks),
            details=details,
        )
        self._last_result = result
        return result

    @property
    def last_score(self) -> float | None:
        return self._last_result.score if self._last_result else None


class StructureEvaluatorLike:
    """与 self_optimizer.evaluator.StructureEvaluator 共享的常量/复杂度函数。

    不直接 import 以避免环形依赖；_SKIP_DIRS 与 complexity 逻辑保持一致
    （单一事实源原则：如需修改请同步 evaluator.py）。
    """

    _SKIP_DIRS = frozenset({
        ".git", ".cache", "__pycache__", "node_modules", "venv",
        ".venv", ".npm-global", ".tox", ".mypy_cache", ".ruff_cache",
        "site-packages", "dist", "build", "egg-info",
        "bench", ".ling-audit", "archive", ".audit",
    })

    @staticmethod
    def complexity(func_node: ast.FunctionDef) -> int:
        complexity = 1
        for node in ast.walk(func_node):
            if isinstance(node, (ast.If, ast.While, ast.For, ast.ExceptHandler)):
                complexity += 1
            elif isinstance(node, ast.BoolOp):
                complexity += len(node.values) - 1
        return complexity
