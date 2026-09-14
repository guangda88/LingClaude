"""P2: 子进程插件运行时（warm 级热拔插骨架）。

插件代码跑独立子进程（崩溃/死循环不拖垮主进程），通过 JSON-RPC
(stdin/stdout) 暴露 execute；换插片 = 杀掉旧进程起新进程（进程级 swap）。

用法:
    result = run_plugin_subprocess(manifest, "execute", {"name": "edit", ...})
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# 子进程入口：加载 manifest → 实例化插件 → 调 method → JSON 输出
_PLUGIN_RUNNER_ENTRY = """\
import importlib.util, json, sys

def _main():
    manifest_path, req_json = sys.argv[1], sys.argv[2]
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    req = json.loads(req_json)
    module_path, _, class_name = manifest["entry"].partition(":")
    spec = importlib.util.spec_from_file_location("_plugin_subproc", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    instance = getattr(module, class_name)()
    try:
        result = getattr(instance, req.get("method", "execute"))(**req.get("args", {}))
        if hasattr(result, "is_ok"):
            data = getattr(result, "data", None) if result.is_ok else None
            out = {"ok": bool(result.is_ok),
                   "data": _serialize(data),
                   "error": getattr(result, "error", "") if not result.is_ok else ""}
        else:
            out = {"ok": True, "data": _serialize(result), "error": ""}
        print(json.dumps({"ok": True, "data": out}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))

def _serialize(obj):
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    return obj

_main()
"""


def run_plugin_subprocess(
    manifest_path: str | Path,
    method: str = "execute",
    args: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """子进程执行插件方法。返回 {"ok": bool, "data"|"error": ...}。

    2026-09-14 (warm 试点): 支持两种 args 格式 ——
      A) 纯参数: {"path": "...", "offset": 1}  → 直接透传 execute
      B) MCP 外壳: {"name": "read", "arguments": {"path": ...}} → 剥离外壳，
         只把 arguments 透传给 execute（与 ToolRegistry 的 MCP 调用流对齐，
         使子进程插件可无缝接 mcp_proxy stdio transport）。
    """
    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        return {"ok": False, "error": f"manifest 不存在: {manifest_path}"}
    # MCP 外壳归一：{"name":..., "arguments": {...}} → {"path": ...}
    if args and "name" in args and isinstance(args.get("arguments"), dict):
        args = args["arguments"]
    req = {"method": method, "args": args or {}}
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PLUGIN_RUNNER_ENTRY, str(manifest_path), json.dumps(req)],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"插件子进程超时（>{timeout}s）"}
    if proc.returncode != 0:
        return {"ok": False, "error": f"插件子进程退出码 {proc.returncode}: {proc.stderr.strip()[-500:]}"}
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        return {"ok": False, "error": f"子进程输出非 JSON: {exc}"}


# ============================================================================
# warm 试点（P2）: MCP stdio 服务模式 —— 插件子进程走标准 MCP 协议
#
# 目标：使插件成为「标准 MCP stdio server」，可被 mcp_proxy.register_server(
# transport="stdio") + mcp_client.MCPStdioClient 连接池直接调用 —— 子进程隔离
# （warm 级）+ 标准协议（initialize/tools/list/tools/call）。
#
# 协议：行式 JSON-RPC 2.0（与 mcp_client.MCPStdioClient 完全对称）：
#   <- {"jsonrpc":"2.0","id":1,"method":"initialize","params":{...}}
#   -> {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":...,"capabilities":{}}}
#   <- {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
#   -> {"jsonrpc":"2.0","id":2,"result":{"tools":[{name,description,inputSchema},...]}}
#   <- {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"read","arguments":{...}}}
#   -> {"jsonrpc":"2.0","id":3,"result":{"content":[{"type":"text","text":...}]}}
# ============================================================================

_PLUGIN_STDIO_ENTRY = """\
import importlib.util, json, sys

def _load_plugin(manifest):
    module_path, _, class_name = manifest["entry"].partition(":")
    spec = importlib.util.spec_from_file_location("_plugin_stdio", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, class_name)()

def _serialize(obj):
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    if hasattr(obj, "data"):
        return _serialize(getattr(obj, "data"))
    return obj

def _result_text(obj):
    # 统一转成文本内容（与 mcp_client.call_tool 的 text 提取对齐）
    if isinstance(obj, dict) and "error" in obj and set(obj) <= {"error"}:
        return obj["error"]
    return json.dumps(_serialize(obj), ensure_ascii=False, default=str)

def _main():
    manifest_path = _MANIFEST_PATH
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    plugin = _load_plugin(manifest)
    provides = manifest.get("provides") or []
    tool_name = provides[0] if provides else "execute"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": True}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        rid = req.get("id")
        method = req.get("method")
        if method == "initialize":
            out = {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": req.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {},
                "serverInfo": {"name": manifest["name"], "version": manifest.get("version", "1.0.0")},
            }}
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            tools = [{"name": p, "description": manifest.get("description", ""),
                      "inputSchema": input_schema} for p in provides]
            out = {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}
        elif method == "tools/call":
            params = req.get("params", {})
            name = params.get("name") or tool_name
            arguments = params.get("arguments") or {}
            try:
                # 只透传 arguments（MCP 外壳归一）：name 是路由信息，不进 execute 参数。
                # 与 run_plugin_subprocess 的 B 格式处理对齐 —— 插件 execute 只收真实参数。
                value = plugin.execute(**arguments)
                text = _result_text(value)
                out = {"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": text}]}}
            except Exception as exc:  # noqa: BLE001 — 子进程内异常转 JSON-RPC 错误
                out = {"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    "isError": True}}
        else:
            out = {"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32601, "message": f"Method not found: {method}"}}
        print(json.dumps(out, ensure_ascii=False), flush=True)

_main()
"""


def serve_plugin_stdio(manifest_path: str | Path) -> None:
    """插件 MCP stdio 服务模式入口（被 mcp_proxy stdio 命令调用）。

    用法（register_server command）:
        [sys.executable, "-c", "from lingclaude.engine.plugin_runner import serve_plugin_stdio; serve_plugin_stdio('lingclaude/plugins/tools/read/manifest.plugin.json')"]
    """
    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        sys.stderr.write(f"manifest 不存在: {manifest_path}\n")
        sys.exit(1)
    globals_dict = {"__name__": "__main__", "_MANIFEST_PATH": str(manifest_path)}
    exec(compile(_PLUGIN_STDIO_ENTRY, "<plugin_stdio>", "exec"), globals_dict)
