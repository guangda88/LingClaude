"""Q4 (2026-09-14): QueryEngine 生命周期/持久化职责拆分（行为零变化）。

从 lingclaude/core/query_engine.py 机械搬迁（AST 提取原方法体，仅缩进调整）。
消费方（QueryEngine）以多继承组合本 mixin；对外 API 面不变。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lingclaude.core.intel import DailyDigest, DailyDigestGenerator
from lingclaude.core.models import PermissionDenial
from lingclaude.core.redact import redact as _redact_text
from lingclaude.core.token_pricing import compute_cost_usd as _compute_cost_usd
from lingclaude.core.types import Result

logger = logging.getLogger(__name__)

class QueryEngineLifecycleMixin:
        def collect_daily_digest(self, report_date: str | None = None) -> Result[DailyDigest]:
            items = self._intel_collector.collect_all()
            digest = DailyDigestGenerator.generate(items, report_date)
            if self._intel_relay is not None:
                relay_result = self._intel_relay.relay(digest)
                if relay_result.is_error:
                    return relay_result  # type: ignore[return-value]
            self._intel_collector.clear()
            return Result.ok(digest)

        def set_session_history_path(self, path: Path) -> None:
            self._session_history_path = path

        # ------------------------------------------------------------------
        # token schema 老路径接入（token-schema-legacy-path-ingest 清偿①②，
        # 2026-09-24）。值源 = _finalize_turn 缓存的本轮单轮值（_last_turn_*，
        # 语义见 query_engine_turn_mixin：负值 = provider 未回传的估算哨兵），
        # 老路径不重复采样，与 sink 链同源避免双轨口径。
        # ------------------------------------------------------------------
        def _token_usage_fields(self) -> dict[str, Any]:
            """session_history 老路径记录的 token/cost 字段组装。

            cost 口径假设：cached_tokens ⊆ input_tokens（OpenAI 系口径），
            非缓存 input = input_tokens - cached_tokens；单价表外置
            （LINGCLAUDE_TOKEN_PRICE_TABLE → JSON：{模型子串: {input, output,
            cached?}}，单位 USD / 1M tokens，命中最长 key）。未配表 / 未登记
            模型 → cost_usd=None + cost_status="unpriced"（绝不估算、绝不编价）。
            """
            fields: dict[str, Any] = {
                "input_tokens": int(getattr(self, "_last_turn_input", 0) or 0),
                "output_tokens": int(getattr(self, "_last_turn_output", 0) or 0),
                "cached_tokens": int(getattr(self, "_last_turn_cached", 0) or 0),
                "model": getattr(self, "_last_model", None) or "unknown",
            }
            cost = _compute_cost_usd(
                fields["model"],
                fields["input_tokens"],
                fields["output_tokens"],
                fields["cached_tokens"],
            )
            if cost is None:
                fields["cost_usd"] = None
                fields["cost_status"] = "unpriced"
            else:
                fields["cost_usd"] = round(cost, 6)
                fields["cost_status"] = "priced"
            return fields

        def _append_to_session_history(self, query: str, response: str) -> None:
            try:
                self._session_history_path.parent.mkdir(parents=True, exist_ok=True)
                history: list[dict[str, str]] = []
                if self._session_history_path.exists():
                    try:
                        raw = json.loads(self._session_history_path.read_text(encoding="utf-8"))
                        if isinstance(raw, list):
                            history = raw
                    except (json.JSONDecodeError, ValueError):
                        logger.warning("Session history corrupted, starting fresh")
                        history = []
                history.append({
                    "query": _redact_text(query[:200]),
                    "title": _redact_text(query[:80]),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "session_id": self.session_id,
                    **self._token_usage_fields(),
                })
                # 原子写入：先写临时文件，再rename，防止进程中断导致损坏
                import tempfile
                fd, tmp_path = tempfile.mkstemp(
                    dir=str(self._session_history_path.parent),
                    suffix=".tmp",
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        json.dump(history, f, ensure_ascii=False, indent=2)
                    os.replace(tmp_path, str(self._session_history_path))
                except Exception:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
            except Exception as e:
                logger.warning("Session history write failed: %s", e)

        def _learn_from_turn(self, prompt: str, response: str) -> None:
            from lingclaude.core.turn_learner import record_turn_learnings
            record_turn_learnings(
                prompt=prompt,
                behavior=self._behavior,
                messages=self._messages,
                session_id=self.session_id,
                response=response,
            )

        def _log_to_flywheel(
            self,
            pattern_type: str,
            error_message: str,
            tool_name: str = "",
            file_path: str = "",
            context: str = "",
        ) -> None:
            from lingclaude.core.data_flywheel import DataFlywheel  # noqa: F401 — 接线验证
            from lingclaude.core.state_store import StateStore  # noqa: F401 — 接线验证
            self._session_runtime.log_to_flywheel(pattern_type, error_message, tool_name, file_path, context)

        def _session_state_path(self) -> Path:
            return self._session_runtime.session_state_path()

        def _save_session_state(self) -> None:
            self._session_runtime.save_session_state()

        def _load_session_state(self) -> None:
            self._session_runtime.load_session_state()

        def _check_optimization_triggers(self) -> None:
            self._session_runtime.check_optimization_triggers()

        def _collect_behavior_intel(self) -> None:
            self._session_runtime.collect_behavior_intel()

        def _index_project(self) -> dict[str, Any]:
            return self._session_runtime.index_project()

        def _format_output(self, lines: list[str]) -> str:
            return self._session_runtime.format_output(lines)
