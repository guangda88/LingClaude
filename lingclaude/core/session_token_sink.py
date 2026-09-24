#!/usr/bin/env python3
"""Session 维度 LLM token 落盘 — token schema 盲区清偿（arch_review D3）。

背景（2026-09-23 review-20260923-jev-laya-24h-consumption.json D3）：
- 仓内 117,421 sessions 中 0 含 token 字段；snapshot input/output_tokens 纯占位 0；
  真实 token 数据只活在 TokenMonitor 的 SQLite 聚合层（token_monitor.db），
  session 维度 JSON 从不落盘 → 跨期 token 对比只能靠 commit body 估算。
- 原计划挂 task_scheduler.total_tokens_used 加 StateStore 落盘；经调用链核查
  TaskScheduler.mark_completed 全仓无生产调用点（仅演示 main + 单测），是死接线。
  正确清偿点 = 回合循环真实数据流：query_engine_turn_mixin._finalize_turn
  → TokenMonitor.record_usage → legacy_sink 链。

设计（与 lingmemory_token_bridge 旁路纪律同款）：
- best-effort：写失败只 warning，绝不影响主流程
- 开关：LINGCLAUDE_SESSION_TOKEN_SINK=off 关闭，默认开
- 路径：~/.lingclaude/state/session_token_usage/{session_id}/{round_idx}.json
  （复用 StateStore._atomic_write_json 原子写 + JsonFileBackend 目录约定）
- schema（memory 盲区修复要求的最小集）：
  session_id / round_idx / input_tokens / output_tokens / cached_tokens /
  total_tokens / model / provider / task_type / created_at
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lingclaude.core.token_pricing import compute_cost_usd

logger = logging.getLogger(__name__)

_ENV_TOGGLE = "LINGCLAUDE_SESSION_TOKEN_SINK"
_RECORD_TYPE = "session_token_usage"


class SessionTokenSink:
    """TokenMonitor 旁路 sink：session/轮次维度真实 token 落盘。

    事件接口（鸭子类型，与 LingMemoryTokenSink 同款，经 legacy_sink 注入）：
    - on_usage(usage_dict)：单次用量 → 一条 JSON 记录
    session_id / round_idx / provider 由 metadata 注入（QueryEngine 侧携带），
    缺失时 fail-soft 跳过（无 session 上下文的调用不落脏数据）。
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = root  # 测试隔离注入；None → JsonFileBackend 默认 ~/.lingclaude/state

    def on_usage(self, usage: dict[str, Any]) -> None:
        try:
            # off 只管默认生产路径；显式 root（测试隔离注入 tmp_path）不受开关影响
            # （2026-09-24 修正：原实现 off 短路在 root 分流前，误伤显式注入的隔离测试）
            if (
                self._root is None
                and os.environ.get(_ENV_TOGGLE, "").strip().lower() == "off"
            ):
                return
            meta = usage.get("metadata") or {}
            session_id = str(meta.get("session_id") or "").strip()
            if not session_id:
                return  # 无 session 上下文（如单测/后台轮询）不落盘
            round_idx = max(0, int(meta.get("round_idx") or 0))
            model = usage.get("model") or "unknown"
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            cached_tokens = int(meta.get("cached_tokens") or 0)
            # cost 字段（token-schema-legacy-path-ingest 清偿②, 2026-09-24）:
            # 与老路径 session_history 同 schema; 单价表外置, 未定价 → unpriced
            cost = compute_cost_usd(model, input_tokens, output_tokens, cached_tokens)
            payload = {
                "session_id": session_id,
                "round_idx": round_idx,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_tokens": cached_tokens,
                "total_tokens": int(usage.get("total_tokens") or 0),
                "model": model,
                "provider": meta.get("provider") or "unknown",
                "task_type": usage.get("task_type") or "unknown",
                "cost_usd": None if cost is None else round(cost, 6),
                "cost_status": "unpriced" if cost is None else "priced",
                # legacy 链会重建 payload 丢顶层 timestamp：metadata 优先，兜底 now()
                "created_at": (
                    meta.get("timestamp")
                    or usage.get("timestamp")
                    or datetime.now(timezone.utc).isoformat()
                ),
            }
            self._write(session_id, round_idx, payload)
        except Exception as e:  # noqa: BLE001 — 旁路纪律：永不破坏主流程
            logger.warning("[session_token_sink] 落盘失败（已忽略）: %s", e)

    def _write(self, session_id: str, round_idx: int, payload: dict[str, Any]) -> None:
        from lingclaude.core.state_store import StateStore

        key = f"{session_id}/{round_idx:06d}"
        if self._root is not None:
            StateStore(root=self._root).save(_RECORD_TYPE, key, payload, root=self._root)
        else:
            StateStore().save(_RECORD_TYPE, key, payload)


def record_turn_usage(
    monitor: Any,
    session_id: str,
    round_idx: int,
    model: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    task_type: str = "turn",
) -> None:
    """回合循环便捷入口：组装 metadata 后走 monitor.record_usage。

    经同一 legacy_sink 链分发（灵忆镜像 + session 落盘），避免双路写。
    """
    monitor.record_usage(
        model=model,
        task_type=task_type,
        total_tokens=input_tokens + output_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        metadata={
            "session_id": session_id,
            "round_idx": round_idx,
            "provider": provider,
            "cached_tokens": cached_tokens,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )
