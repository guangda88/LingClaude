"""AST-based editor for lingclaude.

Replaces function/class bodies at the AST level instead of text matching.
This avoids the common failure mode of old_text/new_text where whitespace
or formatting differences cause the edit to fail.

Operations:
- replace_function: Replace the body of a specific function
- replace_method: Replace the body of a method in a class
- replace_class: Replace the body of a class (all methods)
"""
from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lingclaude.core.types import Result


@dataclass(frozen=True)
class ASTEditResult:
    file: str
    target: str  # function/class name
    kind: str  # "function", "method", "class"
    lines_old: int
    lines_new: int
    success: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "target": self.target,
            "kind": self.kind,
            "lines_old": self.lines_old,
            "lines_new": self.lines_new,
            "success": self.success,
            "message": self.message,
        }


def _find_function_node(
    tree: ast.Module,
    name: str,
    class_name: str | None = None,
    occurrence: int = 1,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    count = 0
    if class_name:
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                for item in ast.iter_child_nodes(node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if item.name == name:
                            count += 1
                            if count == occurrence:
                                return item
    else:
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == name:
                    count += 1
                    if count == occurrence:
                        return node
    return None


def _find_class_node(
    tree: ast.Module,
    name: str,
) -> ast.ClassDef | None:
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _get_indent(source: str, lineno: int) -> str:
    lines = source.split("\n")
    if lineno < 1 or lineno > len(lines):
        return "    "
    line = lines[lineno - 1]
    indent = ""
    for ch in line:
        if ch in (" ", "\t"):
            indent += ch
        else:
            break
    return indent


def _get_node_source_range(
    source: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> tuple[int, int]:
    lines = source.split("\n")
    start_line = node.lineno
    end_line = node.end_lineno or start_line

    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        body_start = node.body[0].lineno if node.body else start_line + 1
        decorator_end = start_line
        for dec in node.decorator_list:
            if dec.end_lineno and dec.end_lineno >= decorator_end:
                decorator_end = dec.end_lineno

        for i in range(decorator_end, body_start):
            if i < len(lines) and lines[i].strip().startswith(("def ", "async def ")):
                break

        return start_line, end_line

    return start_line, end_line


def _resolve_path(file_path: str) -> Result[Path]:
    """Validate and resolve file path, blocking traversal attacks."""
    p = Path(file_path)
    resolved = p.resolve()
    if ".." in p.parts:
        return Result.fail(f"路径不允许包含 '..': {file_path}")
    if not resolved.exists():
        return Result.fail(f"文件不存在: {file_path}")
    return Result.ok(resolved)


def replace_function_body(
    file_path: str,
    function_name: str,
    new_body: str,
    class_name: str | None = None,
    occurrence: int = 1,
) -> Result[ASTEditResult]:
    resolved = _resolve_path(file_path)
    if resolved.is_error:
        return resolved  # type: ignore[return-value]
    path = resolved.data
    if not path.exists():
        return Result.fail(f"文件不存在: {file_path}")

    try:
        source = path.read_text(encoding="utf-8")
    except Exception as e:
        return Result.fail(f"读取失败: {e}")

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return Result.fail(f"语法错误: {e}")

    kind = "method" if class_name else "function"
    target_name = f"{class_name}.{function_name}" if class_name else function_name

    node = _find_function_node(tree, function_name, class_name, occurrence)
    if node is None:
        return Result.fail(f"未找到 {kind}: {target_name}")

    start_line, end_line = _get_node_source_range(source, node)
    lines = source.split("\n")

    base_indent = _get_indent(source, node.body[0].lineno if node.body else start_line)

    dedented = textwrap.dedent(new_body)
    new_lines = []
    for line in dedented.split("\n"):
        if line.strip():
            new_lines.append(base_indent + line)
        else:
            new_lines.append("")

    header_lines = lines[start_line - 1 : (node.body[0].lineno - 1 if node.body else start_line)]

    old_body_start = node.body[0].lineno - 1 if node.body else start_line
    old_body_lines = end_line - old_body_start

    result_lines = lines[: start_line - 1] + header_lines + new_lines + lines[end_line:]
    new_source = "\n".join(result_lines)

    try:
        ast.parse(new_source)
    except SyntaxError as e:
        return Result.fail(f"替换后语法错误: {e}")

    try:
        path.write_text(new_source, encoding="utf-8")
    except Exception as e:
        return Result.fail(f"写入失败: {e}")

    return Result.ok(ASTEditResult(
        file=file_path,
        target=target_name,
        kind=kind,
        lines_old=old_body_lines,
        lines_new=len(new_lines),
        success=True,
        message=f"已替换 {target_name} ({old_body_lines} → {len(new_lines)} 行)",
    ))


def list_functions(file_path: str) -> Result[list[dict[str, Any]]]:
    resolved = _resolve_path(file_path)
    if resolved.is_error:
        return resolved  # type: ignore[return-value]
    path = resolved.data
    if not path.exists():
        return Result.fail(f"文件不存在: {file_path}")

    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception as e:
        return Result.fail(f"解析失败: {e}")

    functions: list[dict[str, Any]] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            for item in ast.iter_child_nodes(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = ", ".join(
                        a.arg for a in item.args.args if a.arg not in ("self", "cls")
                    )
                    functions.append({
                        "name": f"{node.name}.{item.name}",
                        "kind": "method",
                        "line": item.lineno,
                        "end_line": item.end_lineno,
                        "args": args,
                    })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ", ".join(a.arg for a in node.args.args)
            functions.append({
                "name": node.name,
                "kind": "function",
                "line": node.lineno,
                "end_line": node.end_lineno,
                "args": args,
            })

    return Result.ok(functions)
