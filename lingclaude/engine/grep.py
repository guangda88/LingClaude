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
    context: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        d = {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "content": self.content,
            "matched_text": self.matched_text,
        }
        if self.context:
            d["context"] = list(self.context)
        return d


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

    # T0-5: 默认全类型搜索时跳过的垃圾目录（防 node_modules 级灾难扫描）
    _SKIP_DIRS = frozenset({
        "node_modules", "__pycache__", ".venv", "venv", "env",
        "dist", "build", "target", ".tox", ".mypy_cache", ".pytest_cache",
        ".git", ".idea", ".vscode",
    })
    # 单次搜索的文件数上限（默认 "*" 时防止大目录树耗尽时间）
    _MAX_FILES = 5000

    def __init__(
        self,
        base_dir: str = ".",
        max_results: int = 200,
        max_line_length: int = 500,
        default_include: str = "*",
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
        path: str | None = None,
        before: int = 0,
        after: int = 0,
        context: int = 0,
    ) -> Result[GrepResult]:
        start = time.monotonic()
        glob_pattern = include or self.default_include
        # T0-5: path 参数 — 指定搜索根目录（默认 base_dir）
        try:
            search_root = Path(path).resolve() if path else self.base_dir
        except (OSError, RuntimeError) as e:
            return Result.fail(f"无效搜索路径: {e}")
        if not search_root.is_dir():
            return Result.fail(f"搜索路径不存在或不是目录: {search_root}")

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
        truncated = False

        file_iter = search_root.rglob(glob_pattern)
        for file_path in file_iter:
            if not file_path.is_file():
                continue
            if self._is_hidden(file_path, search_root):
                continue
            if self._in_skip_dir(file_path, search_root):
                continue
            # 隐藏文件可搜（.gitignore/.github 等），但凭据文件一律跳过
            # （防止 grep 把 .env/.npmrc 等密钥内容带进模型上下文）
            if self._is_sensitive(file_path, search_root):
                continue
            if max_depth is not None:
                rel = file_path.relative_to(search_root)
                if len(rel.parts) > max_depth:
                    continue

            # 文件数上限：默认全类型搜索时防止大目录树耗尽时间
            if files_searched >= self._MAX_FILES:
                truncated = True
                break

            files_searched += 1

            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            lines = content.splitlines()
            for i, line in enumerate(lines, 1):
                for m in regex.finditer(line):
                    files_matched_set.add(str(file_path))
                    display_line = line.strip()
                    if len(display_line) > self.max_line_length:
                        display_line = display_line[:self.max_line_length] + "..."

                    # T0-5: 上下文行（对标 grep -B/-A/-C）
                    context_lines: tuple[str, ...] = ()
                    effective_before = before if before > 0 else context
                    effective_after = after if after > 0 else context
                    if effective_before > 0 or effective_after > 0:
                        lo = max(0, i - 1 - effective_before)
                        hi = min(len(lines), i + effective_after)
                        ctx = [
                            f"{j+1}: {lines[j].rstrip()[:self.max_line_length]}"
                            for j in range(lo, hi)
                            if j != i - 1
                        ]
                        context_lines = tuple(ctx)

                    matches.append(
                        GrepMatch(
                            file=str(file_path.relative_to(search_root)),
                            line=i,
                            column=m.start() + 1,
                            content=display_line,
                            matched_text=m.group(0),
                            context=context_lines,
                        )
                    )
                    if len(matches) >= self.max_results:
                        truncated = True
                        break
                if truncated:
                    break
            if truncated:
                break

        duration = time.monotonic() - start
        return Result.ok(
            GrepResult(
                matches=tuple(matches),
                files_searched=files_searched,
                files_matched=len(files_matched_set),
                total_matches=len(matches),
                duration=duration,
                truncated=truncated,
            )
        )

    def _is_hidden(self, path: Path, root: Path | None = None) -> bool:
        base = root or self.base_dir
        try:
            rel = path.relative_to(base)
        except ValueError:
            return False
        return any(part.startswith(".") for part in rel.parts[:-1])

    def _in_skip_dir(self, path: Path, root: Path) -> bool:
        try:
            rel = path.relative_to(root)
        except ValueError:
            return False
        return any(part in self._SKIP_DIRS for part in rel.parts[:-1])

    @staticmethod
    def _is_sensitive(path: Path, root: Path) -> bool:
        from lingclaude.engine.sensitive_path_gate import is_sensitive_path

        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = str(path)
        # 相对路径补前导 / 使 "/.ssh" 类目录标记可命中
        return is_sensitive_path(rel) or is_sensitive_path(f"/{rel}")
