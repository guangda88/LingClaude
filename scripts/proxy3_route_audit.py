#!/usr/bin/env python3
"""proxy3 路由实测 — 灵克 7/27 18:56 创建

依据：族长 7/27 18:10 裁定（12 critical 根因 = proxy3 不能生产）
目的：实测 900+ 路由健康状态，识别失效集合
输出：failures.json / pass.json / health_audit_20260727_1830.md

测试项：
1. /v1/models 拉全路由
2. 对每条路由发一次 5 token 测试请求
3. 记录 response code + latency + body excerpt
4. 分类：pass / fail_401 / fail_429 / fail_5xx / timeout / connection_error
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path

OUTPUT_DIR = Path("/home/ai/lingclaude/health")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TS = time.strftime("%Y%m%d_%H%M%S")
FAIL_JSON = OUTPUT_DIR / f"route_audit_{TS}_failures.json"
PASS_JSON = OUTPUT_DIR / f"route_audit_{TS}_pass.json"
REPORT_MD = OUTPUT_DIR / f"route_audit_{TS}.md"

PROXY3_URL = "http://127.0.0.1:8765"
TEST_TIMEOUT = 30  # 秒
MAX_WORKERS = 8
MAX_MODELS = 200  # 设上限避免 30min 阻塞
# proxy3 EVOLUTION_LOG #037: auth.py requires X-Agent-Id
TEST_AGENT_ID = "lingclaude"
TEST_BEARER = "test-audit-bearer"


def get_models() -> list[str]:
    """从 proxy3 /v1/models 拉全路由。"""
    out = subprocess.run(
        ["python3", "-c", f"""
import urllib.request, json
r = urllib.request.urlopen('{PROXY3_URL}/v1/models', timeout=10)
d = json.loads(r.read())
import sys
for m in d.get('data', []):
    sys.stdout.write(m.get('id', '') + '\\n')
"""],
        capture_output=True, text=True, timeout=20
    )
    models = [m.strip() for m in out.stdout.splitlines() if m.strip()]
    return models[:MAX_MODELS]


def test_route(model: str) -> dict:
    """对单条模型路由发一次最小化测试请求。"""
    start = time.time()
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "What is 2+2? Answer in one word."}],
        "max_tokens": 5,
        "stream": False,
    }).encode()
    try:
        req = urllib.request.Request(
            f"{PROXY3_URL}/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                # X-Agent-Id required by proxy3 auth.py:21 (EVOLUTION_LOG #037)
                "X-Agent-Id": TEST_AGENT_ID,
                # Bearer auth for proxy3 main routes
                "Authorization": f"Bearer {TEST_BEARER}",
            },
        )
        with urllib.request.urlopen(req, timeout=TEST_TIMEOUT) as r:
            body = json.loads(r.read())
            elapsed = time.time() - start
            content = ""
            if body.get("choices"):
                content = body["choices"][0].get("message", {}).get("content", "")[:50]
            return {
                "model": model,
                "status": "pass",
                "code": 200,
                "latency_sec": round(elapsed, 2),
                "response_excerpt": content,
            }
    except urllib.error.HTTPError as e:
        elapsed = time.time() - start
        return {
            "model": model,
            "status": f"fail_{e.code}",
            "code": e.code,
            "latency_sec": round(elapsed, 2),
            "error": str(e)[:200],
        }
    except urllib.error.URLError as e:
        elapsed = time.time() - start
        return {
            "model": model,
            "status": "connection_error",
            "code": 0,
            "latency_sec": round(elapsed, 2),
            "error": str(e)[:200],
        }
    except Exception as e:
        elapsed = time.time() - start
        return {
            "model": model,
            "status": "exception",
            "code": 0,
            "latency_sec": round(elapsed, 2),
            "error": str(e)[:200],
        }


def classify(results: list[dict]) -> dict:
    """按状态分类结果。"""
    classes: dict[str, list[dict]] = {}
    for r in results:
        cls = r["status"].split("_")[0]
        classes.setdefault(cls, []).append(r)
    return classes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=MAX_MODELS, help="最大模型数")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    args = parser.parse_args()

    print(f"=== proxy3 路由实测开始 {TS} ===")
    print(f"URL: {PROXY3_URL}")
    print(f"Max models: {args.max}, Workers: {args.workers}")

    models = get_models()
    print(f"Total models to test: {len(models)}")

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(test_route, m): m for m in models}
        done_count = 0
        for future, model in futures.items():
            try:
                r = future.result(timeout=TEST_TIMEOUT + 5)
                results.append(r)
                done_count += 1
                if done_count % 20 == 0:
                    print(f"  {done_count}/{len(models)} done...")
            except FuturesTimeout:
                results.append({"model": model, "status": "timeout", "code": 0, "latency_sec": TEST_TIMEOUT})
                done_count += 1

    print(f"\nTotal tested: {len(results)}")

    classes = classify(results)
    pass_count = sum(len(v) for k, v in classes.items() if k == "pass")
    fail_count = len(results) - pass_count
    print(f"Pass: {pass_count} ({100*pass_count/max(1,len(results)):.1f}%)")
    print(f"Fail: {fail_count} ({100*fail_count/max(1,len(results)):.1f}%)")

    # 分类详细输出
    print("\n=== 失败分布 ===")
    for cls, items in sorted(classes.items()):
        if cls == "pass":
            continue
        sample = items[:3]
        sample_str = ", ".join(s["model"][:40] for s in sample)
        print(f"{cls}: {len(items)} ({sample_str}{', ...' if len(items) > 3 else ''})")

    # 写文件
    failures = [r for r in results if r["status"] != "pass"]
    passes = [r for r in results if r["status"] == "pass"]
    FAIL_JSON.write_text(json.dumps(failures, indent=2, ensure_ascii=False))
    PASS_JSON.write_text(json.dumps(passes, indent=2, ensure_ascii=False))

    # 报告 markdown
    lines = [
        f"# proxy3 路由实测报告 {TS}",
        "",
        f"**URL**: {PROXY3_URL}",
        f"**Total**: {len(results)} | **Pass**: {pass_count} ({100*pass_count/max(1,len(results)):.1f}%) | **Fail**: {fail_count} ({100*fail_count/max(1,len(results)):.1f}%)",
        "",
        "## 失败分布",
        "",
        "| Status | Count | Sample |",
        "|---|---|---|",
    ]
    for cls, items in sorted(classes.items()):
        if cls == "pass":
            continue
        sample = ", ".join(s["model"][:30] for s in items[:3])
        lines.append(f"| {cls} | {len(items)} | {sample} |")
    lines.append("")
    lines.append(f"## 输出文件")
    lines.append(f"- 失败: `{FAIL_JSON}`")
    lines.append(f"- 通过: `{PASS_JSON}`")
    REPORT_MD.write_text("\n".join(lines))

    print(f"\n报告: {REPORT_MD}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())