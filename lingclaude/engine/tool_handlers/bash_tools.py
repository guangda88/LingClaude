"""Bash 工具 handler 插片 — 从 coding.py 拆分（灵元：工具是插片）。"""

from __future__ import annotations

from typing import Any


class BashToolsMixin:
    """bash / bash_lingxi 工具 handler（依赖 self.bash / self.bash_lingxi）。"""

    def _bash_handler(self, command: str, **_kwargs: Any) -> dict[str, Any]:
        # T0-2: sensitive_path_gate 检查 bash 命令中的路径
        from lingclaude.engine.sensitive_path_gate import check_sensitive_path
        # P2-1 收紧：只提取路径形 token（~ / ./ ../ 前缀），且 / 前不是单词字符
        # （否则 echo "abc/def" 会把 /def 误当路径；引号内真实路径 cat "/home/x" 仍会命中）
        import re
        paths_in_cmd = re.findall(r'(?:~|(?<![\w])/|\.\.?/)[\w./~-]+', command)
        for p in paths_in_cmd:
            if len(p) > 1:
                # 修复 2026-09-11（四位监督审计 P0-3）：传入完整 command，
                # 让 gate 区分 metadata（test -f / ls → 放行）vs read（cat → 拦）
                is_sensitive, reason = check_sensitive_path(p, command=command)
                if is_sensitive:
                    return {"error": f"Path blocked by sensitive_path_gate in command: {p} ({reason})"}
        result = self.bash.run(command)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration": result.duration,
        }

    def _bash_lingxi_handler(self, command: str, **_kwargs: Any) -> dict[str, Any]:
        result = self.bash_lingxi.run(command)
        return {
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration": result.duration,
        }
