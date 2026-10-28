"""P0 两项死接线修复的回归测试。"""
from __future__ import annotations

from lingclaude.core.config import PermissionConfig, lingclaudeConfig
from lingclaude.engine.loop.loop_body import AGENT_MAX_TOOL_ROUNDS, _resolve_max_tool_rounds
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


class TestModelAtSuffixStripping:
    """config.model 带 @provider 后缀会在 wire 层报 1211（2026-10-28 实锤）——启动时剥离+告警。"""

    def test_at_suffix_stripped_and_warned(self, caplog) -> None:
        import logging

        with caplog.at_level(logging.WARNING, logger="lingclaude.core.config"):
            cfg = lingclaudeConfig.from_dict(
                {"model": {"provider": "glm", "model": "glm-5.3-flash@glm"}}
            )
        assert cfg.model.model == "glm-5.3-flash"
        assert any("1211" in r.message for r in caplog.records)

    def test_bare_name_untouched(self) -> None:
        cfg = lingclaudeConfig.from_dict({"model": {"model": "glm-5.3-flash"}})
        assert cfg.model.model == "glm-5.3-flash"

    def test_router_selector_fields_keep_at_syntax(self) -> None:
        # model_router 的 code_model/chat_model 是路由串, @ 语法合法, 不得剥离
        cfg = lingclaudeConfig.from_dict(
            {"model_router": {"code_model": "glm-5.3-flash@glm", "enabled": True}}
        )
        assert cfg.model_router.code_model == "glm-5.3-flash@glm"
