from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path("config.yaml")


@dataclass
class EngineConfig:
    max_turns: int = 8
    max_budget_tokens: int = 200000
    compact_after_turns: int = 12
    structured_output: bool = False


@dataclass
class PermissionConfig:
    deny_tools: list[str] = field(default_factory=list)
    deny_prefixes: list[str] = field(default_factory=list)


@dataclass
class TriggerConfig:
    enabled: bool = True
    review_score_threshold: int = 60
    max_complexity: int = 15
    max_class_lines: int = 300
    max_method_lines: int = 50
    max_execution_time: float = 30.0
    new_lines_threshold: int = 500
    min_interval_hours: int = 24


@dataclass
class OptimizerConfig:
    goal: str = "structure"
    max_trials: int = 50
    timeout_seconds: int = 120


@dataclass
class SessionConfig:
    save_dir: str = ".lingclaude/sessions/"
    max_history: int = 100


@dataclass
class LingClaudeConfig:
    engine: EngineConfig = field(default_factory=EngineConfig)
    permissions: PermissionConfig = field(default_factory=PermissionConfig)
    triggers: TriggerConfig = field(default_factory=TriggerConfig)
    optimizer: OptimizerConfig = field(default_factory=OptimizerConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    log_level: str = "INFO"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LingClaudeConfig:
        engine_raw = raw.get("engine", {})
        perm_raw = raw.get("permissions", {})
        trig_raw = raw.get("self_optimizer", {}).get("triggers", {})
        opt_raw = raw.get("self_optimizer", {}).get("optimization", {})
        sess_raw = raw.get("session", {})

        return cls(
            engine=EngineConfig(
                max_turns=engine_raw.get("max_turns", 8),
                max_budget_tokens=engine_raw.get("max_budget_tokens", 200000),
                compact_after_turns=engine_raw.get("compact_after_turns", 12),
                structured_output=engine_raw.get("structured_output", False),
            ),
            permissions=PermissionConfig(
                deny_tools=perm_raw.get("deny_tools", []),
                deny_prefixes=perm_raw.get("deny_prefixes", []),
            ),
            triggers=TriggerConfig(
                enabled=trig_raw.get("enabled", True),
                review_score_threshold=trig_raw.get("review_score_threshold", 60),
                max_complexity=trig_raw.get("max_complexity", 15),
                max_class_lines=trig_raw.get("max_class_lines", 300),
                max_method_lines=trig_raw.get("max_method_lines", 50),
                max_execution_time=trig_raw.get("max_execution_time", 30.0),
                new_lines_threshold=trig_raw.get("new_lines_threshold", 500),
                min_interval_hours=trig_raw.get("min_interval_hours", 24),
            ),
            optimizer=OptimizerConfig(
                goal=opt_raw.get("goal", "structure"),
                max_trials=opt_raw.get("max_trials", 50),
                timeout_seconds=opt_raw.get("timeout_seconds", 120),
            ),
            session=SessionConfig(
                save_dir=sess_raw.get("save_dir", ".lingclaude/sessions/"),
                max_history=sess_raw.get("max_history", 100),
            ),
            log_level=raw.get("system", {}).get("log_level", "INFO"),
        )


def load_config(path: Path | None = None) -> LingClaudeConfig:
    target = path or DEFAULT_CONFIG_PATH
    if target.exists():
        raw = yaml.safe_load(target.read_text())
        return LingClaudeConfig.from_dict(raw)
    return LingClaudeConfig()
