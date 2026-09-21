# ref-typesafe-jev — Jev TypeSafe SDK（Decisions, not strings）

> 来源：docs/peer-borrow/JEV_LAYA_OPTIMIZATION_PLAN.md §二.2.2 / §五 / §九
> as_of: 2026-09-21 真读一手资料（接入手册 8 Parts）

## 摘要

Jev SDK：`pip install typesafe-sdk`，导入 `typesafe_sdk.Choice / Noul / Score / TypeSafeClient`。
核心哲学 **"Decisions, not strings"**——让 decision 成为结构化对象而非自由文本，不污染主干。

## 对灵克的可借鉴点

| Jev 概念 | 灵克落点 | 判定 |
|----------|---------|------|
| `Choice/Noul/Score` 三件套 | 决策 record 维度补充（records-events-transition 之外） | 同向强化 |
| 决策不污染主干 | 薄主干/分形插片（铁律 §一 概念封闭） | 同向强化 |
| Router 自动 detect + dispatch | `core/seam.py` `auto_dispatch_table`（SeamRegistry Router 风格） | 同向强化 |
| "Zero Hallucination" 营销词 | **不抄**（铁律 §四 诚实：format ≠ correctness） | 警觉点 |
| threshold 0.7/0.8 示例 | **必须本地 calibrate**，不照搬 | 警觉点 |
| 替代 LLM 路由 | **不是替代，是补充**（fast lane；reasoning/长链路仍走 LLM） | 警觉点 |

## 关联

- P1-3：`NOT_GOOD_AT` seam 字段（Jev 不擅长清单，task_router 直接绕过 LLM）
- P1-0：Speculative Fan Out 走 LoopHooks 子 seam
