"""灵扬（lingyang）插片——家族批量插片化（批2）。

载体定性（2026-09-18 实测）：CLI 型（/home/ai/lingyang）
铁律锚点：manifest 全声明 + agent_family.McpAgentPluginBase（J4 run 全程 record 化 +
铁律 8 缺席查）；非 MCP 型：_call_tool 显式 raise → run failed 入账（J4 不假活）。
账本对账：org_member/lingyang.json 的 trust/plug 声明与 manifest 一致（测试锚定）。
"""
from __future__ import annotations

from pathlib import Path

from lingclaude.plugins.agents.agent_family import McpAgentPluginBase

MANIFEST_PATH = Path(__file__).parent / "manifest.agent.json"


class LingYangAgentPlugin(McpAgentPluginBase):
    """agent/lingyang 灵扬插片（cli 型，manifest 全驱动）。"""

    def _call_tool(self, tool: str, arguments: dict) -> str:
        if self._manifest["transport"].get("kind") != "mcp":
            raise RuntimeError(
                "carrier kind=%s 无 MCP 传输：run 一律 failed 入账（J4 不假活，"
                "载体清偿见债务 family-carriers-batch2-4）"
                % self._manifest["transport"]["kind"])
        return super()._call_tool(tool, arguments)


def register(registry) -> None:
    from lingclaude.core.seam import SeamType  # 延迟 import，避免循环
    registry.register(SeamType.AGENT, LingYangAgentPlugin.name, LingYangAgentPlugin())
