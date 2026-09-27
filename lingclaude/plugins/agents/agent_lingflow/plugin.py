"""灵通（lingflow）插片——家族批量插片化（批2）。

载体定性（2026-09-27 载体清偿）：MCP stdio 型（lingflow_mcp_server.py 零依赖
JSON-RPC 薄壳，包装 skills/*/implementation.py 既有入口——run_skill/run_workflow/
list_skills）。原 platform 型的 _call_tool 显式 raise 已随薄壳上线移除（父类
MCP 真传输接管；J4 record/铁律 8 缺席查由 McpAgentPluginBase 统一保障）。
账本对账：org_member/lingflow.json 的 trust/plug 声明与 manifest 一致（测试锚定）。
"""
from __future__ import annotations

from pathlib import Path

from lingclaude.plugins.agents.agent_family import McpAgentPluginBase

MANIFEST_PATH = Path(__file__).parent / "manifest.agent.json"


class LingFlowAgentPlugin(McpAgentPluginBase):
    """agent/lingflow 灵通插片（MCP stdio 型，manifest 全驱动）。"""


def register(registry) -> None:
    from lingclaude.core.seam import SeamType  # 延迟 import，避免循环
    _p = LingFlowAgentPlugin(MANIFEST_PATH)
    registry.register(SeamType.AGENT, _p.name, _p)
