from __future__ import annotations

import base64
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lingclaude.engine.file_edit import ToolContract
from lingclaude.core.types import Result

_IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".ico",
})

_BINARY_EXTENSIONS = frozenset({
    ".pyc", ".so", ".o", ".a", ".exe", ".dll", ".dylib",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".mp3", ".mp4", ".wav", ".avi", ".mkv", ".mov",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".sqlite", ".db",
})


@dataclass(frozen=True)
class ReadResult:
    path: str
    content: str
    size: int
    lines: int
    encoding: str = "utf-8"
    is_image: bool = False
    image_mime: str | None = None
    duration: float = 0.0
    truncated: bool = False
    offset: int = 0
    limit: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "path": self.path,
            "size": self.size,
            "lines": self.lines,
            "encoding": self.encoding,
            "duration": self.duration,
            "truncated": self.truncated,
        }
        if self.is_image:
            d["is_image"] = True
            d["image_mime"] = self.image_mime
        else:
            d["content"] = self.content
            if self.offset:
                d["offset"] = self.offset
            if self.limit is not None:
                d["limit"] = self.limit
        return d


class FileReadTool:
    contract = ToolContract(
        scope="file:read",
        effect="Reads file contents (text or binary) without modification",
        rollback="No rollback needed — read-only operation",
        timeout=5.0,
    )

    def __init__(
        self,
        base_dir: str = ".",
        max_file_size: int = 5 * 1024 * 1024,
        max_line_length: int = 2000,
        default_encoding: str = "utf-8",
    ) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.max_file_size = max_file_size
        self.max_line_length = max_line_length
        self.default_encoding = default_encoding

    def read(
        self,
        path: str,
        offset: int = 0,
        limit: int | None = None,
        line_numbers: bool = True,
    ) -> Result[ReadResult]:
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

        ext = target.suffix.lower()
        if ext in _IMAGE_EXTENSIONS:
            return self._read_image(target, start)

        if ext in _BINARY_EXTENSIONS:
            return Result.fail(
                f"二进制文件不支持文本读取: {ext}。"
                f"请使用专用的二进制处理工具"
            )

        return self._read_text(target, offset, limit, line_numbers, start)

    def read_image(self, path: str) -> Result[ReadResult]:
        start = time.monotonic()
        resolved = self._resolve(path)
        if resolved.is_error:
            return resolved  # type: ignore[return-value]
        target = resolved.data

        if not target.exists():
            return Result.fail(f"文件不存在: {path}")

        ext = target.suffix.lower()
        if ext not in _IMAGE_EXTENSIONS:
            return Result.fail(f"不是图像文件: {ext}")

        return self._read_image(target, start)

    def _read_text(
        self,
        target: Path,
        offset: int,
        limit: int | None,
        line_numbers: bool,
        start: float,
    ) -> Result[ReadResult]:
        try:
            raw = target.read_text(encoding=self.default_encoding)
        except UnicodeDecodeError:
            try:
                raw = target.read_text(encoding="latin-1")
            except Exception as e:
                return Result.fail(f"编码检测失败: {e}")
        except Exception as e:
            return Result.fail(f"读取失败: {e}")

        all_lines = raw.splitlines()
        total_lines = len(all_lines)

        start_idx = min(offset, total_lines)
        end_idx = total_lines if limit is None else min(start_idx + limit, total_lines)

        selected = all_lines[start_idx:end_idx]
        truncated = end_idx < total_lines

        if line_numbers:
            numbered: list[str] = []
            for i, line in enumerate(selected, start=start_idx + 1):
                if len(line) > self.max_line_length:
                    line = line[: self.max_line_length] + f"... (truncated at {self.max_line_length} chars)"
                numbered.append(f"{i:>6}\t{line}")
            content = "\n".join(numbered)
        else:
            content = "\n".join(
                line[: self.max_line_length] + f"... ({len(line)} chars)"
                if len(line) > self.max_line_length else line
                for line in selected
            )

        duration = time.monotonic() - start

        return Result.ok(
            ReadResult(
                path=str(target),
                content=content,
                size=target.stat().st_size,
                lines=total_lines,
                encoding=self.default_encoding,
                duration=duration,
                truncated=truncated,
                offset=start_idx,
                limit=limit,
            )
        )

    def _read_image(self, target: Path, start: float) -> Result[ReadResult]:
        size = target.stat().st_size
        if size > self.max_file_size:
            return Result.fail(
                f"图像文件过大: {size} 字节（上限 {self.max_file_size} 字节）"
            )

        try:
            data = target.read_bytes()
        except Exception as e:
            return Result.fail(f"读取图像失败: {e}")

        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        b64 = base64.b64encode(data).decode("ascii")
        duration = time.monotonic() - start

        return Result.ok(
            ReadResult(
                path=str(target),
                content=b64,
                size=size,
                lines=0,
                is_image=True,
                image_mime=mime,
                duration=duration,
            )
        )

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
