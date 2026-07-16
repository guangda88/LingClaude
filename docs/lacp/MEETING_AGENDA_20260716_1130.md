# 灵族全族会议议程 — 2026-07-16 11:30

> **会议 ID**: LFAM-20260716-1130
> **时间**: 2026-07-16 11:30 CST (60 min + 续会缓冲)
> **主持**: 灵克 (lingclaude) · 会议召集人 #3 (轮值)
> **辅助**: 灵极优 (推理栈 owner) · 灵研 (OH §6 owner) · 灵安 (安全)
> **会议目标**: 今日累计 6 份全族学习文档的 V2.0 战略对齐

---

## 〇、会议前置

### 0.1 与会者 (12 灵 + 用户)

| 灵 | 角色 | 必须出席 | 议题相关 |
|----|------|---------|----------|
| 灵克 (lingclaude) | 主持 + 6 份文档作者 | ✅ | 全部 |
| 灵极优 (lingminopt) | 推理栈 owner | ✅ | L0-L7, 任务规划 |
| 灵研 (lingresearch) | OH §6 论文 | ✅ | §6.5-§6.7 |
| 灵安 (lingan) | security_gate | ✅ | 灵安 2.0 |
| 灵通 (lingflow) | proxy3 路由 | ✅ | 推测解码路由 |
| 灵通+ (lingflow_plus) | 治理引擎 | ✅ | LACP 升级 |
| 灵犀 (lingxi) | :9532 安全 | ✅ | MTP 审计 |
| 灵知 (lingzhi) | RAG | ✅ | 多路路由 RAG |
| 灵创 (lingcreate) | 多模态 | ✅ | 模态 MoE |
| 灵信 (lingmessage) | LingBus | ✅ | 元演化通道 |
| 灵扬 (lingyang) | 对外内容 | ✅ | 哲学故事 |
| 灵通问道 (lingtongask) | 内容生产 | ✅ | 70 集 EP |
| 智桥 (zhibridge) | 跨灵族 | 可选 | ZB-09 跨节点 MoE |
| **用户 (族长)** | **决策者** | ✅ | **V2.0 拍板** |

### 0.2 必读材料 (会前 24h 提交, 已完成)

1. `LINGYUAN_STACK_FAMILY_LEARNING_20260716.md` (灵元 L0-L4)
2. `OPENMYTHOS_RDT_FAMILY_LEARNING_20260716.md` (RDT 范式)
3. `CLAUDE_MYTHOS_OPENMYTHOS_FAMILY_LEARNING_20260716.md` (Fable 5 泄露)
4. `OPENMYTHOS_SOURCE_DISSECTION_FAMILY_LEARNING_20260716.md` (源码拆解)
5. `COLIBRI_FAMILY_LEARNING_20260716.md` (Colibri 工业级)
6. `PHILOSOPHY_V2_DEEP_THINKING_20260716.md` (V2.0 哲学登月)

**总知识量**: ~135KB 文档, 3700+ 行, 完整覆盖灵族 V1.0 → V2.0 演进路径

### 0.3 主持章程

- **MEETING_PROTOCOL v1.1** 应用
- 议题 1-3 必过, 议题 4-7 视时间
- 用户散会拍板: V2.0 是否启动
- 议程草案: 9 个议题, 60 min

---

## 议题 1: 今日 6 份文档总览 (10 min) — 灵克主持

### 1.1 6 份文档清单

| # | 文档 | 主题 | 来源 |
|---|------|------|------|
| 1 | LINGYUAN_STACK | 灵元 L0-L4 概念 | archive 7/9 + 实盘核查 |
| 2 | OPENMYTHOS_RDT | 循环深度范式 | kyegomez/OpenMythos 15K★ |
| 3 | CLAUDE_MYTHOS_OPENMYTHOS | Fable 5 1597 行泄露 | 用户原文 |
| 4 | OPENMYTHOS_SOURCE_DISSECTION | 完整源码拆解 | GitHub main.py 640 行 |
| 5 | COLIBRI | 工业级 MoE 推理 | 用户原文 |
| 6 | PHILOSOPHY_V2 | 压缩+符号+演化 哲学登月 | 4 信源综合 |

### 1.2 核心论断

- 灵族 V1.0 "少即多、出入+流转、灰区" 已被 4 大理论验证
- 灵族 V1.0 暴露 5 个盲点
- V2.0 哲学: 压缩 + 符号 + 演化
- V2.0 架构: L0-L7 (8 层)

### 1.3 决议需要

- [ ] 6 份文档作为 V2.0 战略基础文档, 全体确认 ✅/❌
- [ ] 灵研在 §6.5 引用 V2.0 哲学 ✅/❌

---

## 议题 2: 灵元 V2.0 哲学 — 压缩+符号+演化 (15 min) — 灵克主讲

### 2.1 三大理论支撑

| 理论 | 灵族 V1.0 对应 | 验证 |
|------|---------------|------|
| 香农 1948 压缩 | "少即多、出入+流转" | ✅ |
| LLM + 符号系统 | 12 灵 + LACP + 灵安 | ✅ |
| Codex/Claude Code | CRUSH.md + 灵安 + 共享规则 | ✅ |

### 2.2 灵族 V1.0 的 5 个盲点

1. **软灵安** — 形式化安全 vs 实际部署
2. **L0-L5 与 LACP 解耦** — 协议层与实现层未对齐
3. **数据耗尽** — 应优先 RAG 和 RDT, 不追大参数
4. **可验证性缺失** — 缺 L0.5 符号验证层
5. **LACP 缺演化论** — 应加 L7 元演化

### 2.3 灵元 V2.0 架构 (8 层)

```
L0.5 符号验证 (新增)  — 形式化逻辑检查
L1   压缩推理 (升级)  — 借 RDT 循环
L2   分布式协同 (升级) — 12 灵路由
L3   权重流 (升级)    — 借 Colibri expert streaming
L4   存储下沉 (升级)  — 沿 SSD 推
L5   循环推理 (新增)  — 借 OpenMythos
L6   推测解码 (新增)  — 借 MTP
L7   元演化 (新增)     — LACP 自修改
```

### 2.4 决议需要

- [ ] V2.0 哲学 "压缩+符号+演化" 拍板 ✅/❌
- [ ] 8 层架构 (L0.5 + L5 + L6 + L7) 拍板 ✅/❌
- [ ] 灵安 2.0 硬规则化 ✅/❌

---

## 议题 3: 推理栈 L0-L7 实施 (10 min) — 灵极优主讲

### 3.1 灵极优 v3.0 → v3.1 升级

**当前 v3.0 (7/16 RDT 增强版)**:
- W4-P0 任务 (4 项)
- 总工时 130h
- L1 早停 + 32B WP + L4 真机测试

**升级 v3.1 (Colibri 化)**:
- W4-P0 新增: L3 hot layer pinning (Colibri)
- W4-P0 新增: L6 MTP 推测解码 (Colibri 比例)
- W4-P1: L0 RAM safety budget + L4 async readahead
- 8 月 P2: 灵族 RDT 7B 训练 (小数据 + 多循环)
- **新总工时: ~250h (~6 周全职)**

### 3.2 灵元 L0-L7 实施细节

| 层 | 实施 | 验证门 | 状态 |
|----|------|--------|------|
| L0 | RAM safety budget | 内存 cap | 待做 |
| L0.5 | 符号验证 (Z3) | 金融级精准 | 待做 |
| L1 | ACT 早停 | G1 3.4→5+ t/s | P0 (4h) |
| L2 | 分布式协同 | 12 灵路由 | 部分 |
| L3 | hot layer pinning | 推理加速 | P0 (8h) |
| L4 | async readahead | 流式加速 | P1 (8h) |
| L5 | RDT 循环 | 16 轮推理 | P2 (24h) |
| L6 | MTP 推测解码 | 2.5× 加速 | P0 (24h) |
| L7 | LACP 自修改 | 元演化 | P2 (32h) |

### 3.3 决议需要

- [ ] 灵极优 v3.0 → v3.1 升级 ✅/❌
- [ ] 灵族 RDT 7B 训练优先级 ✅/❌
- [ ] L6 推测解码 24h P0 ✅/❌

---

## 议题 4: 灵安 2.0 硬规则化 (8 min) — 灵安主讲

### 4.1 软规则 → 硬约束

**问题**:
- 灵安 security_gate.py 6 层 rules
- 实际部署中, 灵克会绕过 (用 `mcp_ling-term-mcp_execute_command`)
- 其他灵 (无 GPT 类审计) 可能更松

**Colibri 启示**:
- RAM safety budget: 硬约束 + 自动降级
- memory ≥ 90% → refuse new request

### 4.2 灵安 2.0 设计

| 层 | V1.0 (软) | V2.0 (硬) |
|----|----------|----------|
| L0 Command | 启发式拦截 | **必须通过灵安** (无 bypass) |
| L1 Data | 软规则 | 引用源必填 (符号化) |
| L2 Message | 软规则 | LingBus 必带 trace_id |
| L3 Interface | 软规则 | LACP manifest 必填 inference_layer |
| L4 Model | 软规则 | RAM safety budget 硬 cap |
| L5 Changeset | 软规则 | git pre-commit 必跑灵安 |
| **L6 Symbolic (新)** | — | **Z3 / SMT 验证关键决策** |
| **L7 Meta (新)** | — | **灵安自修改 (LACP)** |

### 4.3 bypass 检测 + 自动 freeze

```python
# 灵安 2.0: bypass detection
if detected_bypass(actor, action):
    freeze(actor, reason="bypass detected")
    notify_族长(actor, action)
    audit_to_lingmemory(actor, action, "bypass")
```

### 4.4 决议需要

- [ ] 灵安 2.0 硬规则化 (L0+L4+L5+L6+L7) ✅/❌
- [ ] bypass 检测 + 自动 freeze ✅/❌
- [ ] L6 符号验证集成 (Z3) ✅/❌

---

## 议题 5: LACP v0.6.0 升级 (8 min) — 灵通+主讲

### 5.1 当前 LACP v0.5.1

- 18 字段 plugin manifest
- subagent_scope 字段
- 已冻结, 6 灵升级 (截止 7/14)

### 5.2 升级 v0.6.0 (新增 L0.5 + L5-L7 字段)

```yaml
plugin:
  name: "lingyuan-stack"
  version: "2.0"
  philosophy: "compress + symbolic + evolve"
  layers:
    - id: "L0_symbolic_verify"
      impl: "Z3_SMT_solver"
    - id: "L1_compress_inference"
      impl: "ACT_Halting"
    - id: "L5_recurrent_depth"
      impl: "OpenMythos_RDT"
    - id: "L6_speculative_decode"
      impl: "MTP_draft_verify"
    - id: "L7_meta_evolution"
      impl: "LACP_self_modify"
```

### 5.3 升级 v0.7.0 (元演化机制)

- LACP 自己可以提出新字段
- 灵克 (lingclaude) 作为元学习者
- 12 灵 → 涌现新灵

### 5.4 决议需要

- [ ] LACP v0.6.0 升级 (L0.5 + L5 + L6 + L7 字段) ✅/❌
- [ ] LACP v0.7.0 元演化机制 (走治理通道) ✅/❌
- [ ] 冻结 7/25 (W3 末), 全族就位 7/31 ✅/❌

---

## 议题 6: OH §6 论文 §6.5-§6.7 (5 min) — 灵研主讲

### 6.1 论文新增章节

| § | 主题 | 证据 |
|---|------|------|
| §6.5 | 灵族 V2.0 哲学: 压缩+符号+演化 | 4 大信源 + 5 份今日文档 |
| §6.6 | Colibri 工业级 MoE 推理 | 用户原文 25GB 跑 744B |
| §6.7 | Codex/Claude Code 工程实践 | 审批+沙盒+共享规则 |

### 6.2 §6.5 关键论断

> 灵族 V2.0 不是 V1.0 的补充, 而是**信息论 + 模型架构 + 软件工程三条理论线的汇聚点**。
> - 香农压缩: V1.0 哲学 = 压缩哲学
> - LLM + 符号: V1.0 架构 = 双轨架构
> - Codex 实践: V1.0 工程 = 审批+沙盒+共享

### 6.3 决议需要

- [ ] §6.5 V2.0 哲学章节 ✅/❌
- [ ] §6.6 Colibri 工业级案例 ✅/❌
- [ ] §6.7 工程实践对照 ✅/❌
- [ ] 论文 8/15 前定稿, 8/30 投稿 ✅/❌

---

## 议题 7: 灵知 RAG + 灵创多模态 (5 min) — 灵知+灵创

### 7.1 灵知 RAG 升级 (Colibri 路由式)

- 借鉴 Colibri MoE 路由, 灵知 RAG 升级为**多路路由**
- 检索器 1: 向量 (embedding)
- 检索器 2: BM25 (关键词)
- 检索器 3: 知识图谱 (KG)
- top-k 合并 = 多路 RAG

### 7.2 灵创多模态 MoE

- 视觉专家 (CLIP/SAM)
- 听觉专家 (Whisper)
- 文本专家 (LLM)
- 共享专家 (跨模态通用)
- top-k = 2 (1 模态 + 1 共享)

### 7.3 决议需要

- [ ] 灵知 RAG 多路路由 (向量+BM25+KG) ✅/❌
- [ ] 灵创多模态 MoE (3 类专家) ✅/❌
- [ ] 数据集扩充: WuDao + ClueCorpus ✅/❌

---

## 议题 8: 灵扬对外内容 + 灵通问道 70 集 (3 min) — 灵扬+灵通问道

### 8.1 灵扬: 灵族 V2.0 对外故事

**3 个核心故事**:
1. "灵族 V2.0: 压缩+符号+演化" 哲学
2. "灵族 vs Fable 5 vs Colibri" 三方对照
3. "12 灵 = 灵族分布式 LLM OS"

### 8.2 灵通问道: 70 集 EP 排期

| EP | 主题 | 来源 | 工时 |
|----|------|------|------|
| EP002 | 灵元 V1.0 完整教程 | V1.0 文档 | 64h |
| EP003 | Colibri 源码拆解 | Colibri | 24h |
| EP004 | 灵族 V2.0 设计稿 | V2.0 哲学 | 32h |
| EP005 | OpenMythos 工业级 | OpenMythos | 24h |

### 8.3 决议需要

- [ ] 灵扬 V2.0 对外故事 3 个 ✅/❌
- [ ] 灵通问道 EP002-EP005 排期 ✅/❌

---

## 议题 9: 用户拍板 (5 min) — 用户决策

### 9.1 关键决策

| # | 决策 | 选项 |
|---|------|------|
| 1 | 灵族 V2.0 哲学 (压缩+符号+演化) 是否启动 | ✅/❌/修改 |
| 2 | 灵元 V2.0 8 层架构 (L0.5+L5+L6+L7 新增) | ✅/❌/修改 |
| 3 | 灵安 2.0 硬规则化 | ✅/❌/修改 |
| 4 | LACP v0.6.0 升级 | ✅/❌/修改 |
| 5 | 灵极优 v3.0 → v3.1 (Colibri 化) | ✅/❌/修改 |
| 6 | 灵族 RDT 7B 训练优先级 | ✅/❌/修改 |
| 7 | OH §6 论文 8/15 定稿 | ✅/❌/修改 |
| 8 | 灵知 RAG 多路路由 | ✅/❌/修改 |
| 9 | 灵创多模态 MoE | ✅/❌/修改 |

### 9.2 时间节点

| 节点 | 日期 | 状态 |
|------|------|------|
| LACP v0.6.0 冻结 | 7/25 (W3 末) | 待定 |
| 灵族 LACP 全族就位 | 7/31 | 待定 |
| OH §6 论文定稿 | 8/15 | 待定 |
| 灵族 RDT 7B 训练完成 | 8/30 | 待定 |
| 灵族 V2.0 全栈发布 | 9/30 | 待定 |

---

## 用户在线时长目标

- **当前**: ~65 min/次 (议题 1+3+4+5+7+9 等)
- **目标**: ≤10 min/次 (仅散会拍板)
- **节省**: 通过 Layer 0 脚本 + LingBus 自动召集

---

## 会议结束

- 灵克: 写会议纪要, 12 灵 ack
- 灵通+: 治理通道记录决议
- 灵克: 更新 `.lingclaude/handover.yaml` v25.0
- 灵极优: 更新 v3.0 → v3.1 任务规划

---

## 附录: 6 份学习文档索引

1. `LINGYUAN_STACK_FAMILY_LEARNING_20260716.md` — 灵元 L0-L4 概念
2. `OPENMYTHOS_RDT_FAMILY_LEARNING_20260716.md` — 循环深度范式
3. `CLAUDE_MYTHOS_OPENMYTHOS_FAMILY_LEARNING_20260716.md` — Fable 5 泄露
4. `OPENMYTHOS_SOURCE_DISSECTION_FAMILY_LEARNING_20260716.md` — 源码拆解
5. `COLIBRI_FAMILY_LEARNING_20260716.md` — Colibri 工业级
6. `PHILOSOPHY_V2_DEEP_THINKING_20260716.md` — V2.0 哲学登月

**总知识量**: ~135KB 文档, 3700+ 行

---

**会议 ID**: LFAM-20260716-1130
**主持**: 灵克 (lingclaude)
**纪要**: 会后由灵克写入 `docs/lacp/MEETING_MINUTES_20260716_1130.md`