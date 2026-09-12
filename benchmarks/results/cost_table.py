#!/usr/bin/env python3
"""#7 成本采集表生成 — 汇总 6 agent 的 token/耗时/成本 (RealBench 三维度)。

数据源:
- 耗时(wall_s): cost_ledger.jsonl 每行 agent_wall_s
- token: 各 agent 会话日志 (claude/codex 已补进 ledger; 此处补 opencode/crush/atomcode/lingclaude)
- 成本: coding plan 免费=0; 直连按价 (codex ZAI 已耗尽→volc 也免费; 均记 0 并注明)

输出: cost_table.json + cost_table.md (每 agent 一行: avg_wall_s / total_in_tokens /
      total_out_tokens / cost_usd / 效率分=pass_per_minute)
"""
from __future__ import annotations
import json
import os
import re
import sqlite3
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
LEDGER = BASE / "cost_ledger.jsonl"
OUT_DIR = BASE
WINS = time.time() - 6 * 3600  # bench 窗口近 6h


def _sum_usage_jsonl(root: str) -> tuple[int, int]:
    """atomcode 会话 jsonl: usage{prompt,completion}. 窗口内汇总."""
    tp = tc = 0
    for f in os.path.expanduser(root).replace("~", os.path.expanduser("~")) and \
            [x for x in Path(os.path.expanduser(root)).glob("*/*.jsonl")]:
        try:
            if time.time() - f.stat().st_mtime > 6 * 3600:
                continue
            for l in open(f, encoding="utf-8", errors="ignore"):
                for m in re.finditer(
                        r'"usage":\{"prompt":(\d+),"completion":(\d+)', l):
                    tp += int(m.group(1)); tc += int(m.group(2))
        except OSError:
            continue
    return tp, tc


def _crush_db_tokens() -> tuple[int, int, float]:
    db = Path.home() / ".crush" / "crush.db"
    if not db.exists():
        return 0, 0, 0.0
    c = sqlite3.connect(db)
    try:
        pt = ct = cost = 0
        for p, q, r in c.execute(
                "select coalesce(prompt_tokens,0), coalesce(completion_tokens,0), "
                "coalesce(cost,0) from sessions where created_at > ?",
                (time.time() - 6 * 3600,)):
            pt += p; ct += q; cost += r
        return pt, ct, cost
    finally:
        c.close()


def _opencode_tokens() -> tuple[int, int]:
    """opencode: 免费路由(cost=0), token 不落本地明细 → 记 0 并标注 not_tracked."""
    return 0, 0


def _lingclaude_journal_tokens() -> tuple[int, int]:
    """lingclaude: 从会话 journal 提取 turn_end 的 total_input/output（P0 后非 0）。

    兼容：P0 修复前 journal 恒 0 → 返回 (0,0)，标注 not_in_journal。
    """
    jdir = Path.home() / ".lingclaude" / "journals"
    tin = tout = 0
    if jdir.exists():
        for f in jdir.glob("*.jsonl"):
            try:
                for l in open(f, encoding="utf-8", errors="ignore"):
                    if '"turn_end"' not in l:
                        continue
                    m = re.search(r'"total_input":\s*(\d+)', l)
                    n = re.search(r'"total_output":\s*(\d+)', l)
                    if m:
                        tin += int(m.group(1))
                    if n:
                        tout += int(n.group(1))
            except OSError:
                continue
    return tin, tout


def collect() -> dict:
    rows = [json.loads(l) for l in open(LEDGER, encoding="utf-8")]
    agents = ["claude", "codex", "opencode", "crush",
              "atomcode", "lingclaude"]
    out = {}
    for a in agents:
        ar = [r for r in rows if r.get("agent") == a]
        benches = {}
        for b in ("repobench_in_file", "terminal-bench-2.0", "swe-bench-lite"):
            br = [r for r in ar if r.get("bench") == b]
            # 去重: 同一 (bench, task) 只计 1 pass — 重跑/多 attempt 不再虚高
            passed_tasks = {r.get("task") for r in br if r.get("passed")}
            benches[b] = {
                "n_runs": len(br),
                "n_pass": len(passed_tasks),
                "n_unique_tasks": len({r.get("task") for r in br}),
                "passed_tasks": sorted(str(t) for t in passed_tasks if t),
                "avg_wall_s": round(sum(r.get("agent_wall_s", 0) or 0
                                        for r in br) / len(br), 1) if br else 0,
                "max_wall_s": max((r.get("agent_wall_s", 0) or 0 for r in br),
                                  default=0),
                "sum_wall_s": round(sum(r.get("agent_wall_s", 0) or 0
                                        for r in br), 1),
            }
        # token: 区分口径
        #  - ledger 里 token_scope 非 window 类: 逐 run 精确, 可求和
        #  - window_cumulative / *_window6h: 是该 agent 会话窗口的"累计总量",
        #    已被挂到每条 run 上 → 不能逐 run 再累加(会虚高 N 倍), 只取 1 条代表值
        # window_cumulative(窗口累计值已挂到每条 run): 取该 agent 最大代表值 1 次,
        # 不逐 run 累加(否则虚高 N 倍); 其余来源可正常求和。
        win_vals_in = [r.get("input_tokens") or 0 for r in ar
                       if str(r.get("token_scope", "")).startswith(("window",))]
        win_vals_out = [r.get("output_tokens") or 0 for r in ar
                        if str(r.get("token_scope", "")).startswith(("window",))]
        if win_vals_in:
            tin = max(win_vals_in)
            tout = max(win_vals_out)
            token_src = "window_cumulative"
        else:
            run_scoped = [r for r in ar if (r.get("input_tokens") or 0) > 0]
            tin = sum(max(r.get("input_tokens") or 0, 0) for r in run_scoped)
            tout = sum(max(r.get("output_tokens") or 0, 0) for r in run_scoped)
            token_src = "ledger_per_run"
        if tin == 0 and tout == 0:
            # 缺失 agent 补采
            if a == "atomcode":
                tin, tout = _sum_usage_jsonl(
                    str(Path.home() / ".atomcode" / "sessions"))
                token_src = "atomcode_usage_window6h"
            elif a == "crush":
                tin, tout, _ = _crush_db_tokens()
                token_src = "crush_db_sessions_window6h"
            elif a == "opencode":
                tin, tout = _opencode_tokens()
                token_src = "not_tracked_free_router"
            elif a == "lingclaude":
                tin, tout = _lingclaude_journal_tokens()
                token_src = "lingclaude_journal_turn_end" if (tin or tout) else "not_in_journal"
        out[a] = {
            "benches": benches,
            "input_tokens": tin,
            "output_tokens": tout,
            "total_tokens": tin + tout,
            "token_source": token_src,
            "cost_usd": 0.0,  # 全 coding plan / 免费路由, 本次 0 成本
            "note": "coding-plan/免费路由, 本 run 成本 $0",
        }
    return out


def _efficiency_score(d: dict) -> float:
    """效率分: 每 60s 完成的 pass 数 (跨 bench). 越高越快."""
    passes = sum(b["n_pass"] for b in d["benches"].values())
    wall = sum(b["sum_wall_s"] for b in d["benches"].values()) or 1
    return round(passes / (wall / 60), 3)


def main() -> None:
    data = collect()
    # 补效率分
    for a, d in data.items():
        d["efficiency_pass_per_min"] = _efficiency_score(d)
    data["meta"] = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "cost_basis": "全部走 coding plan / 免费模型, 成本 $0",
        "token_note": "claude/codex 从会话日志精确到 run; "
                      "atomcode 会话窗 6h 汇总; crush/opencode 未落 token 明细",
        "realbench": "官方 RealBench 仓库 404, 改用本自研计时层",
    }
    json.dump(data, open(OUT_DIR / "cost_table.json", "w",
                          encoding="utf-8"), ensure_ascii=False, indent=1)

    # Markdown 表
    L = ["# 成本与效率采集表（#7）",
         "",
         f"> 生成: {data['meta']['generated']}  |  "
         f"{data['meta']['cost_basis']}  |  {data['meta']['realbench']}",
         "",
         "| agent | bench | runs | pass | avg_wall_s | sum_wall_s |",
         "|-------|-------|------|------|-----------|-----------|"]
    for a, d in {k: v for k, v in data.items() if k != "meta"}.items():
        for b, m in d["benches"].items():
            if m["n_runs"]:
                L.append(f"| {a} | {b} | {m['n_runs']} | {m['n_pass']} "
                         f"| {m['avg_wall_s']} | {m['sum_wall_s']} |")
    L += ["",
          "## token 与成本",
          "",
          "| agent | input_tokens | output_tokens | total | cost_usd | 来源 |",
          "|-------|-----------|-------------|-------|----------|------|"]
    for a, d in {k: v for k, v in data.items() if k != "meta"}.items():
        L.append(f"| {a} | {d['input_tokens']:,} | {d['output_tokens']:,} "
                 f"| {d['total_tokens']:,} | {d['cost_usd']} | "
                 f"{d['token_source']} |")
    L += ["",
          "## 效率分（pass / 分钟）",
          ""]
    for a, d in {k: v for k, v in data.items() if k != "meta"}.items():
        L.append(f"- {a}: **{d['efficiency_pass_per_min']}** pass/min")
    open(OUT_DIR / "cost_table.md", "w",
         encoding="utf-8").write("\n".join(L))
    print("cost_table.json + cost_table.md 已生成")
    print(f"agent 数: {len(data) - 1}")


if __name__ == "__main__":
    main()
