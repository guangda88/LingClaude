from __future__ import annotations

import ast
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from lingclaude.core.config import VerificationConfig


@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    checks: tuple[dict[str, Any], ...] = ()
    error: str | None = None


@dataclass
class VerificationGate:
    enabled: bool = True
    syntax_check: bool = True
    test_run: bool = False
    test_command: str = "python3 -m pytest {test_path} -x -q --tb=short"
    blocked_extensions: tuple[str, ...] = (".py",)
    allowed_write_roots: tuple[str, ...] = ()
    max_tool_calls_per_session: int = 0
    _tool_call_count: int = field(default=0, repr=False)

    @classmethod
    def from_config(cls, config: VerificationConfig) -> VerificationGate:
        return cls(
            enabled=config.enabled,
            syntax_check=config.syntax_check,
            test_run=config.test_run,
            test_command=config.test_command,
            blocked_extensions=config.blocked_extensions,
            allowed_write_roots=config.allowed_write_roots,
            max_tool_calls_per_session=config.max_tool_calls_per_session,
        )

    def verify(
        self,
        tool_name: str,
        file_path: str | None = None,
        content: str | None = None,
    ) -> VerificationResult:
        if not self.enabled:
            return VerificationResult(passed=True)

        if file_path is None:
            return VerificationResult(passed=True)

        path = Path(file_path)
        if path.suffix not in self.blocked_extensions:
            return VerificationResult(passed=True)

        checks: list[dict[str, Any]] = []

        path_check = self._check_path_traversal(path)
        if not path_check["passed"]:
            return VerificationResult(
                passed=False,
                checks=tuple([path_check]),
                error=path_check.get("error", "path traversal blocked"),
            )
        checks.append(path_check)

        if self.syntax_check and content is not None:
            syn = self._check_syntax(content, str(path))
            checks.append(syn)
            if not syn["passed"]:
                return VerificationResult(
                    passed=False,
                    checks=tuple(checks),
                    error=syn["error"],
                )

        return VerificationResult(passed=True, checks=tuple(checks))

    def verify_post_write(
        self,
        file_path: str,
    ) -> VerificationResult:
        if not self.enabled or not self.syntax_check:
            return VerificationResult(passed=True)

        path = Path(file_path)
        if path.suffix not in self.blocked_extensions or not path.exists():
            return VerificationResult(passed=True)

        checks: list[dict[str, Any]] = []

        content = path.read_text(encoding="utf-8", errors="replace")
        syn = self._check_syntax(content, str(path))
        checks.append(syn)
        if not syn["passed"]:
            return VerificationResult(
                passed=False,
                checks=tuple(checks),
                error=syn["error"],
            )

        if self.test_run:
            test = self._run_tests(str(path))
            checks.append(test)
            if not test["passed"]:
                return VerificationResult(
                    passed=False,
                    checks=tuple(checks),
                    error=test.get("error", "tests failed"),
                )

        return VerificationResult(passed=True, checks=tuple(checks))

    def check_rate_limit(self) -> VerificationResult:
        if self.max_tool_calls_per_session <= 0:
            return VerificationResult(passed=True)
        self._tool_call_count += 1
        if self._tool_call_count > self.max_tool_calls_per_session:
            return VerificationResult(
                passed=False,
                error=f"会话工具调用次数已达上限 ({self.max_tool_calls_per_session})",
            )
        return VerificationResult(passed=True)

    def reset_rate_limit(self) -> None:
        self._tool_call_count = 0

    def _check_path_traversal(self, path: Path) -> dict[str, Any]:
        if not self.allowed_write_roots:
            return {"check": "path_traversal", "passed": True, "file": str(path)}
        try:
            resolved = path.resolve()
            for root in self.allowed_write_roots:
                if str(resolved).startswith(str(Path(root).resolve()) + os.sep) or resolved == Path(root).resolve():
                    return {"check": "path_traversal", "passed": True, "file": str(path)}
        except (OSError, ValueError):
            pass
        return {
            "check": "path_traversal",
            "passed": False,
            "file": str(path),
            "error": f"写入路径超出允许范围: {path}",
        }

    @staticmethod
    def _check_syntax(content: str, filename: str) -> dict[str, Any]:
        try:
            ast.parse(content, filename=filename)
            return {"check": "syntax", "passed": True, "file": filename}
        except SyntaxError as e:
            return {
                "check": "syntax",
                "passed": False,
                "file": filename,
                "error": f"SyntaxError: {e.msg} at line {e.lineno}",
                "line": e.lineno,
                "offset": e.offset,
            }

    def _run_tests(self, file_path: str) -> dict[str, Any]:
        test_path = file_path.replace("/lingclaude/", "/tests/").replace(".py", "")
        if not Path(test_path).exists():
            src_path = Path(file_path)
            test_path = str(src_path.parent.parent / "tests" / f"test_{src_path.name}")
        cmd = self.test_command.format(test_path=test_path)
        try:
            result = subprocess.run(
                shlex.split(cmd), shell=False, capture_output=True, text=True, timeout=30,
            )
            passed = result.returncode == 0
            output = (result.stdout + result.stderr).strip()[:500]
            return {
                "check": "test_run",
                "passed": passed,
                "file": file_path,
                "command": cmd,
                "returncode": result.returncode,
                **({"error": output} if not passed else {"output": output}),
            }
        except subprocess.TimeoutExpired:
            return {
                "check": "test_run",
                "passed": False,
                "file": file_path,
                "command": cmd,
                "error": "测试执行超时（30秒）",
            }
        except Exception as e:
            return {
                "check": "test_run",
                "passed": False,
                "file": file_path,
                "command": cmd,
                "error": str(e),
            }

    @staticmethod
    def check_syntax(content: str, filename: str = "<string>") -> dict[str, Any]:
        return VerificationGate._check_syntax(content, filename)


WRITE_SCOPED_TOOLS = frozenset({
    "write",
    "edit",
    "file_create",
    "file_insert",
    "file_delete_lines",
    "ast_replace",
})

CRITICAL_TOOLS = frozenset({
    "bash",
    "bash_lingxi",
})
