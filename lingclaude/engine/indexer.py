"""Project symbol indexer for lingclaude.

Scans Python source trees and builds a symbol table:
- imports (module level)
- classes (name, bases, methods)
- functions (name, args, decorators)

The index is used to inject project context into system prompts,
so lingclaude knows "what modules, classes, functions exist" before editing.
"""
from __future__ import annotations

import ast
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lingclaude.core.types import Result


@dataclass
class SymbolInfo:
    name: str
    kind: str  # "class", "function", "import"
    file: str
    line: int
    detail: str = ""  # base classes, decorators, arg list

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "detail": self.detail,
        }


@dataclass
class ProjectIndex:
    root: str
    files_scanned: int = 0
    symbols: list[SymbolInfo] = field(default_factory=list)
    file_summaries: dict[str, dict[str, int]] = field(default_factory=dict)
    duration: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "files_scanned": self.files_scanned,
            "total_symbols": len(self.symbols),
            "classes": sum(1 for s in self.symbols if s.kind == "class"),
            "functions": sum(1 for s in self.symbols if s.kind == "function"),
            "imports": sum(1 for s in self.symbols if s.kind == "import"),
            "duration": round(self.duration, 3),
            "symbols": [s.to_dict() for s in self.symbols[:200]],
            "file_summaries": self.file_summaries,
        }

    def format_compact(self, max_symbols: int = 100) -> str:
        lines = [f"项目索引: {self.root} ({self.files_scanned} 文件, {len(self.symbols)} 符号)"]

        classes = [s for s in self.symbols if s.kind == "class"]
        functions = [s for s in self.symbols if s.kind == "function"]

        if classes:
            lines.append("\n类:")
            for c in classes[:max_symbols]:
                methods = [
                    s.name for s in self.symbols
                    if s.kind == "function" and s.file == c.file
                    and s.line > c.line
                    and s.detail.startswith("method:")
                ]
                method_str = f" → {', '.join(methods[:8])}" if methods else ""
                lines.append(f"  {c.name} ({c.file}:{c.line}){method_str}")

        if functions:
            top_funcs = [f for f in functions if not f.detail.startswith("method:")][:max_symbols]
            if top_funcs:
                lines.append("\n函数:")
                for f in top_funcs:
                    lines.append(f"  {f.name}({f.detail}) ({f.file}:{f.line})")

        return "\n".join(lines)


_SKIP_DIRS = frozenset({
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".eggs", "*.egg-info",
})


def _should_skip(path: Path) -> bool:
    return any(part in _SKIP_DIRS for part in path.parts)


def _parse_file(path: Path, root: Path) -> list[SymbolInfo]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    rel = str(path.relative_to(root))
    symbols: list[SymbolInfo] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                symbols.append(SymbolInfo(
                    name=alias.asname or alias.name,
                    kind="import",
                    file=rel,
                    line=node.lineno,
                    detail=alias.name,
                ))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = ", ".join(a.asname or a.name for a in node.names)
            symbols.append(SymbolInfo(
                name=f"{module}.{names}" if module else names,
                kind="import",
                file=rel,
                line=node.lineno,
                detail=f"from {module}",
            ))
        elif isinstance(node, ast.ClassDef):
            bases = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
                elif isinstance(base, ast.Attribute):
                    bases.append(ast.dump(base))
            decorators = []
            for d in node.decorator_list:
                if isinstance(d, ast.Name):
                    decorators.append(d.id)
            symbols.append(SymbolInfo(
                name=node.name,
                kind="class",
                file=rel,
                line=node.lineno,
                detail=", ".join(bases) if bases else "",
            ))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = _format_args(item.args)
                    dec = [d.id if isinstance(d, ast.Name) else "" for d in item.decorator_list]
                    symbols.append(SymbolInfo(
                        name=item.name,
                        kind="function",
                        file=rel,
                        line=item.lineno,
                        detail=f"method:{node.name}({args})" + (f" @{','.join(dec)}" if dec else ""),
                    ))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = _format_args(node.args)
            decorators = []
            for d in node.decorator_list:
                if isinstance(d, ast.Name):
                    decorators.append(d.id)
            symbols.append(SymbolInfo(
                name=node.name,
                kind="function",
                file=rel,
                line=node.lineno,
                detail=args + (f" @{','.join(decorators)}" if decorators else ""),
            ))

    return symbols


def _format_args(args: ast.arguments) -> str:
    parts: list[str] = []
    for arg in args.args:
        if arg.arg == "self" or arg.arg == "cls":
            continue
        annotation = ""
        if arg.annotation:
            annotation = f": {ast.dump(arg.annotation)[:30]}"
        parts.append(f"{arg.arg}{annotation}")
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    return ", ".join(parts[:6]) + ("..." if len(parts) > 6 else "")


def index_project(root: str = ".", max_files: int = 200) -> Result[ProjectIndex]:
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        return Result.fail(f"Not a directory: {root}")

    start = time.monotonic()
    idx = ProjectIndex(root=str(root_path))

    py_files = sorted(
        (f for f in root_path.rglob("*.py") if not _should_skip(f)),
        key=lambda f: f.name,
    )[:max_files]

    for py_file in py_files:
        symbols = _parse_file(py_file, root_path)
        idx.symbols.extend(symbols)
        idx.files_scanned += 1
        idx.file_summaries[str(py_file.relative_to(root_path))] = {
            "classes": sum(1 for s in symbols if s.kind == "class"),
            "functions": sum(1 for s in symbols if s.kind == "function"),
            "imports": sum(1 for s in symbols if s.kind == "import"),
        }

    idx.duration = time.monotonic() - start
    return Result.ok(idx)
