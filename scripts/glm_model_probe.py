#!/usr/bin/env python3
"""GLM 模型可用性探测 — 逐一测试每个模型，打印 ✅/❌"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

GLM_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODELS = [
    "glm-4.7",
    "glm-4.5-air",
    "glm-4-flash",
    "glm-4.5",
    "glm-4.6",
    "glm-5",
    "glm-5-turbo",
    "glm-5.1",
]


def load_keys() -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    env_path = Path(os.environ.get("GLM_ENV_PATH", ".env"))
    if not env_path.exists():
        print("错误: 找不到 .env 文件", file=sys.stderr)
        sys.exit(1)
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if k in ("GLM_CODING_PLAN_KEY", "GLM_API_KEY", "GLM_47_CC_KEY"):
            keys.append((k, v))
    return keys


def probe_model(api_key: str, model: str) -> bool:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
    }, ensure_ascii=False).encode()
    req = urllib.request.Request(
        GLM_URL, data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            json.loads(resp.read())
        return True
    except Exception:
        return False


def main():
    keys = load_keys()
    if not keys:
        print("错误: 未找到 GLM API Key", file=sys.stderr)
        sys.exit(1)

    print(f"检测到 {len(keys)} 个 Key: {', '.join(k for k, _ in keys)}\n")

    for model in MODELS:
        for key_name, key_val in keys:
            ok = probe_model(key_val, model)
            icon = "✅" if ok else "❌"
            print(f"  {icon}  {model:<16} (via {key_name})")
            if ok:
                break
        else:
            print(f"  ❌  {model:<16} (all keys failed)")

    print()


if __name__ == "__main__":
    main()
