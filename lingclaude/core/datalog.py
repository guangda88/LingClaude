"""P0.9 灵克 datalog 适配 — 将 L5/T0/T3 事件输出到统一格式

输出: ~/.lingclaude/datalog/YYYY-MM-DD.jsonl
格式: 与 P0.8 schema 对齐 (docs/datalog_schema_samples.md)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

# 2026-09-16 启动提速：FactCheckResult 仅作类型标注——急切 import 会把
# fact_checker→fastapi/retrieval 链（≈600ms）拖进每次启动。改 TYPE_CHECKING。
if TYPE_CHECKING:  # pragma: no cover
    from lingclaude.core.fact_checker import FactCheckResult

logger = logging.getLogger(__name__)

DATALOG_DIR = Path.home() / ".lingclaude" / "datalog"


def _ensure_datalog_dir() -> Path:
    """确保 datalog 目录存在"""
    today = time.strftime("%Y-%m-%d")
    path = DATALOG_DIR / f"{today}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_event(event: dict[str, Any]) -> None:
    """写入一条 datalog 事件 (append-only JSONL)"""
    path = _ensure_datalog_dir()
    event["timestamp"] = event.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def log_l5_audit(
    session_id: str,
    l5_session_id: str,
    l5_round: int,
    consistency_score: float,
    should_early_exit: bool,
    should_fix: bool,
    t1_result: dict[str, Any] | None = None,
    t3_result: dict[str, Any] | None = None,
    model: str = "",
    latency_ms: float = 0,
) -> None:
    """记录 L5 审计事件"""
    event = {
        "event_type": "l5.audit.round",
        "member": "lingclaude",
        "session_id": session_id,
        "l5_session_id": l5_session_id,
        "l5_round": l5_round,
        "consistency_score": round(consistency_score, 3),
        "should_early_exit": should_early_exit,
        "should_fix": should_fix,
        "t1_fact_check": t1_result,
        "t3_entity_conflict": t3_result,
        "model": model,
        "latency_ms": round(latency_ms, 1),
    }
    _write_event(event)


def log_t0_behavior(
    session_id: str,
    checks: dict[str, str],
    nudges: list[str],
    should_block: bool,
) -> None:
    """记录 T0 行为校验事件"""
    event = {
        "event_type": "t0.behavior.check",
        "member": "lingclaude",
        "session_id": session_id,
        "checks": checks,
        "nudges": nudges,
        "should_block": should_block,
    }
    _write_event(event)


def log_degradation_alert(
    session_id: str,
    signal: str,
    severity: str,
    detail: str,
    msg_index: int,
) -> None:
    """记录工具调用退化事件"""
    event = {
        "event_type": "tool.degradation.alert",
        "member": "lingclaude",
        "session_id": session_id,
        "signal": signal,
        "severity": severity,
        "detail": detail,
        "msg_index": msg_index,
    }
    _write_event(event)


def log_model_call(
    model: str,
    input_tokens: int,
    output_tokens: int,
    finish_reason: str,
    latency_ms: float,
    path: str = "stream",
) -> None:
    """记录一次模型调用（stream/complete 统一埋点）。

    atomcode#1 (P0): datalog cost 字段此前从未被填充，Token 开销
    无法量化。usage 由 openai 协议 response.usage 现成返回，本函数
    只做纯接缝写入：事件落 JSONL，异常静默（不干扰主链路）。
    """
    event = {
        "event_type": "model.call",
        "member": "lingclaude",
        "model": model,
        "cost": {
            "in": int(input_tokens),
            "out": int(output_tokens),
        },
        "finish_reason": finish_reason,
        "latency_ms": round(latency_ms, 1),
        "path": path,
    }
    try:
        _write_event(event)
    except Exception as e:  # noqa: BLE001 — 遥测绝不反噬主链路
        logger.warning("datalog log_model_call failed: %s", e)
