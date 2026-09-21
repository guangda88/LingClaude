#!/usr/bin/env python3
"""Laya 3 checkpoints CPU 延迟实测（对照 T4 33ms 基准）— handover §6 / #19."""
import json, time, sys
sys.path.insert(0, "/tmp/laya_venv/lib/python3.12/site-packages")
os_env_hf = None
import laya
from laya import load
from laya.presets import router_questions
from laya.common import ece_score

RESULTS = {}
QS = list(router_questions())[:20] if callable(router_questions) else list(router_questions)[:20]

for name, kwargs in [("english", {}), ("multilingual", {"subfolder": "multilingual"}), ("typed-decisions", {"subfolder": "typed-decisions"})]:
    t0 = time.time()
    try:
        agent = load("convaiinnovations/laya", device="cpu", **kwargs)
    except Exception as e:
        RESULTS[name] = {"error": f"load failed: {e}"}
        continue
    load_s = time.time() - t0
    lat = []
    probs_all = []
    labels_all = []
    for q in QS:
        t1 = time.time()
        try:
            d = agent.route(q)
        except Exception as e:
            lat.append(None)
            continue
        lat.append((time.time() - t1) * 1000)
        p = getattr(d, "probs", None)
        if p is not None:
            probs_all.append(list(p) if hasattr(p, "__iter__") else [p])
    ok = [x for x in lat if x is not None]
    RESULTS[name] = {
        "load_s": round(load_s, 2),
        "n_q": len(QS), "n_ok": len(ok),
        "lat_ms_avg": round(sum(ok) / len(ok), 1) if ok else None,
        "lat_ms_min": round(min(ok), 1) if ok else None,
        "lat_ms_max": round(max(ok), 1) if ok else None,
        "sample_decision": str(d)[:200] if ok else None,
    }
    del agent

print(json.dumps({"as_of": "2026-09-21", "device": "cpu", "baseline_T4_ms": 33, "results": RESULTS}, ensure_ascii=False, indent=2))
with open("/tmp/laya_cpu_bench.json", "w") as f:
    json.dump(RESULTS, f, ensure_ascii=False, indent=2)
