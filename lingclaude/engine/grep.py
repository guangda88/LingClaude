from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lingclaude.engine.file_edit import ToolContract
from lingclaude.core.types import Result


@dataclass(frozen=True)
class GrepMatch:
    file: str
    line: int
    column: int
    content: str
    matched_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "content": self.content,
            "matched_text": self.matched_text,
        }


@dataclass(frozen=True)
class GrepResult:
    matches: tuple[GrepMatch, ...]
    files_searched: int
    files_matched: int
    total_matches: int
    duration: float
    truncated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "matches": [m.to_dict() for m in self.matches],
            "files_searched": self.files_searched,
            "files_matched": self.files_matched,
            "total_matches": self.total_matches,
            "duration": self.duration,
            "truncated": self.truncated,
        }


class GrepTool:
    contract = ToolContract(
        scope="file:search",
        effect="Searches file contents using regex patterns, read-only",
        rollback="No rollback needed — read-only operation",
        timeout=15.0,
    )

    def __init__(
        self,
        base_dir: str = ".",
        max_results: int = 200,
        max_line_length: int = 500,
        default_include: str = "*.py",
    ) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.max_results = max_results
        self.max_line_length = max_line_length
        self.default_include = default_include

    def search(
        self,
        pattern: str,
        include: str | None = None,
        literal: bool = False,
        case_sensitive: bool = True,
        max_depth: int | None = None,
    ) -> Result[GrepResult]:
        start = time.monotonic()
        glob_pattern = include or self.default_include

        try:
            if literal:
                regex = re.compile(re.escape(pattern), 0 if case_sensitive else re.IGNORECASE)
            else:
                flags = 0 if case_sensitive else re.IGNORECASE
                regex = re.compile(pattern, flags)
        except re.error as e:
            return Result.fail(f"正则表达式无效: {e}")

        matches: list[GrepMatch] = []
        files_searched = 0
        files_matched_set: set[str] = set()

        file_iter = self.base_dir.rglob(glob_pattern)
        for file_path in file_iter:
            if not file_path.is_file():
                continue
            if self._is_hidden(file_path):
                continue
            if max_depth is not None:
                rel = file_path.relative_to(self.base_dir)
                if len(rel.parts) > max_depth:
                    continue

            files_searched += 1

            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            for i, line in enumerate(content.splitlines(), 1):
                for m in regex.finditer(line):
                    files_matched_set.add(str(file_path))
                    display_line = line.strip()
                    if len(display_line) > self.max_line_length:
                        display_line = display_line[:self.max_line_length] + "..."

                    matches.append(
                        GrepMatch(
                            file=str(file_path.relative_to(self.base_dir)),
                            line=i,
                            column=m.start() + 1,
                            content=display_line,
                            matched_text=m.group(0),
                        )
                    )
                    if len(matches) >= self.max_results:
                        duration = time.monotonic() - start
                        return Result.ok(
                            GrepResult(
                                matches=tuple(matches),
                                files_searched=files_searched,
                                files_matched=len(files_matched_set),
                                total_matches=len(matches),
                                duration=duration,
                                truncated=True,
                            )
                        )

        duration = time.monotonic() - start
        return Result.ok(
            GrepResult(
                matches=tuple(matches),
                files_searched=files_searched,
                files_matched=len(files_matched_set),
                total_matches=len(matches),
                duration=duration,
                truncated=False,
            )
        )

    def _is_hidden(self, path: Path) -> bool:
        try:
            rel = path.relative_to(self.base_dir)
        except ValueError:
            return False
        return any(part.startswith(".") for part in rel.parts)
