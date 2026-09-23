"""灵信（lingmessage）插片——家族批量插片化批1（MCP stdio 型）。

载体实测（2026-09-18）：healthy（实测 2026-09-18：initialize 回标准 result）
铁律锚点：manifest 全声明（铁律 5/6/7 + stop_layer 三要素）+ agent_family.McpAgentPluginBase
（铁律 3/J4 run 全程 record 化 + 铁律 8 缺席查）。账本对账：org_member/lingmessage.json 的
trust/plug 声明与 manifest 一致（N1 精神，测试锚定）。
"""
from __future__ import annotations

from pathlib import Path

from lingclaude.plugins.agents.agent_family import McpAgentPluginBase

MANIFEST_PATH = Path(__file__).parent / "manifest.agent.json"


class LingMessageAgentPlugin(McpAgentPluginBase):
    """agent/lingmessage 灵信插片（MCP stdio，manifest 全驱动）。"""


def register(registry) -> None:
    from lingclaude.core.seam import SeamType  # 延迟 import，避免循环
    _p = LingMessageAgentPlugin(MANIFEST_PATH)
    registry.register(SeamType.AGENT, _p.name, _p)
