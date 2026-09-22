"""P1-1（2026-09-22）：Laya fast lane——本地路由模型插片（CPU，multilingual checkpoint）。

职责：部门路由/紧急度量等 System-1 快速判定，替代一轮 LLM 路由调用
（典型 1-5s 网络往返 vs 本地 multilingual 151ms @2线程，实测
benchmarks/laya_cpu_bench_v3_results.json）。**不写进 SLA**（铁律 §四：
实测数字仅作排期依据）。

设计（契约 §二/§四 边界）：
- 独立插片，不改 task_router 本体——fast lane 失效时 resolve 走原路径（fail-soft）
- torch.set_num_threads(4) 钉线程（8 线程实测反而 2-2.5× 退化，见 v3 复测）
- 进程内单例懒加载（HF_HUB_OFFLINE=1 走本地缓存，避免每轮联网校验）
- NOT_GOOD_AT 语义对齐：Laya 不擅长的（长文/代码理解/创意生成）显式回退 LLM
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# 实测甜点（v3 复测）：multilingual 322M @2线程 151ms 单题中位；
# 8 线程 2-2.5× 退化 → 钉 4 线程（多问并行批时有余量）。
_LAYA_THREADS = 4
_LAYA_MODEL = "convaiinnovations/laya"
_LAYA_SUBFOLDER = "multilingual"

# Laya NOT_GOOD_AT（Jev 式不擅长清单，显式声明）：命中即回退 LLM 路由。
# Laya 是部门路由/分类器，长文理解/代码语义/创意生成不在其训练分布内。
NOT_GOOD_AT_PATTERNS = ("```", "def ", "class ", "diff --git")

# B2（2026-09-22 质量验证裁定）：confidence 门控 + 薄弱域回退。
# 依据 benchmarks/laya_fastlane_quality.py 逐条明细：
# - confidence < 0.3 的 2 条判定（factual_lookup→code、data_analysis）全部误判，
#   故低置信判定一律回退 LLM（gate 0.3 对齐质量验证结论，不写 SLA）。
# - factual_lookup 域是 Laya 最薄弱域（3 条里 2 条偏到别处），无论 confidence
#   多高，判定结果为 factual_lookup 时直接回退——该类查询本就应走 LLM 事实检索。
#
# B2-R（2026-09-22 放宽试点，fast lane 转正收益验证）：门控参数从硬编码
# 上移到策略文件 fan_out_questions.yaml defaults（hot-update，m-time watch）：
#   min_confidence（0.3→0.1 试点）、weak_domains（factual_lookup 保留回退）、
#   speculative_min_difficulty（1.5→0.5，FanOut pre-classifier 消费）。
# 读失败回退下述内置默认（与原硬编码值一致——零行为分叉兜底）。
_MIN_CONFIDENCE = 0.1          # B2-R：放宽试点（内置兜底值）
_WEAK_DOMAINS = ("factual_lookup",)

# B2-R：策略文件门控参数（hot-update；读取失败用内置默认）
_B2_GATE_KEYS = ("fast_lane_min_confidence", "fast_lane_weak_domains",
                 "fan_out_speculative_min_difficulty")
_b2_gate_cache: dict[str, Any] = {"ts": 0.0, "values": None}


def _b2_gate_params() -> dict[str, Any]:
    """策略文件门控参数（mtime watch + 30s 节流；读失败→内置默认，不崩）。"""
    now = time.monotonic()
    if _b2_gate_cache["values"] is not None and now - _b2_gate_cache["ts"] < 30:
        return _b2_gate_cache["values"]
    vals: dict[str, Any] = {}
    try:
        from lingclaude.core.policy_loader import get as policy_get
        data = policy_get("fan_out_questions") or {}
        defaults = data.get("defaults") or {}
        for key in _B2_GATE_KEYS:
            if key in defaults:
                vals[key] = defaults[key]
    except Exception:
        logger.debug("fast lane 门控参数读取失败（用内置默认）", exc_info=True)
    _b2_gate_cache.update(ts=now, values=vals)
    return vals


_laya_agent: Any = None  # 进程内单例
_laya_failed: bool = False  # 探测失败后不再重试（fail-soft 收敛）


def _get_agent() -> Any | None:
    """懒加载 Laya agent（单例；失败置位不再重试，回退 LLM 路由）。"""
    global _laya_agent, _laya_failed
    if _laya_agent is not None or _laya_failed:
        return _laya_agent
    try:
        import torch
        torch.set_num_threads(_LAYA_THREADS)
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        from laya import load
        _laya_agent = load(_LAYA_MODEL, device="cpu", subfolder=_LAYA_SUBFOLDER)
        logger.info("Laya fast lane 就绪: %s (%s) threads=%d", _LAYA_MODEL, _LAYA_SUBFOLDER, _LAYA_THREADS)
    except Exception as e:  # noqa: BLE001 — fast lane 不可用即回退，不阻塞主链
        logger.warning("Laya fast lane 不可用（回退 LLM 路由）: %s", e)
        _laya_failed = True
    return _laya_agent


def _load_questions() -> dict[str, dict[str, Any]] | None:
    """P1-4 data-driven：读 policies/fan_out_questions.yaml（热更，改文件不改代码）。

    读失败/键缺失回退内置 preset（graceful degrade，模式同 router_keywords）。
    """
    try:
        from lingclaude.core.policy_loader import get as policy_get
        data = policy_get("fan_out_questions")
        fq = (data or {}).get("fast_lane_questions")
        if isinstance(fq, dict) and fq.get("$ref") == "router_questions":
            from laya.presets import router_questions
            return router_questions()
        if isinstance(fq, dict) and fq:
            return fq
    except Exception:
        logger.debug("fan_out_questions.yaml 读取失败，回退内置 preset", exc_info=True)
    try:
        from laya.presets import triage_questions
        return triage_questions()
    except Exception:
        return None


def _gate_verdict(result: dict[str, Any]) -> dict[str, Any] | None:
    """B2/B2-R：对 Laya 判定结果做 confidence 门控 + 薄弱域回退。

    规则（质量验证裁定，不写 SLA；B2-R 阈值/薄弱域清单走策略文件热更）：
    - answers 缺失/非 dict → 无法门控，回退（返回 None）
    - domain 判定 confidence < min_confidence（默认 0.1，策略可热调）→ 回退
    - domain 判定结果命中 weak_domains（默认 factual_lookup）→ 回退（薄弱域）
    - 通过 → 返回原 result（调用方直接采信）
    """
    params = _b2_gate_params()
    min_conf = params.get("fast_lane_min_confidence", _MIN_CONFIDENCE)
    weak = params.get("fast_lane_weak_domains", _WEAK_DOMAINS)
    if not isinstance(min_conf, (int, float)):
        min_conf = _MIN_CONFIDENCE
    if isinstance(weak, (list, tuple)):
        weak = tuple(str(d).lower() for d in weak)
    elif isinstance(weak, str) and weak:
        weak = (weak.lower(),)
    else:
        weak = _WEAK_DOMAINS

    if not isinstance(result, dict):
        return None
    answers = result.get("answers")
    if not isinstance(answers, dict):
        return None
    dom_ans = answers.get("domain")
    if not isinstance(dom_ans, dict):
        return None
    confidence = dom_ans.get("confidence")
    if not isinstance(confidence, (int, float)):
        # confidence 缺失（异常输出形态）→ 保守回退，不冒险采信
        return None
    if confidence < min_conf:
        return None
    got_domain = str(dom_ans.get("choice", "")).lower()
    if got_domain in weak:
        return None
    return result


def fast_route(state: str, questions: dict[str, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Laya 快速路由判定（System-1，B2 门控版）。

    state: 用户输入/会话状态文本。
    questions: triage 问题集（None 时读 policies/fan_out_questions.yaml，
    读失败回退内置 preset）。
    返回经 confidence 门控 + 薄弱域回退后的 system_one 结果 dict；
    不可用 / NOT_GOOD_AT 命中 / 低置信 / factual_lookup 域 → None（调用方回退 LLM 路由）。
    """
    agent = _get_agent()
    if agent is None:
        return None
    # NOT_GOOD_AT 回退：代码/长文类不在训练分布内，显式让位 LLM
    if any(p in state for p in NOT_GOOD_AT_PATTERNS):
        return None
    try:
        if questions is None:
            questions = _load_questions()
            if not questions:
                return None
        result = agent.system_one(state[:2000], questions)  # 截断防长文拖累
        return _gate_verdict(result)
    except Exception:
        logger.debug("Laya fast_route failed (non-blocking)", exc_info=True)
        return None


def reset() -> None:
    """测试/热更辅助：清单例与失败标记。"""
    global _laya_agent, _laya_failed
    _laya_agent = None
    _laya_failed = False
