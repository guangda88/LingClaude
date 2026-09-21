# ref-laya-system1 — Laya 路由模型（System-1 fast lane）

> 来源：docs/peer-borrow/JEV_LAYA_OPTIMIZATION_PLAN.md §二.2.1 / §六 / §九
> as_of: 2026-09-21 真读一手资料（README + checkpoints）

## 摘要

Laya：小型路由/分类模型，三个 checkpoints（Apache 2.0，100% 人类标注，无合成标签）：

| Checkpoint | 参数 | 用途 |
|-----------|------|------|
| laya | 421M | 通用部门路由 |
| laya-multilingual | 322M | 多语种 |
| laya-typed-decisions | — | typed-decisions 场景 |

训练：TD(λ=1.0) 多轮前缀（避免数据泄漏）。基准 T4：33ms/问（7.2ms 为最小档）。

## 本机裁定（2026-09-21 用户）

- **Laya 走 CPU，GPU 留给灵元推理栈**。
- 本机 GTX 1660 Ti（6GB）对 421M 无决定性优势；CPU 估 165-330ms/问（慢 5-10×，仍 <0.4s），
  部门路由/紧急度量非实时判断**够用**。
- CPU 实测数字仅作 P1-1 排期可信度依据，**不写进 SLA**（铁律 §四）。

## 工程化锚点

- P1-1：Laya 本地 fast lane（NOT_GOOD_AT 显式声明 + 安全路径代码优先 + CPU 实测先做）
- P1-2：8 项评估指标矩阵进 benchmark.py（ECE 双列表分桶 + 桶号 pred=0.1→桶1）
- P1-4：`policies/fan_out_questions.yaml`（沿用 router_keywords data-driven 模式）

## 警觉点

- Laya Router 是"自动 detect 后派"——是灵元"插片自动组合"的工程化实例，但作者独立研究，
  与灵元哲学的收敛是**同向参考而非验证**，警惕"工程化证明"过度归因。
- NuOut 三原语适用面窄：不能完全替代 LLM，长文/代码理解/创意生成仍走 decoder-only LLM。
