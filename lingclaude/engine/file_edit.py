from __future__ import annotations

import difflib
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lingclaude.core.types import Result


@dataclass(frozen=True)
class ToolContract:
    scope: str
    effect: str
    rollback: str
    timeout: float = 30.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "effect": self.effect,
            "rollback": self.rollback,
            "timeout": self.timeout,
        }


@dataclass(frozen=True)
class EditResult:
    path: str
    lines_added: int
    lines_removed: int
    lines_changed: int
    diff: str
    duration: float
    backed_up: bool
    backup_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "lines_changed": self.lines_changed,
            "diff": self.diff,
            "duration": self.duration,
            "backed_up": self.backed_up,
            "backup_path": self.backup_path,
        }


class FileEditTool:
    contract = ToolContract(
        scope="file:edit",
        effect="Modifies file content in-place via text replacement, line insertion, or line deletion",
        rollback="Restores file from .bak copy created before each edit",
        timeout=10.0,
    )

    def __init__(
        self,
        base_dir: str = ".",
        max_file_size: int = 5 * 1024 * 1024,
        max_diff_lines: int = 500,
        backup_suffix: str = ".bak",
    ) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.max_file_size = max_file_size
        self.max_diff_lines = max_diff_lines
        self.backup_suffix = backup_suffix
        self._last_backup: dict[str, str] = {}

    def replace(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool = False,
    ) -> Result[EditResult]:
        start = time.monotonic()
        resolved = self._resolve(path)
        if resolved.is_error:
            return resolved  # type: ignore[return-value]
        target = resolved.data

        if not target.exists():
            return Result.fail(f"文件不存在: {path}")
        if not target.is_file():
            return Result.fail(f"不是文件: {path}")

        size_check = self._check_size(target)
        if size_check.is_error:
            return size_check  # type: ignore[return-value]

        try:
            content = target.read_text(encoding="utf-8")
        except Exception as e:
            return Result.fail(f"读取失败: {e}")

        count = content.count(old_text)
        if count == 0:
            return Result.fail(f"未找到匹配文本: {path}")
        if count > 1 and not replace_all:
            return Result.fail(
                f"找到 {count} 处匹配，请提供更多上下文或使用 replace_all=True"
            )

        self._backup(target)

        new_content = content.replace(old_text, new_text) if replace_all else content.replace(old_text, new_text, 1)

        write_result = self._write_and_diff(target, content, new_content, start)
        return write_result

    def create(self, path: str, content: str) -> Result[EditResult]:
        start = time.monotonic()
        resolved = self._resolve(path)
        if resolved.is_error:
            return resolved  # type: ignore[return-value]
        target = resolved.data

        if target.exists():
            return Result.fail(f"文件已存在: {path}，请使用 replace 编辑现有文件")

        target.parent.mkdir(parents=True, exist_ok=True)
        self._backup(target)
        old_content = ""
        new_content = content

        write_result = self._write_and_diff(target, old_content, new_content, start)
        return write_result

    def insert(self, path: str, line: int, text: str) -> Result[EditResult]:
        start = time.monotonic()
        resolved = self._resolve(path)
        if resolved.is_error:
            return resolved  # type: ignore[return-value]
        target = resolved.data

        if not target.exists():
            return Result.fail(f"文件不存在: {path}")

        size_check = self._check_size(target)
        if size_check.is_error:
            return size_check  # type: ignore[return-value]

        try:
            content = target.read_text(encoding="utf-8")
        except Exception as e:
            return Result.fail(f"读取失败: {e}")

        lines = content.splitlines(keepends=True)
        if not lines and content:
            lines = [content]

        if line < 0 or line > len(lines):
            return Result.fail(
                f"行号超出范围: {line}（文件共 {len(lines)} 行）"
            )

        backup_result = self._backup(target)
        if backup_result.is_error:
            return backup_result  # type: ignore[return-value]

        insert_text = text if text.endswith("\n") else text + "\n"
        lines.insert(line, insert_text)
        new_content = "".join(lines)

        write_result = self._write_and_diff(target, content, new_content, start)
        return write_result

    def delete_lines(self, path: str, start_line: int, end_line: int) -> Result[EditResult]:
        resolved = self._resolve(path)
        if resolved.is_error:
            return resolved  # type: ignore[return-value]
        target = resolved.data

        if not target.exists():
            return Result.fail(f"文件不存在: {path}")

        size_check = self._check_size(target)
        if size_check.is_error:
            return size_check  # type: ignore[return-value]

        t_start = time.monotonic()
        try:
            content = target.read_text(encoding="utf-8")
        except Exception as e:
            return Result.fail(f"读取失败: {e}")

        lines = content.splitlines(keepends=True)
        if not lines and content:
            lines = [content]

        if start_line < 0 or start_line >= len(lines):
            return Result.fail(
                f"起始行号超出范围: {start_line}（文件共 {len(lines)} 行）"
            )
        if end_line < start_line or end_line > len(lines):
            return Result.fail(
                f"结束行号无效: {end_line}（范围 {start_line}-{len(lines)}）"
            )

        backup_result = self._backup(target)
        if backup_result.is_error:
            return backup_result  # type: ignore[return-value]

        del lines[start_line:end_line]
        new_content = "".join(lines)

        write_result = self._write_and_diff(target, content, new_content, t_start)
        return write_result

    def undo(self, path: str) -> Result[str]:
        resolved = self._resolve(path)
        if resolved.is_error:
            return resolved  # type: ignore[return-value]
        target = resolved.data

        backup_path = self._last_backup.get(str(target))
        if not backup_path:
            backup_file = Path(str(target) + self.backup_suffix)
            if not backup_file.exists():
                return Result.fail(f"未找到备份: {path}")
            backup_path = str(backup_file)

        backup = Path(backup_path)
        if not backup.exists():
            return Result.fail(f"备份文件不存在: {backup_path}")

        try:
            shutil.copy2(backup, target)
            backup.unlink()
            self._last_backup.pop(str(target), None)
            return Result.ok(f"已回滚: {path}")
        except Exception as e:
            return Result.fail(f"回滚失败: {e}")

    def _resolve(self, path: str) -> Result[Path]:
        p = Path(path)
        if p.is_absolute():
            resolved = p.resolve()
        else:
            resolved = (self.base_dir / p).resolve()

        base_resolved = self.base_dir.resolve()

        try:
            resolved.relative_to(base_resolved)
        except ValueError:
            return Result.fail(f"拒绝访问路径 {path}（超出基础目录 {self.base_dir}）")

        return Result.ok(resolved)

    def _check_size(self, target: Path) -> Result[None]:
        try:
            size = target.stat().st_size
        except OSError as e:
            return Result.fail(f"无法获取文件大小: {e}")
        if size > self.max_file_size:
            return Result.fail(
                f"文件过大: {size} 字节（上限 {self.max_file_size} 字节）"
            )
        return Result.ok(None)

    def _backup(self, target: Path) -> Result[str]:
        if not target.exists():
            self._last_backup[str(target)] = ""
            return Result.ok("")
        backup_path = str(target) + self.backup_suffix
        try:
            shutil.copy2(target, backup_path)
            self._last_backup[str(target)] = backup_path
            return Result.ok(backup_path)
        except Exception as e:
            return Result.fail(f"备份失败: {e}")

    def _write_and_diff(
        self,
        target: Path,
        old_content: str,
        new_content: str,
        start_time: float,
    ) -> Result[EditResult]:
        try:
            target.write_text(new_content, encoding="utf-8")
        except Exception as e:
            return Result.fail(f"写入失败: {e}")

        duration = time.monotonic() - start_time

        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)

        diff_lines = list(difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{target.name}",
            tofile=f"b/{target.name}",
            n=3,
        ))

        if len(diff_lines) > self.max_diff_lines:
            diff_lines = diff_lines[: self.max_diff_lines]
            diff_lines.append(f"... (diff truncated at {self.max_diff_lines} lines)\n")

        diff_text = "".join(diff_lines)

        added = sum(1 for dl in diff_lines if dl.startswith("+") and not dl.startswith("+++"))
        removed = sum(1 for dl in diff_lines if dl.startswith("-") and not dl.startswith("---"))

        backup_path = self._last_backup.get(str(target))

        return Result.ok(
            EditResult(
                path=str(target),
                lines_added=added,
                lines_removed=removed,
                lines_changed=abs(len(new_lines) - len(old_lines)),
                diff=diff_text,
                duration=duration,
                backed_up=bool(backup_path),
                backup_path=backup_path,
            )
        )
