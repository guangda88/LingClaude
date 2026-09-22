#!/usr/bin/env python3
"""P1-1 fast lane 实机判定质量验证（2026-09-22）：真实 prompt 集跑 fast_route，
对照人工标注期望 domain/difficulty 判准确率。数字不写 SLA（铁律 §四）。

数据源：router_questions preset 的 domain/difficulty 两问，跑在 laya venv
（multilingual checkpoint，CPU @4线程）。期望标注为人工构造的合理值——
"判得准不准"= Laya 实际判定与人工期望的匹配率。

输出：benchmarks/laya_fastlane_quality.json（domain 准确率 / difficulty 分布 /
NOT_GOOD_AT 命中率 / 逐条明细），供 P1-1 部署裁定（准确率阈值之上才挂
resolve 链生产消费）。
"""
import json
import os
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import torch
from laya import load
from laya.presets import router_questions

QS = router_questions()

# 人工标注测试集：(prompt, 期望 domain, 期望 difficulty 分桶)
# domain 值对齐 router_questions.domain criteria 的键。
# difficulty 分桶：trivial/easy→low, moderate→mid, hard→high（对齐 resolve 链的
# difficulty.startswith("hard") 判定面，只验"high 是否触发路由提升"的语义）。
CASES = [
    # ── code 域 ──
    ("write a python function to parse JSON and flatten it", "code", "mid"),
    ("refactor this class to use dependency injection", "code", "mid"),
    ("debug why my react component re-renders infinitely", "code", "high"),
    ("fix the null pointer exception in main.java line 42", "code", "mid"),
    # ── math/logic ──
    ("solve this integral of x^2 from 0 to 1", "math_or_logic", "mid"),
    ("prove that sqrt(2) is irrational", "math_or_logic", "high"),
    ("what is 15% of 240", "math_or_logic", "low"),
    # ── writing ──
    ("write a thank you email to the client for the contract renewal", "writing", "low"),
    ("draft a marketing blog post about our new AI feature", "writing", "mid"),
    ("write a 5-line haiku about the ocean", "writing", "low"),
    # ── factual_lookup ──
    ("what is the boiling point of water in Celsius", "factual_lookup", "low"),
    ("who won the 2019 world cup in football", "factual_lookup", "low"),
    ("define photosynthesis in one sentence", "factual_lookup", "low"),
    # ── data_analysis ──
    ("analyze this CSV and find the top 5 customers by spend", "data_analysis", "mid"),
    ("plot the sales trend and flag anomalies in the quarterly data", "data_analysis", "mid"),
    # ── NOT_GOOD_AT（代码重块/长链路，应被 NOT_GOOD_AT_PATTERNS 命中回退）──
    ("```python\nimport os\ndef main():\n    pass\n``` explain and extend this", "code", "high"),
    ("review the diff: -- git HEAD~1 and summarize changes", "code", "high"),
]


def _difficulty_bucket(label: str) -> str:
    """把 Laya difficulty 回答归入 low/mid/high 桶（对齐 resolve 链消费语义）。"""
    s = (label or "").lower()
    if s.startswith("trivial") or s.startswith("easy"):
        return "low"
    if s.startswith("moderate"):
        return "mid"
    if s.startswith("hard"):
        return "high"
    return "unknown"


def main():
    agent = load("convaiinnovations/laya", device="cpu", subfolder="multilingual")
    torch.set_num_threads(4)
    # 预热
    for _ in range(2):
        agent.system_one(CASES[0][0], QS)

    domain_hits, total = 0, len(CASES)
    diff_hits = 0
    not_good_hits = 0
    details = []
    latencies = []
    for prompt, exp_dom, exp_diff in CASES:
        t0 = time.perf_counter()
        out = agent.system_one(prompt, QS)
        ms = (time.perf_counter() - t0) * 1000
        latencies.append(round(ms, 1))
        answers = out.get("answers") or {}
        # choice 类答案是 {type:'choice', choice:<值>, probabilities:{...}, confidence}
        # score 类答案是 {type:'score', score:<float>, legend, probabilities}
        # 正确读法：取 choice / score 字段（不是整个 dict 比对）。
        dom_ans = answers.get("domain") or {}
        got_dom = str(dom_ans.get("choice", "")).lower() if isinstance(dom_ans, dict) else str(dom_ans).lower()
        diff_ans = answers.get("difficulty") or {}
        diff_score = diff_ans.get("score") if isinstance(diff_ans, dict) else None
        # difficulty score 是 0-3 数值（0=trivial/1=easy/2=moderate/3=hard）。
        # 分桶（对齐 resolve 链消费语义）：<1.5→low, [1.5,2.5)→mid, >=2.5→high。
        if isinstance(diff_score, (int, float)):
            d_bucket = "low" if diff_score < 1.5 else ("mid" if diff_score < 2.5 else "high")
            got_diff_label = f"score={diff_score}"
        else:
            d_bucket = "unknown"
            got_diff_label = str(diff_ans)
        dom_ok = got_dom == exp_dom
        diff_ok = d_bucket == exp_diff
        domain_hits += int(dom_ok)
        diff_hits += int(diff_ok)
        # NOT_GOOD_AT 命中（代码重块）
        NOT_GOOD = ("```", "def ", "class ", "diff --git")
        if any(p in prompt for p in NOT_GOOD):
            not_good_hits += 1
        details.append({
            "prompt": prompt[:60],
            "exp_domain": exp_dom, "got_domain": got_dom, "domain_ok": dom_ok,
            "exp_diff": exp_diff, "got_difficulty": got_diff, "diff_bucket": d_bucket, "diff_ok": diff_ok,
            "lat_ms": round(ms, 1),
        })

    doc = {
        "as_of": "2026-09-22",
        "checkpoint": "multilingual",
        "device": "cpu@4threads",
        "n_cases": total,
        "domain_accuracy": round(domain_hits / total, 3),
        "difficulty_accuracy": round(diff_hits / total, 3),
        "not_good_at_cases": not_good_hits,
        "lat_ms": {"min": min(latencies), "max": max(latencies),
                    "avg": round(sum(latencies) / len(latencies), 1)},
        "note": "准确率/延迟仅作 P1-1 部署排期依据，不写 SLA（铁律 §四）",
        "details": details,
    }
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    with open("/tmp/laya_fastlane_quality.json", "w") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
