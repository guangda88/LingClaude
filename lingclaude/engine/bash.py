from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class BashResult:
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    command: str

    @property
    def success(self) -> bool:
        return self.exit_code == 0


class BashExecutor:
    def __init__(
        self,
        working_dir: str | None = None,
        timeout: int = 60,
        allowed_commands: list[str] | None = None,
        blocked_commands: list[str] | None = None,
    ) -> None:
        self.working_dir = working_dir
        self.timeout = timeout
        self.allowed_commands = allowed_commands
        self.blocked_commands = blocked_commands or [
            "rm -rf /",
            "sudo",
            "su",
            "mkfs",
            "dd if=",
            ":(){ :|:& };:",
        ]

    def run(self, command: str, timeout: int | None = None) -> BashResult:
        effective_timeout = timeout or self.timeout

        if not self._is_allowed(command):
            return BashResult(
                exit_code=126,
                stdout="",
                stderr=f"Command blocked: {command}",
                duration=0,
                command=command,
            )

        start = time.monotonic()
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                cwd=self.working_dir,
            )
            duration = time.monotonic() - start
            return BashResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration=duration,
                command=command,
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return BashResult(
                exit_code=124,
                stdout="",
                stderr=f"Command timed out after {effective_timeout}s",
                duration=duration,
                command=command,
            )
        except Exception as e:
            duration = time.monotonic() - start
            return BashResult(
                exit_code=1,
                stdout="",
                stderr=str(e),
                duration=duration,
                command=command,
            )

    def _is_allowed(self, command: str) -> bool:
        cmd_lower = command.lower().strip()

        for blocked in self.blocked_commands:
            if blocked.lower() in cmd_lower:
                return False

        if self.allowed_commands is not None:
            cmd_base = cmd_lower.split()[0] if cmd_lower.split() else ""
            return any(
                cmd_base == allowed.split()[0]
                for allowed in self.allowed_commands
            )

        return True
