from __future__ import annotations

import time
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from lingclaude.mcp.lingxi_client import LingXiClient


@dataclass(frozen=True)
class BashResult:
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    command: str

    @property
    def success(self) -> bool:
        return self.exit_code == 0


class BashLingXiExecutor:
    """Bash executor using LingXi MCP server

    Provides secure terminal command execution with LingXi's
    security validation and performance monitoring.
    """

    def __init__(
        self,
        working_dir: str | None = None,
        timeout: int = 60,
        server_path: str = "/home/ai/Ling-term-mcp/dist/cli.js",
        node_path: str = "node",
        allowed_commands: list[str] | None = None,
        blocked_commands: list[str] | None = None,
    ) -> None:
        self.working_dir = working_dir
        self.timeout = timeout
        self.server_path = server_path
        self.node_path = node_path
        self.allowed_commands = allowed_commands
        self.blocked_commands = blocked_commands or []

        # Lazy initialization - client is created when first command is run
        self._client: Optional[LingXiClient] = None

    @property
    def _client_instance(self) -> LingXiClient:
        """Get or create LingXi client instance"""
        if self._client is None:
            self._client = LingXiClient(
                server_path=self.server_path,
                node_path=self.node_path,
            )
            self._client.start()
        return self._client

    def run(self, command: str, timeout: int | None = None) -> BashResult:
        """Execute a shell command via LingXi

        Args:
            command: Command string to execute
            timeout: Override default timeout

        Returns:
            BashResult with execution details
        """
        effective_timeout = timeout or self.timeout

        # Check if command is blocked
        blocked_reason = self._check_blocked(command)
        if blocked_reason:
            return BashResult(
                exit_code=126,
                stdout="",
                stderr=f"命令被阻止: {command}（原因: {blocked_reason}）",
                duration=0,
                command=command,
            )

        start = time.monotonic()
        try:
            # Parse command into parts
            try:
                parts = shlex.split(command)
            except ValueError:
                # Fall back to simple split if shlex fails
                parts = command.split()

            if not parts:
                return BashResult(
                    exit_code=127,
                    stdout="",
                    stderr="空命令",
                    duration=0,
                    command=command,
                )

            cmd_name = parts[0]
            args = parts[1:] if len(parts) > 1 else None

            # Execute via LingXi
            client = self._client_instance
            output = client.execute_command(
                command=cmd_name,
                args=args,
                timeout=effective_timeout,
            )

            duration = time.monotonic() - start
            return BashResult(
                exit_code=0,
                stdout=output,
                stderr="",
                duration=duration,
                command=command,
            )

        except Exception as e:
            duration = time.monotonic() - start
            error_msg = str(e)

            # Check for timeout errors
            if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                return BashResult(
                    exit_code=124,
                    stdout="",
                    stderr=f"命令在 {effective_timeout}s 后超时",
                    duration=duration,
                    command=command,
                )

            return BashResult(
                exit_code=1,
                stdout="",
                stderr=f"执行失败: {error_msg}",
                duration=duration,
                command=command,
            )

    def _check_blocked(self, command: str) -> str | None:
        """Check if command is blocked

        Args:
            command: Command string

        Returns:
            Reason if blocked, None otherwise
        """
        cmd_stripped = command.strip()
        cmd_lower = cmd_stripped.lower()

        # Check custom blocked commands
        for blocked in self.blocked_commands:
            if blocked.lower() in cmd_lower:
                return f"匹配黑名单规则 '{blocked}'"

        # If allowed commands specified, check against them
        if self.allowed_commands is not None:
            parts = cmd_stripped.split()
            if not parts:
                return None

            base_cmd = parts[0]
            base_cmd_name = Path(base_cmd).name

            if not any(
                base_cmd_name.lower() == allowed.split()[0].lower()
                for allowed in self.allowed_commands
            ):
                return f"'{base_cmd_name}' 不在允许列表中"

        return None

    def close(self) -> None:
        """Close LingXi client connection"""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self):
        """Support context manager"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Close connection on exit"""
        self.close()
        return False
