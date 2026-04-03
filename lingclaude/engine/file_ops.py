from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lingclaude.core.types import Result


@dataclass
class FileInfo:
    path: str
    content: str
    size: int
    encoding: str = "utf-8"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size": self.size,
            "encoding": self.encoding,
        }


class FileOps:
    def __init__(self, base_dir: str = ".") -> None:
        self.base_dir = Path(base_dir).resolve()

    def read(self, path: str) -> Result[FileInfo]:
        target = self._resolve(path)
        if not target.exists():
            return Result.fail(f"File not found: {path}")
        if not target.is_file():
            return Result.fail(f"Not a file: {path}")

        try:
            content = target.read_text(encoding="utf-8")
            return Result.ok(
                FileInfo(
                    path=str(target),
                    content=content,
                    size=target.stat().st_size,
                )
            )
        except Exception as e:
            return Result.fail(f"Read error: {e}")

    def write(self, path: str, content: str) -> Result[str]:
        target = self._resolve(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return Result.ok(str(target))
        except Exception as e:
            return Result.fail(f"Write error: {e}")

    def edit(
        self, path: str, old_text: str, new_text: str, replace_all: bool = False
    ) -> Result[str]:
        read_result = self.read(path)
        if read_result.is_error:
            return read_result  # type: ignore[return-value]

        content = read_result.data.content
        count = content.count(old_text)
        if count == 0:
            return Result.fail(f"Text not found in {path}")
        if count > 1 and not replace_all:
            return Result.fail(
                f"Multiple matches ({count}) in {path}, use replace_all=True"
            )

        new_content = content.replace(old_text, new_text) if replace_all else content.replace(old_text, new_text, 1)
        write_result = self.write(path, new_content)
        return write_result

    def glob(self, pattern: str) -> Result[list[str]]:
        matches = sorted(self.base_dir.glob(pattern))
        return Result.ok([str(m.relative_to(self.base_dir)) for m in matches if m.is_file()])

    def grep(
        self, pattern: str, include: str = "*.py"
    ) -> Result[list[dict[str, Any]]]:
        import re

        regex = re.compile(pattern)
        results: list[dict[str, Any]] = []

        for file_path in self.base_dir.rglob(include):
            if not file_path.is_file():
                continue
            try:
                for i, line in enumerate(
                    file_path.read_text(encoding="utf-8").splitlines(), 1
                ):
                    if regex.search(line):
                        results.append(
                            {
                                "file": str(
                                    file_path.relative_to(self.base_dir)
                                ),
                                "line": i,
                                "content": line.strip(),
                            }
                        )
            except Exception:
                continue

        return Result.ok(results)

    def exists(self, path: str) -> bool:
        return self._resolve(path).exists()

    def delete(self, path: str) -> Result[str]:
        target = self._resolve(path)
        if not target.exists():
            return Result.fail(f"Not found: {path}")
        try:
            if target.is_dir():
                import shutil
                shutil.rmtree(target)
            else:
                target.unlink()
            return Result.ok(str(target))
        except Exception as e:
            return Result.fail(f"Delete error: {e}")

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        if p.is_absolute():
            return p
        return self.base_dir / p
