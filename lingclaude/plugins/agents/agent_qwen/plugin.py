"""agent/qwen 插片 — qwen-code 外部 agent 桥（cli-subprocess 型，共享基类薄壳）。"""
from __future__ import annotations

from pathlib import Path

from lingclaude.plugins.agents.agent_cli_base import CliAgentPluginBase, make_register

MANIFEST_PATH = Path(__file__).parent / "manifest.agent.json"


class QwenAgentPlugin(CliAgentPluginBase):
    """agent/qwen — qwen -p 子进程桥（manifest 全驱动，无本地逻辑）。

    ⚠️ 前置：qwen 需先完成 auth 配置（2026-09-28 实测报 No auth type），
    未配置时 run 返回 failed 入账（不假活，缺席查语义）。
    """


register = make_register(QwenAgentPlugin, MANIFEST_PATH)
