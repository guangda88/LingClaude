#!/usr/bin/env python3
"""Laya 3 checkpoints CPU 延迟实测 v2 — Agent.system_one(triage_questions), 对照 T4 33ms."""
import json, time
from laya import load
from laya.presets import triage_questions

QS = triage_questions()
STATES = [
    "User says: my server is down since this morning, clients are complaining loudly, need fix NOW or we lose the contract.",
    "Hi, quick question — what is included in the pro plan? Considering upgrading next quarter.",
    "Please cancel my subscription, found a cheaper alternative.",
    "Invoice #4423 looks wrong, charged twice for September, want a refund.",
]

def bench(name, **kw):
    t0 = time.time()
    agent = load("convaiinnovations/laya", device="cpu", **kw)
    load_s = round(time.time() - t0, 2)
    lats, first = [], None
    for s in STATES:
        t1 = time.time()
        out = agent.system_one(s, QS)
        lats.append((time.time() - t1) * 1000)
        if first is None:
            first = {k: out[k] for k in out if k in ("answers", "confidence")} if isinstance(out, dict) else str(out)[:300]
    return {
        "load_s": load_s,
        "n_states": len(STATES),
        "lat_ms_avg": round(sum(lats) / len(lats), 1),
        "lat_ms_min": round(min(lats), 1),
        "lat_ms_max": round(max(lats), 1),
        "sample": first,
    }

results = {}
for name, kw in [("english", {}), ("multilingual", {"subfolder": "multilingual"}), ("typed-decisions", {"subfolder": "typed-decisions"})]:
    try:
        results[name] = bench(name, **kw)
    except Exception as e:
        import traceback
        results[name] = {"error": traceback.format_exc()[-800:]}

doc = {"as_of": "2026-09-21", "device": "cpu", "api": "Agent.system_one(triage_questions)", "baseline_T4_ms": 33, "results": results}
print(json.dumps(doc, ensure_ascii=False, indent=2))
with open("/tmp/laya_cpu_bench_v2.json", "w") as f:
    json.dump(doc, f, ensure_ascii=False, indent=2)
