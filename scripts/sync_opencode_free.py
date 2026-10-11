#!/usr/bin/env python3
"""opencode free 额度同步 — 与官方 models 清单定时对齐，差量补入 proxy3 routes。

数据源：`opencode models` CLI（官方 zen 档案）
落点：/home/ai/llm-proxy/proxy3_py/routes.json（tier=free, provider=opencode）
原则：只增不删（官方下线的模型留原地，由 free_pool 健康分自然淘汰），
写入前自动备份 routes.json。幂等：已存在的 key 跳过。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROUTES = Path("/home/ai/llm-proxy/proxy3_py/routes.json")
TPL_KEY = "nemotron-3.5-lightning-free@opencode"
NOTE = "Zen免费档 (opencode free 定时同步, {date}, 灵克)"


def main() -> int:
    out = subprocess.run(
        ["opencode", "models"], capture_output=True, text=True, timeout=60
    )
    if out.returncode != 0:
        print(f"opencode models 失败: {out.stderr[:200]}", file=sys.stderr)
        return 1
    free = sorted(
        ln.strip().removeprefix("opencode/")
        for ln in out.stdout.splitlines()
        if ln.startswith("opencode/") and "free" in ln
    )

    d = json.loads(ROUTES.read_text())
    tpl = d.get(TPL_KEY)
    if not tpl:
        print(f"模板 {TPL_KEY} 不存在，中止", file=sys.stderr)
        return 1

    import datetime
    added = []
    for m in free:
        key = f"{m}@opencode"
        if key in d:
            continue
        r = dict(tpl)
        r["upstream_model"] = m
        r["_note"] = NOTE.format(date=datetime.date.today().isoformat())
        d[key] = r
        added.append(key)

    if added:
        shutil.copy(ROUTES, str(ROUTES) + ".bak.sync_" + datetime.date.today().strftime("%Y%m%d"))
        ROUTES.write_text(json.dumps(d, ensure_ascii=False, indent=2))
    print(f"free 总数={len(free)} 新增={len(added)} {added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
