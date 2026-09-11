#!/usr/bin/env python3
"""AST 机械提取 coding.py._setup_tools 的 ToolDefinition 注册 → 生成 specs 表。

纪律（同 P4.1 app.py 拆分）：不手抄、不凭记忆 —— 从 AST 原文提取，
literal_eval 仅接受纯字面量，任何非字面量立即失败退出（宁可不生成）。

支持两种既有模式：
  A) ToolDefinition(..., handler=self._x_handler, ...)          — 直传
  B) register_handler("<x>_handler", self._x_handler) + ToolDefinition(..., handler_name="<x>_handler")
     （cancel_job/plan_mode 已先行解耦的绑定对，从绑定行回溯 attr）
统一产出：handler_name == 工具名的收敛形态。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path("/home/ai/lingclaude/lingclaude/engine/coding.py")
OUT = Path("/home/ai/lingclaude/lingclaude/engine/tool_registration.py")

HEADER = '''"""工具注册 specs 表 — 由 scripts/extract_tool_specs.py 从 coding.py AST 机械提取。

T4（opencode 架构演进项）：注册数据与运行时解耦 —— coding.py 主干不再内联
32 条 ToolDefinition，改由 register_all_tools(registry, runtime) 消费本表。
G8 守卫锁定「主干零注册」。

字段说明：
- handler_attr: runtime 上的私有 handler 方法名（register_all_tools 负责按名绑定）
- concurrency_safe: True = 只读工具，可被 pipeline 并行调度（T1-3）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lingclaude.engine.tools import ToolDefinition


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler_attr: str
    security_scope: str
    concurrency_safe: bool = False


'''

FOOTER = '''

def register_all_tools(registry: Any, runtime: Any) -> None:
    """统一注册入口：specs 表 × runtime handlers → ToolRegistry。

    T3：全部走 handler_name 插片 —— ToolDefinition 不直持 Callable，
    定义（schema）与实现（handler）解耦。绑定失败立即 raise（不静默）。
    """
    for spec in SPECS:
        handler = getattr(runtime, spec.handler_attr, None)
        if handler is None or not callable(handler):
            raise AttributeError(
                f"tool '{spec.name}': runtime 缺 handler {spec.handler_attr}"
            )
        registry.register_handler(spec.name, handler)
        registry.register(
            ToolDefinition(
                name=spec.name,
                description=spec.description,
                parameters=spec.parameters,
                security_scope=spec.security_scope,
                is_concurrency_safe=spec.concurrency_safe,
                handler_name=spec.name,
            )
        )
'''


def _literal(node: ast.expr) -> object:
    try:
        return ast.literal_eval(node)
    except ValueError as e:
        sys.exit(f"FATAL: 非字面量参数 {ast.dump(node)[:120]}: {e}")


def main() -> int:
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    setup = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_setup_tools":
            setup = node
            break
    if setup is None:
        sys.exit("FATAL: _setup_tools 未找到")

    # 模式 B：register_handler("<name>", self._attr) 绑定对
    bound: dict[str, str] = {}
    for stmt in ast.walk(setup):
        if (isinstance(stmt, ast.Call) and isinstance(stmt.func, ast.Attribute)
                and stmt.func.attr == "register_handler" and len(stmt.args) >= 2):
            key = _literal(stmt.args[0])
            h = stmt.args[1]
            if isinstance(h, ast.Attribute) and isinstance(h.value, ast.Name):
                bound[str(key)] = h.attr

    specs: list[tuple[str, ...]] = []
    for stmt in ast.walk(setup):
        if not (isinstance(stmt, ast.Call)
                and isinstance(stmt.func, ast.Name)
                and stmt.func.id == "ToolDefinition"):
            continue
        kw = {k.arg: k.value for k in stmt.keywords}
        name = str(_literal(kw["name"]))
        desc = str(_literal(kw["description"]))
        params = _literal(kw["parameters"])
        if "handler" in kw:
            h = kw["handler"]
            if not (isinstance(h, ast.Attribute) and isinstance(h.value, ast.Name)
                    and h.value.id == "self"):
                sys.exit(f"FATAL: {name} handler 非自描述属性: {ast.dump(h)[:120]}")
            handler_attr = h.attr
        elif "handler_name" in kw:
            hn = str(_literal(kw["handler_name"]))
            handler_attr = bound.get(hn)
            if handler_attr is None:
                sys.exit(f"FATAL: {name} handler_name={hn} 找不到 register_handler 绑定")
        else:
            sys.exit(f"FATAL: {name} 既无 handler 也无 handler_name")
        scope = str(_literal(kw["security_scope"]))
        safe = bool(_literal(kw.get("is_concurrency_safe", ast.Constant(False))))
        specs.append((name, desc, repr(params), handler_attr, scope, str(safe)))

    lines = [HEADER, "SPECS: tuple[ToolSpec, ...] = ("]
    for name, desc, params, attr, scope, safe in specs:
        lines.append(
            "    ToolSpec(\n"
            f"        name={name!r},\n"
            f"        description={desc!r},\n"
            f"        parameters={params},\n"
            f"        handler_attr={attr!r},\n"
            f"        security_scope={scope!r},\n"
            f"        concurrency_safe={safe},\n"
            "    ),"
        )
    lines.append(")\n")
    lines.append(FOOTER)

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK: 提取 {len(specs)} 条 → {OUT}")
    for s in specs:
        print(f"  {s[0]:22s} handler={s[3]:30s} scope={s[4]:8s} safe={s[5]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
