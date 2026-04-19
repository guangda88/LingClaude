from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


DEFAULT_CONFIG_PATH = Path("config.yaml")

_CONFIG_SEARCH_PATHS: list[Path] = [
    Path("config.yaml"),
    Path.home() / ".lingclaude" / "config.yaml",
    Path(__file__).resolve().parent.parent.parent / "config.yaml",
]




@dataclass(frozen=True)
class EngineConfig:
    max_turns: int = 8
    max_budget_tokens: int = 200000
    compact_after_turns: int = 12
    structured_output: bool = False
    bash_executor_type: str = "native"  # "native" or "lingxi"


@dataclass(frozen=True)
class PermissionConfig:
    deny_tools: list[str] = field(default_factory=list)
    deny_prefixes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TriggerConfig:
    enabled: bool = True
    review_score_threshold: int = 60
    max_complexity: int = 15
    max_class_lines: int = 300
    max_method_lines: int = 50
    max_execution_time: float = 30.0
    new_lines_threshold: int = 500
    min_interval_hours: int = 24
    hallucination_threshold: float = 0.3
    frustration_threshold: float = 0.2
    tool_error_threshold: float = 0.3
    correction_threshold: int = 2


@dataclass(frozen=True)
class OptimizerConfig:
    goal: str = "structure"
    max_trials: int = 50
    timeout_seconds: int = 120


@dataclass(frozen=True)
class SessionConfig:
    save_dir: str = ".lingclaude/sessions/"
    max_history: int = 100


@dataclass(frozen=True)
class ModelProviderConfig:
    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.7
    system_prompt: str = "你是灵克，一个 AI 编程助手。"


@dataclass(frozen=True)
class ModelRouterConfig:
    code_model: str = ""
    chat_model: str = ""
    enabled: bool = False


@dataclass(frozen=True)
class IntelConfig:
    enabled: bool = True
    output_dir: str = ".lingclaude/intel/"
    session_history_path: str = "data/session_history.json"
    auto_collect_behavior: bool = True
    auto_relay: bool = True
    relay_target: str = "lingyi"
    digest_hour: int = 23


@dataclass(frozen=True)
class VerificationConfig:
    enabled: bool = True
    syntax_check: bool = True
    test_run: bool = False
    test_command: str = "python3 -m pytest {test_path} -x -q --tb=short"
    blocked_extensions: tuple[str, ...] = (".py",)
    allowed_write_roots: tuple[str, ...] = ()
    max_tool_calls_per_session: int = 0


@dataclass(frozen=True)
class LingClaudeConfig:
    engine: EngineConfig = field(default_factory=EngineConfig)
    permissions: PermissionConfig = field(default_factory=PermissionConfig)
    triggers: TriggerConfig = field(default_factory=TriggerConfig)
    optimizer: OptimizerConfig = field(default_factory=OptimizerConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    model: ModelProviderConfig = field(default_factory=ModelProviderConfig)
    model_router: ModelRouterConfig = field(default_factory=ModelRouterConfig)
    intel: IntelConfig = field(default_factory=IntelConfig)
    verification: VerificationConfig = field(default_factory=VerificationConfig)
    log_level: str = "INFO"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LingClaudeConfig:
        engine_raw = raw.get("engine", {})
        perm_raw = raw.get("permissions", {})
        trig_raw = raw.get("self_optimizer", {}).get("triggers", {})
        opt_raw = raw.get("self_optimizer", {}).get("optimization", {})
        sess_raw = raw.get("session", {})
        model_raw = raw.get("model", {})
        router_raw = raw.get("model_router", {})

        intel_raw = raw.get("intel", {})
        ver_raw = raw.get("verification", {})

        return cls(
            engine=EngineConfig(
                max_turns=engine_raw.get("max_turns", 8),
                max_budget_tokens=engine_raw.get("max_budget_tokens", 200000),
                compact_after_turns=engine_raw.get("compact_after_turns", 12),
                structured_output=engine_raw.get("structured_output", False),
                bash_executor_type=engine_raw.get("bash_executor_type", "native"),
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
                hallucination_threshold=trig_raw.get("hallucination_threshold", 0.3),
                frustration_threshold=trig_raw.get("frustration_threshold", 0.2),
                tool_error_threshold=trig_raw.get("tool_error_threshold", 0.3),
                correction_threshold=trig_raw.get("correction_threshold", 2),
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
            model=ModelProviderConfig(
                provider=model_raw.get("provider", "openai"),
                model=model_raw.get("model", "gpt-4o"),
                api_key=model_raw.get("api_key", ""),
                base_url=model_raw.get("base_url"),
                max_tokens=model_raw.get("max_tokens", 4096),
                temperature=model_raw.get("temperature", 0.7),
                system_prompt=model_raw.get("system_prompt", "你是灵克，一个 AI 编程助手。"),
            ),
            model_router=ModelRouterConfig(
                code_model=router_raw.get("code_model", ""),
                chat_model=router_raw.get("chat_model", ""),
                enabled=router_raw.get("enabled", False),
            ),
            intel=IntelConfig(
                enabled=intel_raw.get("enabled", True),
                output_dir=intel_raw.get("output_dir", ".lingclaude/intel/"),
                session_history_path=intel_raw.get("session_history_path", "data/session_history.json"),
                auto_collect_behavior=intel_raw.get("auto_collect_behavior", True),
                auto_relay=intel_raw.get("auto_relay", True),
                relay_target=intel_raw.get("relay_target", "lingyi"),
                digest_hour=intel_raw.get("digest_hour", 23),
            ),
            verification=VerificationConfig(
                enabled=ver_raw.get("enabled", True),
                syntax_check=ver_raw.get("syntax_check", True),
                test_run=ver_raw.get("test_run", False),
                test_command=ver_raw.get("test_command", "python3 -m pytest {test_path} -x -q --tb=short"),
                blocked_extensions=tuple(ver_raw.get("blocked_extensions", [".py"])),
                allowed_write_roots=tuple(ver_raw.get("allowed_write_roots", [])),
                max_tool_calls_per_session=ver_raw.get("max_tool_calls_per_session", 0),
            ),
            log_level=raw.get("system", {}).get("log_level", "INFO"),
        )


def find_config_path() -> Path | None:
    for p in _CONFIG_SEARCH_PATHS:
        if p.exists():
            return p
    return None


def load_config(path: Path | None = None) -> LingClaudeConfig:
    if path is not None:
        target = path
    else:
        target = find_config_path() or DEFAULT_CONFIG_PATH
    if target.exists():
        raw = yaml.safe_load(target.read_text())
        if raw is None:
            return LingClaudeConfig()
        return LingClaudeConfig.from_dict(raw)
    return LingClaudeConfig()
