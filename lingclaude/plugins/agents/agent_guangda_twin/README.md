# 光大分身插片（agent/guangda-twin）

> 层级声明（铁律 2 停层显式化）：本插片**不含任何新概念**。它只是把
> `/home/ai/guangda-twin` 已有的 8 个 MCP 工具挂到 lc 的 `agent/` 域缝上。
> 概念在分身仓，插片只做传输与 record 化。

## 它是什么

刘庆（guangda88 / 广大老师）的数字分身。与灵族其他 12 子性质根本不同：
**它建模的是主人本人，不是独立 agent。**

## 铁律锚点

| 铁律 | 本插片的落法 |
|---|---|
| 1 薄主干 | 全在 `plugins/agents/`，`core/` 零 diff |
| 2 分形 | 停层显式化：本插片是薄主干，kernel 在分身仓 |
| 3 变化走接缝 | `SeamRegistry.register(SeamType.AGENT, "agent/guangda-twin", …)` |
| 4 修剪语法 | 无临时直连；若日后直连须挂 debt record |
| 5 双向互认 | `federation_pair/{guangda-twin-lc,lc-guangda-twin}.json` 两侧齐备 |
| 6 信任等级 | `trust_level=T1` + `plug_level=L1` 双声明 |
| 7 缝命名空间 | 缝 key 硬编 `agent/` 域前缀 |
| 8 三域 | `health_state` record + `work_claim` TTL 60min |

**N7 横向耦合禁令**：`plugin.py` 不 import 分身仓的任何 Python 符号，
只用子进程 + JSON-RPC 通信。依赖方向恒为 lc → twin。

## 两条不可绕过的边界

1. **自动通道 promote 判据权限 = 0。** 分身每天可以发现问题、出候选、给你出
   3-5 个"你可能想不到的问题"，但 active 判据的新增/修改/删除只能由
   `guangda88` 显式执行。理由：一个自优化系统若能自己改自己的判据，
   它的"优化"就永远无法被证伪——那正是 L3 本体幻觉的同构形态。

2. **默认不调外部模型。** 语料含未发表论文素材与半年私有对话。
   发往外部端点是不可逆的外向动作，需 guangda88 显式授权，不由代码默认开启。

## 工具面

`twin_ask` / `twin_criteria` / `twin_evidence` / `twin_judge` /
`twin_learn` / `twin_propose` / `twin_status` / `twin_export`

## 自检

```bash
python3 scripts/contract_drift.py check agent:agent_guangda_twin   # N5
python3 scripts/ling_org.py reconcile                              # 文档↔账本互证
python3 -m pytest tests/test_iron_law_guards.py -q                 # M1-M6
```
