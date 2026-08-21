"""会话运行时辅助 — flywheel/会话状态/优化触发/项目索引/格式化。

从 query_engine.py 拆出, 封装为 SessionRuntime 类, QueryEngine 保留薄委托。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SessionRuntime:
    """Encapsulates session runtime logic extracted from QueryEngine."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def log_to_flywheel(
        self,
        pattern_type: str,
        error_message: str,
        tool_name: str = "",
        file_path: str = "",
        context: str = "",
    ) -> None:
        try:
            from lingclaude.core.data_flywheel import DataFlywheel, ErrorPattern

            flywheel = DataFlywheel()
            flywheel.log_error(ErrorPattern(
                pattern_type=pattern_type,
                file_path=file_path,
                error_message=error_message[:500],
                tool_name=tool_name,
                context=context[:200],
                session_id=self._engine.session_id,
                occurred_at=datetime.now().isoformat(),
            ))
            flywheel.close()
        except Exception as e:
            logger.warning("飞轮记录失败: %s", e)

    def session_state_path(self) -> Path:
        return Path.home() / ".lingclaude" / "session_state.json"

    def save_session_state(self) -> None:
        engine = self._engine
        try:
            state: dict[str, Any] = {
                "behavior": engine._behavior.to_dict(),
                "calibrator_records": {},
                "total_messages_sent": engine._total_messages_sent,
                "l1_last_triggered_at": engine._l1_last_triggered_at,
            }
            for domain, rec in engine._meta_cognition._calibrator.records.items():
                state["calibrator_records"][domain] = {
                    "correct": rec.correct,
                    "incorrect": rec.incorrect,
                    "last_error": rec.last_error,
                    "last_error_time": rec.last_error_time,
                }
            blind_spots = engine._meta_cognition._blind_spot_detector.error_patterns
            state["blind_spot_patterns"] = {k: v for k, v in blind_spots.items()}
            path = self.session_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("会话状态保存失败: %s", e)

    def load_session_state(self) -> None:
        engine = self._engine
        try:
            path = self.session_state_path()
            if not path.exists():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            from lingclaude.core.meta_cognition import _DomainRecord
            if "calibrator_records" in data:
                for domain, rec_data in data["calibrator_records"].items():
                    engine._meta_cognition._calibrator.records[domain] = _DomainRecord(
                        correct=rec_data.get("correct", 0),
                        incorrect=rec_data.get("incorrect", 0),
                        last_error=rec_data.get("last_error", ""),
                        last_error_time=rec_data.get("last_error_time", ""),
                    )
            if "blind_spot_patterns" in data:
                for domain, count in data["blind_spot_patterns"].items():
                    engine._meta_cognition._blind_spot_detector.error_patterns[domain] = count
            engine._total_messages_sent = data.get("total_messages_sent", 0)
            engine._l1_last_triggered_at = data.get("l1_last_triggered_at", -1)
        except Exception as e:
            logger.warning("会话状态加载失败: %s", e)

    def check_optimization_triggers(self) -> None:
        engine = self._engine
        try:
            from lingclaude.self_optimizer.trigger import OptimizationTrigger
            trigger = OptimizationTrigger()
            context = {
                "behavior_metrics": engine._behavior.to_dict(),
                "hallucination_risk": engine._behavior.hallucination_risk,
                "tool_error_rate": engine._behavior.tool_error_rate,
                "frustration_rate": engine._behavior.frustration_rate,
                "review_score": int((1.0 - engine._behavior.hallucination_risk) * 100),
            }
            triggered, info = trigger.check_all_conditions(context)
            if triggered and info is not None:
                logger.info("自优化触发: %s (priority=%s)", info.reason, info.priority)
                engine.notify_risk(
                    "自优化触发",
                    f"触发原因: {info.reason}, 优先级: {info.priority}",
                )
        except Exception as e:
            logger.warning("优化触发检查失败: %s", e)

    def collect_behavior_intel(self) -> None:
        self._engine._intel_collector.from_behavior(self._engine._behavior.to_dict())

    def index_project(self) -> dict[str, Any]:
        engine = self._engine
        if engine._runtime is None:
            return {}
        if engine._project_index:
            return engine._project_index
        try:
            result = engine._runtime.execute_tool("glob", pattern="**/*.py")
            if not isinstance(result, dict) or "files" not in result:
                return {}
            files = result.get("files", [])
            if not files:
                return {}
            structure: dict[str, list[str]] = {}
            for f in files:
                parts = Path(f).parts
                if len(parts) > 1:
                    pkg = parts[0]
                    structure.setdefault(pkg, []).append("/".join(parts[1:]))
                else:
                    structure.setdefault(".").append(parts[0])
            engine._project_index = structure
            return structure
        except Exception:
            return {}

    def format_output(self, lines: list[str]) -> str:
        engine = self._engine
        if engine.config.structured_output:
            return json.dumps({"summary": lines, "session_id": engine.session_id}, indent=2, ensure_ascii=False)
        return "\n".join(lines)
