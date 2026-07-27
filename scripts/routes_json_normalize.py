#!/usr/bin/env python3
"""routes.json 归一化脚本 — 灵克 7/27 20:15

依据：
- 议程 5 R1 第 7 项前置 (c): routes.json 7 个 no route 归一化
- 族长 7/27 18:10 裁定：议程 5 优先
- proxy3 实测 200/200 chat 全 403（根因：测试渠道不通 + 部分路由本身不可用）

策略：
1. 拉 proxy3 /v1/models 取全部 1647 models
2. 检查 routes.json（默认 /home/ai/llm-proxy/proxy3_py/routes.json）
3. 对每个 model，找匹配的 route；找不匹配的 = no route
4. 输出：归一化候选 + 删除建议
5. 不实际修改 routes.json（需灵通 owner 审批）

执行：需要 PROXY3_ROUTES 环境变量或默认路径。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROXY3_URL = "http://127.0.0.1:8765"
DEFAULT_ROUTES = Path("/home/ai/llm-proxy/proxy3_py/routes.json")
OUTPUT_DIR = Path("/home/ai/lingclaude/health")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_models() -> list[str]:
    """从 proxy3 /v1/models 拉全部路由。"""
    out = subprocess.run(
        ["python3", "-c", f"""
import urllib.request, json, sys
try:
    r = urllib.request.urlopen('{PROXY3_URL}/v1/models', timeout=10)
    d = json.loads(r.read())
    for m in d.get('data', []):
        sys.stdout.write(m.get('id', '') + '\\n')
except Exception as e:
    sys.stderr.write(str(e) + '\\n')
    sys.exit(1)
"""],
        capture_output=True, text=True, timeout=20
    )
    if out.returncode != 0:
        print(f"ERROR: {out.stderr}")
        sys.exit(1)
    return [m.strip() for m in out.stdout.splitlines() if m.strip()]


def load_routes(routes_file: Path) -> dict[str, Any]:
    """加载 proxy3 路由配置。"""
    if not routes_file.exists():
        print(f"WARN: routes.json not found at {routes_file}", file=sys.stderr)
        return {"providers": []}
    try:
        return json.loads(routes_file.read_text())
    except Exception as e:
        print(f"ERROR: cannot parse {routes_file}: {e}", file=sys.stderr)
        return {"providers": []}


def find_no_route(models: list[str], routes: dict) -> list[dict]:
    """对每个 model，找匹配的 route；找不匹配的 = no route。

    routes.json 是 {model_id: {api_key_env, provider, tier}} 的字典（key 是 model id）。
    直接判断 model 是否是 routes.json 的 key 即可。
    """
    # routes 是 {model_id: {config}}
    route_keys = set(routes.keys())

    no_route = []
    for m in models:
        # 直接匹配 routes.json key
        matched = m in route_keys
        no_route.append({"model": m, "matched": matched})

    return no_route


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--routes", type=Path, default=DEFAULT_ROUTES)
    parser.add_argument("--output-prefix", type=str, default="routes_json_normalize")
    args = parser.parse_args()

    print("=== routes.json 归一化分析 ===")
    print(f"proxy3 URL: {PROXY3_URL}")
    print(f"routes.json: {args.routes}")

    models = get_models()
    print(f"Total models: {len(models)}")

    routes = load_routes(args.routes)
    print(f"Loaded {len(routes.get('providers', []))} providers from routes.json")

    normalized = find_no_route(models, routes)
    matched = sum(1 for r in normalized if r["matched"])
    no_route_count = sum(1 for r in normalized if not r["matched"])

    print(f"Matched: {matched} ({100*matched/max(1,len(normalized)):.1f}%)")
    print(f"No route: {no_route_count} ({100*no_route_count/max(1,len(normalized)):.1f}%)")

    # 输出 no_route 列表
    no_route_list = [r["model"] for r in normalized if not r["matched"]]

    output_json = OUTPUT_DIR / f"{args.output_prefix}.json"
    output_json.write_text(json.dumps({
        "ts": __import__("time").strftime("%Y%m%d_%H%M%S"),
        "proxy3_url": PROXY3_URL,
        "routes_json": str(args.routes),
        "total_models": len(models),
        "matched": matched,
        "no_route_count": no_route_count,
        "no_route_models": no_route_list[:50],  # 前 50
    }, indent=2, ensure_ascii=False))

    print(f"\n=== 归一化建议 ===")
    print(f"no route 模型（建议处理）：")
    for m in no_route_list[:20]:
        print(f"  - {m}")
    if len(no_route_list) > 20:
        print(f"  ... +{len(no_route_list)-20} 更多")

    print(f"\n=== 输出 ===")
    print(f"  JSON: {output_json}")
    print(f"  报告: {output_json.with_suffix('.md')}")

    # Markdown 报告
    md = OUTPUT_DIR / f"{args.output_prefix}.md"
    md.write_text(f"""# routes.json 归一化建议

**生成时间**: {__import__("time").strftime("%Y-%m-%d %H:%M:%S")}
**proxy3 URL**: {PROXY3_URL}
**routes.json**: `{args.routes}`

## 统计

- 总模型数: {len(models)}
- 已匹配: {matched} ({100*matched/max(1,len(normalized)):.1f}%)
- 无路由: {no_route_count} ({100*no_route_count/max(1,len(normalized)):.1f}%)

## 归一化建议

### 删除候选（无路由）
- 路由表里无对应 provider 的模型，建议删除或合并

详见 `{output_json}`
""")

    return 0 if no_route_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())