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


def fast_route(state: str, questions: dict[str, dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Laya 快速路由判定（System-1）。

    state: 用户输入/会话状态文本。
    questions: triage 问题集（None 时读 policies/fan_out_questions.yaml，
    读失败回退内置 preset）。
    返回 system_one 结果 dict；不可用/不擅长 → None（调用方回退 LLM 路由）。
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
        return agent.system_one(state[:2000], questions)  # 截断防长文拖累
    except Exception:
        logger.debug("Laya fast_route failed (non-blocking)", exc_info=True)
        return None


def reset() -> None:
    """测试/热更辅助：清单例与失败标记。"""
    global _laya_agent, _laya_failed
    _laya_agent = None
    _laya_failed = False
