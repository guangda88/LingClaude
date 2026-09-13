#!/bin/bash
# 用法: bash scripts/test_providers.sh
# 需先 source ~/.ling_keys.env

set -a
source ~/.ling_keys.env
set +a

python3 - "$@" << 'PYEOF'
import urllib.request, json, time, concurrent.futures, os

cfg = json.load(open('/home/ai/lingcode/config.json'))
providers = cfg['routing']['providers']

targets = [
    ("volcengine",  "Doubao-Seed-2.0-lite",       "volc_coding_plan / Doubao-Seed-2.0-lite"),
    ("volcengine",  "DeepSeek-V4-Flash",           "volc_coding_plan / DeepSeek-V4-Flash"),
    ("volcengine",  "GLM-5.3",                     "volc_coding_plan / GLM-5.3"),
    ("volcengine",  "Kimi-K3",                     "volc_coding_plan / Kimi-K3"),
    ("volcengine",  "MiniMax-M3",                  "volc_coding_plan / MiniMax-M3"),
    ("glm",         "glm-5.3",                     "GLM Coding Plan / GLM-5.3"),
    ("glm",         "glm-5.3-flash",               "GLM Coding Plan / GLM-5.3-Flash"),
    ("kimi",        "k3-256k",                     "Kimi Code / k3-256k"),
    ("kimi",        "kimi-for-coding",             "Kimi Code / kimi-for-coding"),
    ("agnes",       "agnes-3.0-flash",             "Agnes Token Plan / agnes-3.0-flash"),
    ("minimax",     "MiniMax-M3",                  "MiniMax Token Plan / MiniMax-M3"),
    ("mimo",        "mimo-v2.5",                   "MIMO Token Plan / mimo-v2.5"),
    ("mimo",        "mimo-v2.5-pro",               "MIMO Token Plan / mimo-v2.5-pro"),
]

key_map = {
    "volcengine": "VOLC_CODING_API_KEY",
    "glm":        "ZAI_API_KEY",
    "kimi":       "KIMI_API_KEY",
    "agnes":      "AGNES_ENTERPRISE_API_KEY",
    "minimax":    "MINIMAX_API_KEY",
    "mimo":       "XIAOMI_TOKEN_PLAN_API_KEY",
}

def test(pname, model, desc):
    prov = providers.get(pname)
    if not prov:
        return desc, "⏭ 无provider"
    base = prov.get('base_url','').rstrip('/')
    api_key = os.environ.get(key_map.get(pname,''), '')
    if not base or not api_key:
        return desc, "SKIP no API key"
    try:
        t0 = time.time()
        req = urllib.request.Request(
            base + "/chat/completions",
            data=json.dumps({"model": model, "messages": [{"role":"user","content":"hi"}], "max_tokens": 3}).encode(),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            ms = round((time.time()-t0)*1000)
            body = json.loads(r.read())
            return desc, f"✅ HTTP {r.status} | {ms}ms | model={body.get('model','?')}"
    except urllib.error.HTTPError as e:
        err = e.read().decode()[:100]
        return desc, f"❌ HTTP {e.code} | {err}"
    except Exception as ex:
        return desc, f"❌ {type(ex).__name__}: {ex}"

print("\n🔄 正在并发测试所有套餐...")
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
    futs = [ex.submit(test, *t) for t in targets]
    results = [f.result() for f in concurrent.futures.as_completed(futs)]

ok = sum(1 for _,r in results if r.startswith("✅"))
fail = len(results) - ok

print(f"\n{'套餐':<45} 结果")
print("─"*95)
for d, r in sorted(results):
    print(f"  {d:<45} {r}")
print(f"\n📊 汇总: {ok}✅ / {fail}❌ / {len(results)} 总计")
PYEOF
