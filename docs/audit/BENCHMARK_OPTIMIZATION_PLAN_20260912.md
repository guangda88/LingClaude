# lingclaude 优化方向与实施规划（基于 2026-09-12 六 Agent Bench 复盘）

> 配套: docs/audit/BENCHMARK_REVIEW_20260912.md (数据复核修正)
> 源码查证日期: 2026-09-12

---

## 一、数据修正摘要（bug 已确认）

| 项 | 原报告 (错) | 修正后 (对) | 根因 |
|----|------------|------------|------|
| lingclaude TB | 2/6 最高 | **1/6 并列** (claude/codex) | cost_table.py 未按 (agent,task) 去重, countdown-game 重跑3次计2pass |
| lingclaude 效率 | 0.098 | **0.088** | 同上, 重复计 pass 虚高 |
| RepoBench | 3/20 | 3/20 (不变) | 无重复 |

---

## 二、源码级根因查证结论

### 缺口1: token 遥测在单发/免费路由下为 0 (非"没记录")
- `model_call.py:207-208` total_input/output 初始 0
- `model_call.py:235-236` 仅 `response.usage` 非空时累加
- **实测** (round2_tb 真实 journal): `turn_end total_input=0, total_output=0`; `long_task_metrics usage={input:0,output:0}`
- **根因**: 评测走 `lingclaude run` 单发, 底层免费路由/模型 response.usage 未返回 token → 恒 0
- **不是** cost_table 说的 "not_in_journal" (有 journal, 值是 0)

### 缺口2: RepoBench 单文件补全 3/20 弱于 claude 7/20
- runner 用统一 prompt + `lingclaude run` 单发 (runner.py:193)
- 底层 deepseek-v4-flash 短上下文精确补全弱 (benchmark 数据)

### 缺口3: 评测数据统计健壮性
- cost_table.py 无去重 → 重复 run 污染 pass 统计 (本次实际发生)
- 评测产物目录只读, 修不了原地, 需复制+修

---

## 三、优化规划 (按优先级)

### P0: token 遥测补真实值 (治本)
**目标**: 让 `lingclaude run` 单发模式的 usage 落真实 token, 供评测/成本审计。
- 方案A: model_call.py 在 usage 为空时, 用本地 tokenizer (tiktoken/heuristic) 估算 input/output 并填充
- 方案B: 提供 `--usage-json` 输出标志, 让 CLI 单发把 usage 打到 stdout (评测侧易读)
- 推荐 A+B: A 保底, B 供评测采集
- 验证: 重跑 1 题 RepoBench, journal 内 usage > 0

### P1: 评测侧去重修复 (防再犯)
- cost_table.py 加 (agent, task) 去重 (本次已手工验证逻辑, 产物目录只读无法原地改)
- 建议: 评测脚本复制到 lingclaude/benchmarks/ 可写区 + 加去重 + 版本化

### P2: RepoBench 专项优化
- 补全特化 prompt (给更多上下文/示例) 或评估更强补全模型
- 接入 lingclaude/benchmarks/ 做专项回归

### P3: 数据可复现性
- 评测脚本/runner 版本化到 lingclaude/benchmarks/
- FINAL_REPORT 生成器加"原始数据哈希+去重统计"双校验

---

## 四、已实施 (本轮)

1. `docs/audit/BENCHMARK_REVIEW_20260912.md` — 数据修正报告
2. `docs/audit/BENCHMARK_OPTIMIZATION_PLAN_20260912.md` — 本规划
3. `scripts/export_bench_tokens.py` — token 遥测导出脚本 (供评测侧采集, 验证了 journal 值为 0 的根因)
