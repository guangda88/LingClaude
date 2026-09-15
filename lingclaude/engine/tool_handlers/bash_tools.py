"""bash / bash_lingxi 工具 handler（依赖 self.bash / self.bash_lingxi）。"""

from __future__ import annotations

from typing import Any

from lingclaude.core.types import ToolResult


# 路径形 token 提取正则——单一来源，两个 handler 共用，禁止二次分叉。
# 修复 2026-09-12（codex 审计 P0-2）：_bash_lingxi_handler 此前用了非 raw string
# 的副本（`\\\\`），导致正则被二次转义、只匹配出 `~/.` 而漏掉 `/ssh/config`，
# sensitive_path_gate 形同虚设。现抽为模块级常量，与 _bash_handler 完全同源。
# 设计：只提取路径形 token（~ / ./ ../ 前缀），且 / 前不是单词字符
# （否则 echo "abc/def" 会把 /def 误当路径；引号内真实路径 cat "/home/x" 仍会命中）。
_PATH_TOKEN_RE = r'(?:~|(?<![\w])/|\.\.?/)[\w./~-]+'


def _check_sensitive_in_command(command: str) -> tuple[str | None, str | None]:
    """扫路径 token → gate 判定。返回 (blocked_path, reason)；无拦截返回 (None, None)。

    2026-09-11（四位监督审计 P0-3）：传完整 command，让 gate 区分
    metadata（test -f / ls → 放行）vs read（cat → 拦）。
    2026-09-12（codex 审计 P0-2）：两 handler 共用此函数，消除正则分叉。
    """
    from lingclaude.engine.sensitive_path_gate import check_sensitive_path
    import re

    for p in re.findall(_PATH_TOKEN_RE, command):
        if len(p) > 1:
            is_sensitive, reason = check_sensitive_path(p, command=command)
            if is_sensitive:
                return p, reason
    return None, None


class BashToolsMixin:
    """bash / bash_lingxi 工具 handler（依赖 self.bash / self.bash_lingxi）。"""

    def _bash_handler(self, command: str, **_kwargs: Any) -> ToolResult[dict[str, Any]]:
        # T0-2: sensitive_path_gate 检查 bash 命令中的路径
        blocked, reason = _check_sensitive_in_command(command)
        if blocked:
            return ToolResult.err(
                f"Path blocked by sensitive_path_gate in command: {blocked} ({reason})",
                tool_name="bash",
            )
        result = self.bash.run(command)
        return ToolResult.ok(
            {
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration": result.duration,
            }
        )

    def _bash_lingxi_handler(self, command: str, **_kwargs: Any) -> ToolResult[dict[str, Any]]:
        # P0 安全对齐(2026-09-11): 此前 bash_lingxi handler 无 sensitive_path_gate
        # 也无默认黑名单, 是全工具面最明显的安全旁路(codex 审计命中)。
        # 现与 _bash_handler 走同一套敏感路径检查(credential exfiltration 防御)。
        blocked, reason = _check_sensitive_in_command(command)
        if blocked:
            return ToolResult.err(
                f"Path blocked by sensitive_path_gate in command: {blocked} ({reason})",
                tool_name="bash_lingxi",
            )

        result = self.bash_lingxi.run(command)
        return ToolResult.ok(
            {
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration": result.duration,
            }
        )
