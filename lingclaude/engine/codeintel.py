"""P1-6: Code intelligence — trace_callers / trace_callees / find_references / blast_radius.

优先通过 subprocess 调用 AtomCode CLI (`atomcode codeintel`)，纯 Python AST 实现作为 fallback。
AtomCode CLI 接口（确认于 2026-08-21）：
  atomcode codeintel trace_callers <file> <sym> --json
  atomcode codeintel trace_callees <file> <sym> --json
  atomcode codeintel blast_radius <file> --json
"""

from __future__ import annotations

import ast
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Location:
    file: str
    line: int
    col: int = 0
    symbol: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line, "col": self.col, "symbol": self.symbol}


@dataclass
class BlastResult:
    file: str
    direct_deps: list[str] = field(default_factory=list)
    all_deps: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Call graph builder (Python AST only)
# ---------------------------------------------------------------------------


class PythonCallGraph:
    """Build call graph from Python source files using AST.

    For each function, records:
    - callees: functions it calls (by name)
    - callers: functions that call it (by name)
    """

    def __init__(self, root: Path):
        self.root = root
        self._defs: dict[str, dict[str, Any]] = {}  # file -> {sym_name -> node_lineno}
        self._calls: dict[str, set[str]] = {}  # "file:sym" -> {callee_names}
        self._callers: dict[str, set[str]] = {}  # "file:sym" -> {caller_names}
        self._file_calls: dict[str, set[str]] = {}  # file -> {callee_files}
        self._build()

    def _build(self) -> None:
        for py_file in self.root.rglob("*.py"):
            if self._should_skip(py_file):
                continue
            self._scan_file(py_file)

    def _should_skip(self, path: Path) -> bool:
        skip = {"__pycache__", ".git", ".venv", "venv", "node_modules", "build", "dist", ".tox"}
        return any(part in skip for part in path.parts)

    def _scan_file(self, path: Path) -> None:
        rel = str(path.relative_to(self.root))
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            return

        local_defs: dict[str, int] = {}  # local symbol -> lineno

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                local_defs[name] = node.lineno
                key = f"{rel}:{name}"
                self._defs.setdefault(rel, {})[name] = node.lineno
                self._calls.setdefault(key, set())

            elif isinstance(node, ast.ClassDef):
                local_defs[node.name] = node.lineno
                self._defs.setdefault(rel, {}).setdefault(node.name, node.lineno)

        # Second pass: find calls within this file
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fname = node.name
                func_key = f"{rel}:{fname}"
                for called in ast.walk(node):
                    if isinstance(called, ast.Name) and isinstance(called.ctx, ast.Load):
                        callee = called.id
                        self._calls.setdefault(func_key, set()).add(callee)
                        # Record caller relationship
                        self._callers.setdefault(f"{rel}:{callee}", set()).add(fname)
                    elif isinstance(called, ast.Attribute):
                        # method calls: self.method() etc.
                        self._calls.setdefault(func_key, set()).add(called.attr)
                        self._callers.setdefault(f"{rel}:{called.attr}", set()).add(fname)

    def find_callees(self, file: str, symbol: str) -> list[Location]:
        """Find functions/variables referenced by `symbol` in `file`."""
        key = f"{file}:{symbol}"
        callee_names = self._calls.get(key, set())
        results = []
        for fname, defs in self._defs.items():
            for sym, lineno in defs.items():
                if sym in callee_names:
                    results.append(Location(file=fname, line=lineno, symbol=sym))
        return results

    def find_callers(self, file: str, symbol: str) -> list[Location]:
        """Find functions that call `symbol` in `file`."""
        key = f"{file}:{symbol}"
        caller_names = self._callers.get(key, set())
        results = []
        for fname, defs in self._defs.items():
            for sym, lineno in defs.items():
                if sym in caller_names:
                    results.append(Location(file=fname, line=lineno, symbol=sym))
        return results

    def find_references(self, file: str, symbol: str) -> list[Location]:
        """Find all references to `symbol` across the project (def + all calls)."""
        results = []
        # Include definition
        defs = self._defs.get(file, {})
        if symbol in defs:
            results.append(Location(file=file, line=defs[symbol], symbol=symbol))
        # Include callers and callees
        results.extend(self.find_callers(file, symbol))
        results.extend(self.find_callees(file, symbol))
        return results

    def blast_radius(self, file: str) -> BlastResult:
        """Find direct and transitive dependencies of `file`."""
        # Direct imports
        direct = list(self._file_calls.get(file, set()))
        # Transitive (BFS)
        all_deps: set[str] = set(direct)
        queue = list(direct)
        while queue:
            next_file = queue.pop(0)
            deps = self._file_calls.get(next_file, set())
            for d in deps:
                if d not in all_deps:
                    all_deps.add(d)
                    queue.append(d)
        return BlastResult(file=file, direct_deps=direct, all_deps=sorted(all_deps))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _atomcode_call(args: list[str], timeout: int = 30) -> list[dict[str, Any]]:
    """Try AtomCode CLI; return [] on failure (fallback will be used)."""
    try:
        r = subprocess.run(
            ["atomcode"] + args,
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode == 0:
            return [json.loads(l) for l in r.stdout.strip().splitlines() if l]
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return []


def trace_callers(file: str, symbol: str, root: str | Path) -> list[dict[str, Any]]:
    """对标 AtomCode trace_callers: 优先 subprocess，fallback 纯 AST。"""
    result = _atomcode_call(["codeintel", "trace_callers", file, symbol, "--json"])
    if result:
        return result
    g = PythonCallGraph(Path(root))
    return [loc.to_dict() for loc in g.find_callers(file, symbol)]


def trace_callees(file: str, symbol: str, root: str | Path) -> list[dict[str, Any]]:
    """对标 AtomCode trace_callees: 优先 subprocess，fallback 纯 AST。"""
    result = _atomcode_call(["codeintel", "trace_callees", file, symbol, "--json"])
    if result:
        return result
    g = PythonCallGraph(Path(root))
    return [loc.to_dict() for loc in g.find_callees(file, symbol)]


def find_references(file: str, symbol: str, root: str | Path) -> list[dict[str, Any]]:
    """对标 AtomCode find_references: fallback 纯 AST（AtomCode find_refs CLI 待补充）。"""
    result = _atomcode_call(["codeintel", "find_references", file, symbol, "--json"])
    if result:
        return result
    g = PythonCallGraph(Path(root))
    return [loc.to_dict() for loc in g.find_references(file, symbol)]


def blast_radius(file: str, root: str | Path) -> dict[str, Any]:
    """对标 AtomCode blast_radius: 优先 subprocess，fallback 纯 AST。"""
    result = _atomcode_call(["codeintel", "blast_radius", file, "--json"])
    if result:
        return result
    g = PythonCallGraph(Path(root))
    r = g.blast_radius(file)
    return {"file": r.file, "direct": r.direct_deps, "all_deps": r.all_deps}
