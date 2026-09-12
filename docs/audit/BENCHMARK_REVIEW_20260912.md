# 六大 Agent Benchmark 数据复核修正报告（2026-09-12）

> 复核对象: /home/ai/bench/runs/20260912/ (atomcode 评测产物, 只读)
> 复核结论: FINAL_REPORT.md 中 lingclaude 的 TB 结论存在**重复计数 bug**, 已修正。
> 状态更新 (2026-09-12 修复后): P0/P1 已实施 — token 遥测兜底 (cf6261f) + cost_table 去重版本化 (3e13425)。

---

## 一、数据 bug 发现与修正

### 原始产物 (只读, 不可改)
- `round2_tb/report.json`: lingclaude TB = **1/6** (countdown-game 唯一 PASS)
- `round2_tb/report.md`: lingclaude **1/6** (与 claude/codex 并列, 排第 3)
- `cost_ledger.jsonl`: lingclaude/countdown-game 有 **3 条记录** (L121 failed / L122 passed / L150 passed)
- `cost_table.json`: lingclaude TB **n_runs:8, n_pass:2** ← **重复计数 bug**
- `FINAL_REPORT.md`: "lingclaude TB 2/6 最高" ← **基于 bug 的错误结论**

### 根因
`cost_table.py` 的 bench 统计直接对 ledger 行计数 (`len(br)` / `sum(passed)`),
未按 (agent, task) 去重。lingclaude/countdown-game 重跑 3 次被计入 3 条 → n_runs 8、n_pass 2。
其余 agent 无重跑, 不受影响。

### 修正 (去重后, 每 agent 每 task 取最后一条最终判定)

| agent | RepoBench | Terminal-Bench | 效率 pass/min |
|-------|-----------|---------------|---------------|
| claude | 7/20 | **1/6** | 0.642 |
| codex | 5/20 | **1/6** | 0.235 |
| opencode | 7/20 | 0/6 | 0.539 |
| crush | 7/20 | 0/6 | 0.275 |
| atomcode | 1/20 | 0/6 | 0.022 |
| **lingclaude** | 3/20 | **1/6** | **0.088** (原报 0.098) |

**TB 并列**: claude / codex / lingclaude 各 1/6 并列第一; opencode/crush/atomcode 0/6。
**不存在 "lingclaude 2/6 最高"** —— 原报告该结论是重复计数造成的假象。

---

## 二、修正后的正确结论

### lingclaude 真实位置
- RepoBench 单文件补全: 3/20 (中游偏下, 同前)
- Terminal-Bench: 1/6 (**与 claude/codex 并列第一**, 不再是"唯一最高")
- SWE-bench: 能出 patch (定性, agent_ok=True, 同前)
- 效率: 0.088 pass/min (中游, codex 之下)

### 修正带来的实质变化
1. **"TB 最高"的差异化优势消失** → lingclaude 与 claude/codex 在终端任务上并列, 无独占优势
2. 效率分 0.098 → 0.088 (微降, 不影响排名)
3. RepoBench 结论不变 (7/20 vs 3/20 差距依然明显)

---

## 三、优化方向 (基于修正后数据)

1. **单文件补全仍是最大短板** (3/20 vs 头部 7/20):
   - 底层 deepseek-v4-flash 短上下文精确补全弱于 claude 系 → 补全特化 prompt 或更强模型
   - 与 RepoBench 评测代码接入 (lingclaude/benchmarks/ 已有 harness) 做专项回归

2. **token 遥测缺失** — ✅ 已修复 (cf6261f + 3e13425):
   - P0 (cf6261f): model_call 新增 `_estimate_tokens` 兜底, usage 缺失时估算 (0.28 token/字, 中文适配), journal `turn_end` 非 0
   - P1 (3e13425): `cost_table.py` 的 `_lingclaude_journal_tokens()` 从 journal `turn_end` 提取 `total_input/total_output`
   - 设计: 真实 usage 优先 (`_accumulate_usage` 只累真实值), 估算仅在 provider 未回传时生效, 不污染真实数据
   - 残留: 估算口径为近似值, 需更大样本验证估算偏差

3. **终端任务并列第一, 保持但不依赖**:
   - TB 1/6 与 claude/codex 并列, 需更多任务样本才能确认相对优势
   - 建议扩大 TB 子集 (6→20+) 做二次评测

---

## 四、复核方法与可复现性

- 复核脚本逻辑: 从 cost_ledger.jsonl 过滤 bench, 按 (agent, task) 去重取最后一条, 统计 pass/wall
- ✅ 已由正式实现闭环: `benchmarks/results/cost_table.py` (3e13425, 215 行) 版本化入库
  - 去重: `passed_tasks = {task for r in br if r.passed}` + `n_unique_tasks` 字段 (可审计, 非 len(br))
  - lingclaude token: `_lingclaude_journal_tokens()` 从 journal `turn_end` 提取 (P0 修复后非 0)
  - window token: 取 max 代表值不逐 run 累加 (防虚高 N 倍)
  - 兼容: P0 修复前 journal 恒 0 → 返回 (0,0) 标注 not_in_journal
- 原始数据: /home/ai/bench/runs/20260912/cost_ledger.jsonl (158 行, 只读保留)
- 修正产物: 本文件 (docs/audit/BENCHMARK_REVIEW_20260912.md) + benchmarks/results/cost_table.py
