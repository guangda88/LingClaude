"""agent/atomcode 插片 — atomcode-cli 外部 agent 桥（plugin_forge 生成，cli-subprocess 薄壳）。"""
from __future__ import annotations

from pathlib import Path

from lingclaude.plugins.agents.agent_cli_base import CliAgentPluginBase, make_register

MANIFEST_PATH = Path(__file__).parent / "manifest.agent.json"


class AtomcodePlugin(CliAgentPluginBase):
    """agent/atomcode — -p {prompt} --ephemeral --output-format text 子进程桥（manifest 全驱动，无本地逻辑）。

    ⚠️ plugin_forge 生成（2026-09-28）：批3实战：forge 回路首个真实业务单（替代手工复制）
    """


register = make_register(AtomcodePlugin, MANIFEST_PATH)
