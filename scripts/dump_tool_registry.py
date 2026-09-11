#!/usr/bin/env python3
"""dump 当前 CodingRuntime 注册表黄金快照（T3/T4 前后对照用）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path("/home/ai/lingclaude")
sys.path.insert(0, str(ROOT))

from lingclaude.engine.coding import CodingRuntime  # noqa: E402

rt = CodingRuntime()
out = []
for t in rt.registry.list_tools():
    h = rt.registry._resolve_handler(t)
    out.append({
        "name": t.name,
        "description": t.description,
        "parameters": t.parameters,
        "security_scope": t.security_scope,
        "is_concurrency_safe": t.is_concurrency_safe,
        "handler_direct": t.handler is not None,
        "handler_name": t.handler_name,
        "handler_resolved": getattr(h, "__name__", None) if h else None,
    })
out.sort(key=lambda x: x["name"])
dest = Path(sys.argv[1])
dest.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
print(f"OK: {len(out)} tools → {dest}")
