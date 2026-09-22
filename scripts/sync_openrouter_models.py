#!/usr/bin/env python3
"""同步 OpenRouter 模型列表到 lingcode/config.json"""

import json
import os
import urllib.request
from pathlib import Path

CONFIG_PATH = Path("/home/ai/lingcode/config.json")
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

def fetch_openrouter_models() -> list[str]:
    """拉取 OpenRouter 模型 ID 列表"""
    req = urllib.request.Request(OPENROUTER_MODELS_URL, headers={"User-Agent": "lingclaude-sync"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    return [m["id"] for m in data.get("data", []) if m.get("id")]

def update_config(models: list[str]) -> None:
    """把模型列表写入 config.json"""
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    provider = config["routing"]["providers"].get("openrouter", {})
    provider["models"] = models
    # 保持默认模型（若列表为空则清空）
    if models and provider.get("model") not in models:
        provider["model"] = models[0]
    config["routing"]["providers"]["openrouter"] = provider
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

def main() -> int:
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("❌ 缺少 OPENROUTER_API_KEY 环境变量")
        return 1
    try:
        models = fetch_openrouter_models()
        update_config(models)
        print(f"✅ 同步完成，共 {len(models)} 个模型")
        return 0
    except Exception as e:
        print(f"❌ 同步失败: {e}")
        return 1

if __name__ == "__main__":
    exit(main())
