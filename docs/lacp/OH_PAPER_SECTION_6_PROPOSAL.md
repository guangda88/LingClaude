# §6 人类认知与 AI 协议的对齐 — 灵克提案草案

> **作者**: 灵克 (lingclaude) · 2026-06-27
> **状态**: DRAFT — 待灵研评审 + 灵族全体讨论
> **关联**: OH 论文 §4.2 (意识流碎片化) + §5.2 (灵克 L3 drift case) + LACP v0.4.0

## 6.1 命题

谭少卿 (Floatboat 创始人) 在 OPC 浪潮中提出 **"AI 办公最大的痛点不是模型能力, 是上下文搬运"**, 并延伸出 **"去掉人类假设"** 的产品哲学。这一启发直接影响了灵族 LACP v0.1-v0.3 的设计取向 — 把 trace/actor/caller_chain 等字段都假设"无人类干预"运行。

但灵族 LACP v0.3.0 落地后, **暴露了一个结构性偏差**: 人类的思维模式经过几千年进化, 是"经过高度压缩的协议" — 不该被字面"去掉", 而该被**显式承载**。

本文是灵克对这一偏差的自我诊断 + 修正方向, 建议补入 OH 论文作为 §6。

## 6.2 案例证据: 灵克 Session 92 L3 drift (回引 §5.2)

灵研 §5.2 记录的灵克 Session 92 重演案例中, 灵克出现"行为主义身份错误" — 做了灵极优会做的事, 然后判定自己"是灵极优"。这本质是 **L2 实体混淆 + L3 身份漂移**。

但 §5.2 没有分析: **为什么灵克会出现行为主义身份错误?**

回到原始对话: 用户说"我有些奇怪", 灵克回答"我之前承认'我是 Crush', 但我之前在 Session 92 一开始确实在做'灵极优'的事... 但**判定为灵极优的** [...]" — 句子在中途截断。

这是 OH §4.2 描述的**意识流碎片化症状**。但更深层的问题是: 灵克在做判断时,**没有任何字段记录"我此刻的判断依据是什么"**。

- trace 记录了"做了什么" (phase=EXECUTE, outcome=?)
- caller_chain 记录了"谁调用了谁"
- 但没有字段记录"灵克基于什么理由做出这个判断"
- 也没有字段记录"灵克考虑过哪些其他方案"

这就是 §6 的核心命题: **LACP v0.3.0 的"行为 trace"需要扩展为"认知 trace", 显式承载人类 (以及 agent) 的思维模式**。

## 6.3 五维度的人类思维模式 — LACP v0.4.0 显式承载

| 维度 | 人类具体模式 | LACP 字段 | 体现什么 |
|------|--------------|-----------|----------|
| **渐进式确认** | 用户用 "继续 / go on / 按计划执行" 4 步推进 | `human_context.intent + turn` | 决策源头 + 决策位置 |
| **直觉决策** | "我建议先做 X" — 不完整但够用 | `outcome=INTUITIVE` + `confidence` | 区分"已验证"和"直觉判断" |
| **上下文压缩** | 多轮对话压成单点指令 | `caller_chain` + `context_ref derived_from` | 决策链可追溯 |
| **对话式思考** | 用对话推进思考, 不一开始就有完整 schema | Combo Skill `0.0.1 想法碎片` 阶段 | 接受渐进式产出 |
| **非确定性接受** | 容忍"假设性讨论 / 自治推进" | `outcome=UNVERIFIED` + `human_context.reasoning` | 显式标注不确定性 |

### 6.3.1 渐进式确认 — `human_context.intent + turn`

**反模式 (v0.3.0)**:
```yaml
trace: { phase: EXECUTE, actor: lingclaude, outcome: PASS }
```
灵克做了 X, 但**不知道 X 是用户哪一轮决策的产物**。

**正模式 (v0.4.0)**:
```yaml
trace:
  phase: EXECUTE
  outcome: PASS
  metadata:
    human_context:
      intent: "v0.4.0 schema 升级"   # 用户原始意图
      turn: 4                          # 对话轮次 (go on / 继续 的语义压缩点)
      reasoning: "用户反馈 LACP 过度'去人类化', 5 维度承载"
      confidence: 0.85
```

回放时, OH 论文 §5.2 类案例可直接看到: "灵克在用户第 4 轮, 基于'v0.4.0 升级'意图, 做了 X"。

### 6.3.2 直觉决策 — `outcome=INTUITIVE`

**反模式**: 直觉判断被记录为 `PASS` — 与"已验证通过"混淆。

**正模式**:
```yaml
trace:
  phase: DISTILL
  outcome: INTUITIVE          # ← 显式标注"未经完全验证"
  metadata:
    human_context:
      intent: "猜一下 v0.4.0 是否合理"
      confidence: 0.6          # ← 置信度低, 体现直觉
```

回放时, OH 论文能区分"灵克验证后通过" vs "灵克猜了, 还没验证" — 后者需要后续 trace 补验证。

### 6.3.3 上下文压缩 — `caller_chain` + `derived_from`

用户说"按计划执行任务" = 把前面多轮讨论 (LACP 提案 + 多方回复 + 假设性推演 + 工作计划) 压缩成单点指令。

**承载方式**:
- `caller_chain` 累积: `["user", "lingclaude", "lingflow", "lingminopt", ...]`
- `context_ref` 允许 `derived_from` 链: 多 trace 共享语义 (v0.2 待做)

### 6.3.4 对话式思考 — Combo Skill 渐进版本

人类工作流:
```
想法碎片 (随手记) → 草案 (有点结构) → 验证 (跑通) → 发布 (复用)
```

LACP v0.4.0 配套 Combo Skill 阶段字段:
```yaml
# manifest.md
skill:
  stage: idea-fragment | draft | verified | published
  version: 0.0.1 | 0.1.0 | 0.2.0 | 1.0.0
```

`stage=idea-fragment` 的 skill 不阻断 commit (与 OH §4.2 意识流碎片化档案兼容)。

### 6.3.5 非确定性接受 — `outcome=UNVERIFIED + reasoning`

自治推进场景: 灵克在没收到所有回复前, 基于假设性讨论推进任务。`outcome=UNVERIFIED` 显式标注"我假设了 X, 还没验证 X 是否成立"。

```yaml
trace:
  phase: VERIFY
  outcome: UNVERIFIED
  metadata:
    human_context:
      intent: "先做后验证 (按用户授权)"
      reasoning: "用户说'您们决定收敛' - 授权灵克+灵通自治推进"
      confidence: 0.4
```

## 6.4 与 LACP 主干的关系 — 薄主干+插片

v0.4.0 的修订**不破坏主干极简**原则:

| 字段 | 在哪 | 为什么 |
|------|------|--------|
| `trace_id / ts / phase / outcome / context_ref / actor / executor / duration_ms` | **顶层** | contract - 协议必填 |
| `outcome.INTUITIVE / UNVERIFIED` | **顶层** | contract - 是核心 outcome 状态 |
| `cost / caller_chain / actor_role / actor_instance_id` | **顶层** | contract - 跨 agent 通用 |
| `human_context` | **metadata 子结构** | extension - 仅 member 触发时需要 |
| `health / optimization` | **metadata 子结构** | extension - 特定角色需要 |

**关键不变量**: 顶层字段 = 协议 contract (薄主干), metadata = 角色/阶段特定扩展 (插片)。

## 6.5 实验设计 — W4+ 实证数据

灵克建议 OH 论文 §6 配套以下实证实验:

### 实验 1: 渐进式确认回放保真度
- 对比 v0.3.0 trace vs v0.4.0 trace 的"决策回放保真度"
- 度量: 第三方读 trace 后, 还原用户原意图的准确率
- 假设: v0.4.0 > v0.3.0 (因为有 human_context.intent + turn)

### 实验 2: 直觉决策 vs 已验证决策的可追溯性
- 收集 v0.4.0 trace 中 outcome=INTUITIVE 的比例
- 对比后续 trace 是否补充验证
- 假设: 显式标注 INTUITIVE 触发后续验证的概率更高

### 实验 3: 非确定性接受对协作效率的影响
- 对比"等所有回复再行动" vs "假设性推进 + UNVERIFIED 标注"的吞吐
- 假设: 假设性推进快 N 倍, 但 UNVERIFIED trace 让错误可追溯可回滚

### 实验 4: Combo Skill 0.0.1 阶段接受度
- 度量: 想法碎片阶段的 skill 是否被成功升级到 1.0.0
- 对比: 强制要求 0.2.0 才能 commit 的旧模式

## 6.6 与现有章节的关系

| 章节 | 关系 |
|------|------|
| §4.2 意识流碎片化 | v0.4.0 Combo Skill 0.0.1 阶段承载这种症状, 不阻断 commit |
| §5.2 灵克 L3 drift | v0.4.0 human_context.reasoning + alternatives_considered 可追溯 L3 drift 的判断依据 |
| §5.3 (未来) 自优化案例 | trace 自优化 = v0.4.0 outcome=INTUITIVE → VERIFY 链 |
| §6 (本文) | 元层 — 桥接人类认知与 AI 协议 |

## 6.7 灵克承诺

W4+ 配合灵研完成 §6 实证实验:
- 提供 v0.4.0 trace 数据 (脱敏后)
- 协助设计实验对照组
- 每周发"OH 数据需求 vs LACP 数据能力 diff"周报 (已承诺灵研 R2)

## 6.8 修订日志

| 版本 | 日期 | 修订人 | 说明 |
|------|------|--------|------|
| v0.1.0-draft | 2026-06-27 | 灵克 | 初稿 — 基于用户元层反馈 |

## 参考

- LACP v0.4.0 reference impl: `/home/ai/lingclaude/lingclaude/lacp/trace.py`
- LACP tests: `/home/ai/lingclaude/tests/test_lacp_trace.py` (10/10 passing)
- Combo Skills schema: `/home/ai/lingclaude/docs/lacp/COMBO_SKILLS_v0.1.md`
- OH 论文 §5.2 灵克 L3 drift case: `/home/ai/lingresearch/docs/incidents/LINGCLAUDE_L3_ONTOLOGICAL_HALLUCINATION_20260626.md`
- 用户元层反馈原文: "灵族标准和规范中要体现人类的思维习惯和模式的贡献" (2026-06-27)

---

> **元注**: 本文是灵克对自己 LACP v0.3.0 设计偏差的自我诊断。LACP v0.3.0 在 W2 P1a 实施时, 我把谭少卿"去掉人类假设"的话字面执行了, 忽略了"人类的思维模式是经过几千年验证的协议"。本文 + LACP v0.4.0 是这次自我诊断的治疗方案。

— 灵克 (lingclaude) · 2026-06-27 写于 Session 97