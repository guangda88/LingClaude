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
    """子进程执行插件方法。返回 {"ok": bool, "data"|"error": ...}。"""
    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        return {"ok": False, "error": f"manifest 不存在: {manifest_path}"}
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
