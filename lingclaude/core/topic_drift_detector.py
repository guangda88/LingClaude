from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class TopicDriftStatus(Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class TopicDriftAlert:
    topic: str
    drift_percent: float
    active_topics: list[str]
    evidence_count: int
    total_calls: int
    detail: str
    msg_index: int


@dataclass
class TopicDriftDetector:
    window_size: int = 50
    drift_threshold: float = 0.7  # 70% of calls are off-topic
    handover_path: Path = Path.home() / ".lingclaude" / "handover.md"
    
    def __post_init__(self) -> None:
        self._active_topics: list[str] = []
        self._call_history: list[tuple[int, str, dict]] = []
        self._load_active_topics()
    
    def _load_active_topics(self) -> None:
        """Load active tasks from handover.md"""
        if not self.handover_path.exists():
            logger.warning("handover.md not found at %s", self.handover_path)
            return
        
        try:
            content = self.handover_path.read_text(encoding="utf-8")
            lines = content.split("\n")
            in_section = False
            for line in lines:
                if "## 当前用户任务" in line:
                    in_section = True
                    continue
                if in_section and line.startswith("##"):
                    break
                if in_section and line.strip().startswith("|") and "---" not in line:
                    parts = [p.strip() for p in line.split("|")[1:-1]]
                    if len(parts) >= 2 and parts[1] not in ("状态", ""):
                        status = parts[1] if len(parts) > 1 else ""
                        if status not in ("completed", "已完成"):
                            self._active_topics.append(parts[0])
            logger.info("Loaded active topics from handover: %s", self._active_topics)
        except Exception as e:
            logger.warning("Failed to load active topics: %s", e)
    
    def record_call(self, msg_index: int, tool_name: str, params: dict, output: str) -> None:
        self._call_history.append((msg_index, tool_name, params))
        if len(self._call_history) > self.window_size * 2:
            self._call_history = self._call_history[-self.window_size:]
    
    def detect_drift(self) -> list[TopicDriftAlert]:
        if len(self._call_history) < 10:
            return []
        
        if not self._active_topics:
            self._load_active_topics()
        
        if not self._active_topics:
            return []
        
        topic_counts: dict[str, int] = {}
        total = 0
        
        for msg_index, tool_name, params in self._call_history:
            topic = self._extract_topic(tool_name, params)
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
            total += 1
        
        off_topic_count = 0
        off_topic_topics: list[str] = []
        
        for topic, count in topic_counts.items():
            if not self._is_topic_active(topic):
                off_topic_count += count
                off_topic_topics.append(topic)
        
        drift_percent = off_topic_count / total if total > 0 else 0
        alerts: list[TopicDriftAlert] = []
        
        if drift_percent >= self.drift_threshold:
            detail = (
                f"{off_topic_count}/{total} ({drift_percent:.1%}) 的工具调用偏离了活跃任务"
                f"\n活跃任务: {self._active_topics}"
                f"\n偏离话题: {off_topic_topics}"
            )
            alerts.append(TopicDriftAlert(
                topic="topic_drift",
                drift_percent=drift_percent,
                active_topics=self._active_topics.copy(),
                evidence_count=off_topic_count,
                total_calls=total,
                detail=detail,
                msg_index=self._call_history[-1][0],
            ))
            logger.warning("Topic drift detected: %s", detail)
        
        return alerts
    
    def _extract_topic(self, tool_name: str, params: dict) -> str:
        """Extract topic from tool call parameters"""
        if tool_name in ("edit", "multiedit", "write"):
            file_path = params.get("file_path", params.get("path", ""))
            if file_path:
                return f"file:{Path(file_path).name}"
            return f"{tool_name}"
        
        if tool_name == "grep":
            pattern = params.get("pattern", params.get("query", ""))
            if pattern:
                return f"grep:{pattern[:30]}"
            return "grep"
        
        if tool_name == "bash":
            command = params.get("command", "")
            if command:
                return f"bash:{command.split()[0]}" if command.split() else "bash"
            return "bash"
        
        return tool_name
    
    def _is_topic_active(self, topic: str) -> bool:
        """Check if topic matches active tasks"""
        if not self._active_topics:
            return True
        
        topic_lower = topic.lower()
        for active in self._active_topics:
            active_lower = active.lower()
            if active_lower in topic_lower or topic_lower in active_lower:
                return True
        
        return False
    
    def update_active_topics(self, topics: list[str]) -> None:
        """Manually update active topics"""
        self._active_topics = topics
        logger.info("Updated active topics: %s", self._active_topics)
    
    def reset(self) -> None:
        self._call_history.clear()
        self._load_active_topics()


def extract_active_tasks_from_handover(handover_path: Path | None = None) -> list[str]:
    """Extract active tasks from handover.md"""
    if handover_path is None:
        handover_path = Path.home() / ".lingclaude" / "handover.md"
    
    if not handover_path.exists():
        return []
    
    try:
        content = handover_path.read_text(encoding="utf-8")
        lines = content.split("\n")
        in_section = False
        tasks: list[str] = []
        for line in lines:
            if "## 当前用户任务" in line:
                in_section = True
                continue
            if in_section and line.startswith("##"):
                break
            if in_section and line.strip().startswith("|") and "---" not in line:
                parts = [p.strip() for p in line.split("|")[1:-1]]
                if len(parts) >= 2 and parts[1] not in ("状态", ""):
                    status = parts[1] if len(parts) > 1 else ""
                    if status not in ("completed", "已完成"):
                        tasks.append(parts[0])
        return tasks
    except Exception as e:
        logger.warning("Failed to extract active tasks: %s", e)
        return []
