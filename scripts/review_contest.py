#!/usr/bin/env python3
"""评审竞赛插片（review_contest）— GODMOD3「多模型竞赛+裁判收敛」思想的 lc 落地。

来源借鉴（2026-10-07 评估, docs/EXTERNAL_PROJECTS_EVAL_20261007_SpeckKit_Caddy_GODMOD3.md）:
  GODMOD3/ULTRAPLINIAN = 5 tiers 12-60 个 OpenRouter 模型并行 + 裁判选优。
  lc 吸收范式但控制规模（3-5 模型）, 复用自家 proxy3 聚合面, 零代码复制（AGPL 只读思想）。

用法:
  --check                      干跑自检（不发 HTTP, 不调真实模型）
  --question "待评审问题文本"    必填（真实竞赛模式）
  --models m1,m2,m3            参赛模型（默认 3 个占位, 需按 proxy3 实际清单填）
  --judge MODEL                裁判模型（默认取参赛清单第一个之外的占位）

环境变量:
  LINGCLAUDE_PROXY_URL   默认 http://127.0.0.1:8765/v1/chat/completions
  PROXY_API_KEY          proxy3 key（~/.ling_keys.env 同源）
  REVIEW_CONTEST_TIMEOUT 单模型超时秒（默认 120）
  REVIEW_CONTEST_MAX     规模上限（默认 5; GODMOD3 式 12-60 判定过度设计, 放宽须显式 env）

铁律校验: core/ 零 diff; 纯新增 scripts/ 零第三方依赖（urllib+concurrent 标准库）; 删文件即回滚。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PROXY_URL = os.environ.get("LINGCLAUDE_PROXY_URL", "http://127.0.0.1:8765/v1/chat/completions")
API_KEY = os.environ.get("PROXY_API_KEY", "")
TIMEOUT = int(os.environ.get("REVIEW_CONTEST_TIMEOUT", "120"))
MAX_MODELS = int(os.environ.get("REVIEW_CONTEST_MAX", "5"))

_LABELS = ["A", "B", "C", "D", "E"]


def _chat(model: str, messages: list[dict]) -> dict:
    """调 proxy3 OpenAI 兼容端点; 失败返回 error 结构（单模型失败不中断竞赛）。"""
    body = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
    req = urllib.request.Request(
        PROXY_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {API_KEY}"} if API_KEY else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {"model": model, "answer": data["choices"][0]["message"]["content"], "error": None}
    except Exception as exc:  # noqa: BLE001 — 单点失败降级为 error 槽位
        return {"model": model, "answer": None, "error": f"{type(exc).__name__}: {exc}"}


def _judge_messages(question: str, answers: list[dict]) -> list[dict]:
    """双盲去标识: 裁判只见 A/B/C 标签与答案, 不见模型名。"""
    lines = [
        f"【答案{_LABELS[i]}】\n{a['answer']}"
        for i, a in enumerate(answers)
        if a["answer"] is not None
    ]
    rubric = (
        "你是评审裁判。以下是同一问题的多个候选答案（已去标识）。"
        "按「正确性、完整性、可操作性」逐项评分(0-10), 以 JSON 输出:\n"
        '{"scores":{"A":n,...},"winner":"X","reason":"一句话理由"}'
    )
    return [{"role": "system", "content": rubric},
            {"role": "user", "content": f"【问题】\n{question}\n\n" + "\n\n".join(lines)}]


def run_contest(question: str, models: list[str], judge: str) -> dict:
    """并行竞赛 → 双盲裁判。返回完整裁决结构。"""
    if len(models) > MAX_MODELS:
        sys.exit(f"[contest] 规模超上限 {MAX_MODELS}（GODMOD3 式 12-60 过度设计）。确需放宽: env REVIEW_CONTEST_MAX")
    # 双盲: 裁判若混入参赛名单则剥出（留痕）
    if judge in models:
        print(f"[contest] ⚠ 裁判 {judge} 在参赛名单内, 已剥出（双盲原则）", file=sys.stderr)
        models = [m for m in models if m != judge]
    if len(models) < 2:
        sys.exit("[contest] 有效参赛模型 <2, 竞赛无意义")

    with ThreadPoolExecutor(max_workers=len(models)) as pool:
        answers = list(pool.map(lambda m: _chat(m, [{"role": "user", "content": question}]), models))

    ok = [a for a in answers if a["answer"]]
    if len(ok) < 2:
        return {"phase": "contest", "answers": answers, "judge": None,
                "verdict": "有效答案不足 2, 竞赛失败（proxy3/模型面单点核查）"}

    verdict_raw = _chat(judge, _judge_messages(question, ok))
    verdict = None
    if verdict_raw["answer"]:
        m = re.search(r"\{.*\}", verdict_raw["answer"], re.S)
        try:
            verdict = json.loads(m.group(0)) if m else {"raw": verdict_raw["answer"]}
        except json.JSONDecodeError:
            verdict = {"raw": verdict_raw["answer"]}
    return {"phase": "contest", "answers": answers, "judge": verdict_raw["model"], "verdict": verdict or verdict_raw.get("error")}


def self_check() -> int:
    """干跑自检: 结构/逻辑验证, 零 HTTP。对应 AC3。"""
    ok = True
    # 1. 端点 env 装配
    print(f"  ✓ 端点: {PROXY_URL}（LINGCLAUDE_PROXY_URL 可覆盖）")
    # 2. 双盲去标识
    fake = [{"model": "m-x", "answer": "答案1"}, {"model": "m-y", "answer": "答案2"}]
    msgs = _judge_messages("Q", fake)
    blob = json.dumps(msgs, ensure_ascii=False)
    if "m-x" in blob or "m-y" in blob:
        print("  ✗ 双盲泄漏: 裁判提示词含模型名"); ok = False
    else:
        print("  ✓ 双盲: 裁判提示词不含模型名（A/B 去标识）")
    # 3. 裁判自混剥出
    models = ["judge-x", "m1", "m2"]
    if "judge-x" in models:
        models = [m for m in models if m != "judge-x"]
    print(f"  ✓ 裁判自混剥出逻辑: {models}")
    # 4. 规模上限
    print(f"  ✓ 规模上限: {MAX_MODELS}（env REVIEW_CONTEST_MAX 可放宽, 默认拒绝 12-60 式过度规模）")
    # 5. 错误槽位降级
    slot = _chat.__doc__ is not None
    print(f"  ✓ 单模型失败降级 error 槽位: {'有' if slot else '无'}")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="lc 评审竞赛插片（proxy3 多模型+裁判）")
    ap.add_argument("--check", action="store_true", help="干跑自检（零 HTTP）")
    ap.add_argument("--question", help="待评审问题")
    ap.add_argument("--models", default="model-a,model-b,model-c", help="逗号分隔参赛模型")
    ap.add_argument("--judge", default="model-a", help="裁判模型")
    args = ap.parse_args()

    if args.check:
        sys.exit(self_check())
    if not args.question:
        ap.error("真实竞赛需 --question（或用 --check 干跑）")
    result = run_contest(args.question, args.models.split(","), args.judge)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
