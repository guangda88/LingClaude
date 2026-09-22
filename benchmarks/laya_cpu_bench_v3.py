#!/usr/bin/env python3
"""Laya 规范复测 v3：预热丢弃 + 单题计时 + torch 线程扫描 + 对照 LLM 路由端到端延迟。

修正 v2 口径问题：
1. 预热 3 轮丢弃（首推含 JIT/缓存冷启动）
2. 单问题计时（对照 T4 33ms 基准的同口径；多题并行值单列）
3. torch.set_num_threads 扫描（1/2/4/8 核，找本机甜点）
4. 对照物：LLM 路由调用的本地端到端延迟（网络往返另记）
"""
import json
import os
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import torch
from laya import load
from laya.presets import triage_questions

QS = triage_questions()
STATES = [
    "User says: my server is down since this morning, clients are complaining loudly, need fix NOW or we lose the contract.",
    "Hi, quick question — what is included in the pro plan? Considering upgrading next quarter.",
    "Please cancel my subscription, found a cheaper alternative.",
    "Invoice #4423 looks wrong, charged twice for September, want a refund.",
]
WARMUP = 3

def bench_single_question(agent, state, qs):
    """单问题计时（对照 T4 基准口径）：每问预热后测 3 次取中位。"""
    per_q = {}
    for qid, qdef in qs.items():
        # 预热
        for _ in range(WARMUP):
            agent.system_one(state, {qid: qdef})
        lat = []
        for _ in range(3):
            t0 = time.perf_counter()
            agent.system_one(state, {qid: qdef})
            lat.append((time.perf_counter() - t0) * 1000)
        lat.sort()
        per_q[qid] = round(lat[1], 1)  # 中位
    return per_q


def bench_full_preset(agent, states):
    """5 题并行一次调用计时（v2 口径，单列对照）。"""
    lat = []
    for s in states:
        for _ in range(WARMUP):
            agent.system_one(s, QS)
        t0 = time.perf_counter()
        agent.system_one(s, QS)
        lat.append(round((time.perf_counter() - t0) * 1000, 1))
    return lat


def bench_threads(name, kwargs, thread_counts=(1, 2, 4, 8)):
    results = {}
    for n in thread_counts:
        torch.set_num_threads(n)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass  # interop 线程全局只能设一次；后续档位沿用首次设置
        agent = load("convaiinnovations/laya", device="cpu", **kwargs)
        per_q = bench_single_question(agent, STATES[0], QS)
        full = bench_full_preset(agent, STATES)
        med_single = sorted(per_q.values())[len(per_q) // 2]
        results[n] = {
            "median_single_q_ms": round(med_single, 1),
            "full_preset_ms": full,
        }
        del agent
        print(f"  [{name}] threads={n}: single-q median {med_single:.1f}ms, full-preset {full}", flush=True)
    return results


def main():
    doc = {"as_of": "2026-09-22", "device": "cpu", "method": "warmup3+median-of-3+thread-sweep",
           "baseline_T4_ms_single_q": 33, "checkpoints": {}}
    for name, kw in [("english", {}), ("multilingual", {"subfolder": "multilingual"}), ("typed-decisions", {"subfolder": "typed-decisions"})]:
        print(f"=== {name} ===", flush=True)
        try:
            doc["checkpoints"][name] = bench_threads(name, kw)
        except Exception as e:
            doc["checkpoints"][name] = {"error": str(e)[:300]}
    with open("/tmp/laya_bench_v3.json", "w") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(json.dumps(doc, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
