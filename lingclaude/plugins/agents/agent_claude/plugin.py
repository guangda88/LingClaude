"""agent/claude 插片 — claude-code 外部 agent 桥（cli-subprocess 型，共享基类薄壳）。"""
from __future__ import annotations

from pathlib import Path

from lingclaude.plugins.agents.agent_cli_base import CliAgentPluginBase, make_register

MANIFEST_PATH = Path(__file__).parent / "manifest.agent.json"


class ClaudeAgentPlugin(CliAgentPluginBase):
    """agent/claude — claude -p 子进程桥（manifest 全驱动，无本地逻辑）。"""


register = make_register(ClaudeAgentPlugin, MANIFEST_PATH)
