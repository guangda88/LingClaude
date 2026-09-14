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
    # MCP 外壳归一：{"name":..., "arguments": {...}} → 只透传 arguments。
    # 注意与 serve_plugin_stdio 的差异：stdio tools/call 按工具名路由（透传 name
    # 作位置参数给 execute 分派）；run_plugin_subprocess 的语义是「调 execute 方法
    # 本身」（method 参数已指定），name 只是 MCP 外壳信息，不透传（read 等插件
    # execute(**kwargs) 会把 kwargs 原样转发给底层工具，传 name 会炸）。
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
                # 透传 name 作为第一个位置参数 + arguments 关键字：name 是路由信息。
                # 对 execute(name, **kwargs) 型插件（file_ops/git/web/ast）按名分派；
                # 对 execute(*args, **kwargs) 型插件（bash/read）name 进 args 被忽略。
                # 2026-09-14 修正：此前只透传 arguments 不传 name，导致按名分派型
                # 插件经 stdio 调用时 name 落默认值（git 永远 git_status、web 永远
                # web_search、ast 永远 list_functions、file_ops 永远 edit）——路由失效。
                value = plugin.execute(name, **arguments)
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


# ============================================================================
# P2 (warm 试点接线): 插件 → 标准 MCP stdio server → 连接池
#
# 使插件可经 mcp_proxy.register_server(transport="stdio") + 连接池调用：
#   register_plugin_server(manifest)  → register_server(key, ..., transport="stdio",
#                                    command=[python, -c, serve_plugin_stdio(manifest)])
#   call_plugin_server(key, name, args) → 连接池 stdio client → tools/call
#
# warm 语义：子进程隔离（崩溃/死循环不拖垮主进程）+ 连接池复用（避免每次
# spawn 子进程的成本）+ 换插片 = unregister + register（旧连接池条目失效重连）。
# ============================================================================

def plugin_server_command(manifest_path: str | Path) -> list[str]:
    """构造启动插件 stdio server 的 command（供 register_server 使用）。"""
    import sys

    return [
        sys.executable, "-c",
        "from lingclaude.engine.plugin_runner import serve_plugin_stdio; "
        f"serve_plugin_stdio('{Path(manifest_path).as_posix()}')",
    ]


def register_plugin_server(manifest_path: str | Path) -> str | None:
    """把插件注册为标准 MCP stdio server，返回 server key（失败返回 None）。

    幂等：同 key 已注册 → 直接返回 key（不重复注册）。
    """
    import json

    from lingclaude.engine.mcp_proxy import register_server

    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    name = data.get("name")
    provides = data.get("provides") or []
    if not name or not provides:
        return None
    key = f"plugin:{name}"
    try:
        from lingclaude.engine.mcp_proxy import find_server

        if find_server(key) is not None:
            return key
    except Exception:  # noqa: BLE001 — find_server 可能未导出
        pass
    register_server(
        key=key,
        name=name,
        agent_id="lingclaude",
        tools=tuple(provides),
        transport="stdio",
        command=plugin_server_command(manifest_path),
    )
    return key


def call_plugin_server(key: str, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """经 MCP stdio 连接池调用插件工具。

    key 用于日志/校验；实际调用按工具名经 mcp_proxy.call_tool 路由
    （find_server 按 tool_name 查 server，与 MCP 工具路由语义一致）。

    返回 {"ok": bool, "data"|"error": ...}（与 run_plugin_subprocess 同构）。
    """
    from lingclaude.engine.mcp_proxy import call_tool, find_server

    if find_server(tool_name) is None:
        return {"ok": False, "error": f"MCP server 未注册工具: {tool_name}（key={key}）"}
    result = call_tool(tool_name, **args)
    if result.is_error:
        return {"ok": False, "error": result.error}
    tc = result.data
    if not tc.success:
        return {"ok": False, "error": tc.error}
    return {"ok": True, "data": tc.output}
