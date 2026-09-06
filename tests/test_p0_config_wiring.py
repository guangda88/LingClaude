"""P0 两项死接线修复的回归测试。"""
from __future__ import annotations

from lingclaude.core.config import PermissionConfig, lingclaudeConfig
from lingclaude.core.model_call import AGENT_MAX_TOOL_ROUNDS, _resolve_max_tool_rounds
from lingclaude.core.query_engine import QueryEngineConfig


class TestPermissionModeWiring:
    def test_from_dict_loads_mode(self) -> None:
        cfg = lingclaudeConfig.from_dict({"permissions": {"mode": "strict"}})
        assert cfg.permissions.mode == "strict"

    def test_default_ask_when_missing(self) -> None:
        cfg = lingclaudeConfig.from_dict({})
        assert cfg.permissions.mode == "ask"

    def test_dataclass_field_exists(self) -> None:
        assert PermissionConfig().mode == "ask"


class TestMaxToolRounds:
    def test_query_engine_config_has_field(self) -> None:
        assert QueryEngineConfig().max_turns == 8  # 默认兜底存在

    def test_resolver_follows_config_max_turns(self) -> None:
        class Cfg:
            max_turns = 16

        class Engine:
            config = Cfg()

        assert _resolve_max_tool_rounds(Engine()) == 16

    def test_resolver_falls_back_to_constant(self) -> None:
        class Engine:
            pass

        assert _resolve_max_tool_rounds(Engine()) == AGENT_MAX_TOOL_ROUNDS
