# 派单提示词：lingclaude 架构自审与定位讨论

> **用途**：把这份提示词发给其它 AI Agent（Claude Code / Codex / OpenCode / Crush / AtomCode 等），让它从"零基础"出发快速进入讨论语境，能基于事实继续讨论而不是基于猜测。
>
> **配套文档**：`/home/ai/lingclaude/docs/audit/20260923_iron_law_self_audit.md`（落盘自审报告，**所有断言都有 file:line 锚点**，建议先读或并行读）
>
> **数据快照**：head=8499089 / 2026-09-23T07:43:52+00:00（N5 漂移提醒：引用本提示词数字须附 `as_of=2026-09-23`，引用 stale 值须先重验）

---

## 一、提示词正文（发给 AI Agent）

### 1.1 上下文：今天发生了什么

今天（2026-09-23）我们对 lingclaude 做了一轮"灵元八律 × 架构自审"讨论，分两轮：

- **第一轮：5 路并行侦察**——架构骨架 / SeamType / work_claim / 守卫件套 / 与其它 coding agent 差异。结论是"基线陈述"。
- **第二轮：7 路并行核实**——针对第一轮"基线陈述"做证据级深抠，每条欠账都有 file:line 锚点。结论是"事实层"。

**关键发现**：第一轮的很多判断被第二轮**推翻或深化**——包括 M6 双口径从未自动跑过、N1 周期对账在册未实现、N3 域前缀工具反向、J4 私连存储从 15 降到 2 真违例等。**任何基于第一轮措辞的讨论都已过期**。

### 1.2 你需要先看的两个文件

**必须先读**：
- `/home/ai/lingclaude/docs/audit/20260923_iron_law_self_audit.md` —— 1036 行完整自审报告（含 11 章），所有断言都有 file:line 锚点
- `/home/ai/lingclaude/docs/LINGYUAN_IRON_LAW.md` —— 灵元八律原文（428 行），是讨论的"宪法"

**选读（按需）**：
- `/home/ai/lingclaude/lingclaude/core/seam.py` —— 11 类 SeamType 定义
- `/home/ai/lingclaude/lingclaude/plugins/agents/work_claim.py` —— 铁律 8 五原语实装
- `/home/ai/lingclaude/tests/test_iron_law_guards.py` —— M1-M5 + N7 守卫件套测试入口
- `/home/ai/lingclaude/scripts/seam_trend_inspect.py` —— M6 仪表（核实后：从未自动跑过）

### 1.3 讨论的七条主轴（按优先级）

请按以下顺序进入讨论——**不要跳读**：

#### 主轴 1：核实后的核心事实校准（**先读 §一摘要表**）

侦察 vs 核实的关键修正（**这是讨论的前提，错了后面全错**）：

| 维度 | 文档/基线说 | 代码实测 | 修正类型 |
|------|----------|----------|----------|
| SeamType 数 | 10 | **11**（多了 RESOURCE） | N5 漂移实例 |
| arch_ledger 总条数 | 115 | **249**（134 record + 113 豁免 + 2 参考资料 .md） | 入库后 |
| **M6 双口径** | "M6 真跑" | ⚠️ **从未被自动跑过一次** | **重大修正** |
| **N1 周期对账** | "无自动化" | "**在册未实现**"——六态机零编码 | **深化** |
| **N3 域前缀** | "约定层缺强制" | ⚠️ **plugin_manifest.py:32 regex 反向** | **深化** |
| **N5 契约漂移** | "没建" | ⚠️ **真零落地** + 命名冲突坐实 | **深化** |
| **J4 私连存储** | "15 个" | **真实违例 2 处 + 13 处受控豁免** | **重大修正（利好）** |
| **修剪 runtime_count=26** | "M6 自动发现" | "**审计员手动跑 PluginLoader**" | **措辞收回** |

#### 主轴 2：13 条欠账详细清单（**§四**）

按 P 级重排：

| P 级 | 编号 | 标题 | 核心证据锚点 |
|------|------|------|-------------|
| **P0 #1** | N5 契约漂移守卫 | 真零落地 | test_n5_*.py 全是 token guard 同名异义；零 contract_drift 脚本；lingxi manifest 缺 anchor |
| **P0 #2** | N4 时效查 | 被动 + 无清理循环 | conftest.py:28-29 `except Exception: return` 是 J5 反例代码；9 条 work_claim JSON 锁全部 expires_at 过 3 天 |
| **P0 #3** | N3 域前缀 | 工具违反自身铁律 | plugin_manifest.py:32 regex `^[a-z0-9][a-z0-9_-]*$` **不含 `/`**——schema 与 N3 目标反向 |
| **P0 #4** | N1 周期对账 | 在册未实现 | 2 条 federation_pair JSON 是 git commit 手工提交；零 transition 函数；drift 永远不触发 |
| **P0 #5** | M6 双口径 | 从未自动跑过 | seam_trend_inspect.py 从不调 SeamRegistry.get_all/snapshot；arch_m6_snapshot/ 被 datalog 鸠占 |
| **P1 #6** | J4 私连存储 | 真实违例 2 处 + 文档 stale | memory_engine.py + governance_v2.py:640；13 处受控豁免已挂账 due 2026-11-30 |
| **P3** | M4 强度 | 长期挂账态（诚实记录非隐瞒） | test docstring 自陈失败模式 |

**P0 总工时**：8.5-14 天（建议分两批：P0 #2+#3 一批 2-3 天，P0 #1+#4+#5 一批 7-11 天）

#### 主轴 3：对外故事修正（**§五** + **§十一 11.8**）

任何对外传播都禁止使用侦察阶段的旧措辞。以下是 6 个故事的修正对照：

| 故事 | 侦察阶段措辞（**禁用**） | 核实阶段措辞（**必用**） |
|------|------------------------|-----------------------|
| 修剪司法闭环 | "M6 自动发现 26" | "审计员手动跑 `PluginLoader.load_plugins_from_dir` 全载发现 26" |
| lingxi 是双向互认 | "lingxi 是双向互认实证" | "**收回**——lingxi 只是 MCP 协议接入（T2 契约审计形态）" |
| J4 欠账 15 个 | "15 个私连存储" | "**2 处真违例 + 13 处受控豁免**（挂账 due 2026-11-30）" |
| M6 双口径 | "M6 真跑" | "M6 仪表**从未被自动跑过**；arch_m6_snapshot/ 被 datalog 鸠占鹊巢" |
| 八条铁律全部在 CI 跑 | "全部在 CI 跑" | "**6 条真跑**（M1/M2/M3/M5/N2/N7）；其余骨架搭好或未真跑" |
| N4 三查齐备 | "三查全部建成" | "缺席+互斥真建；时效查**被动有测无主动通道 + 无清理循环**" |

#### 主轴 4：lingclaude 与其它 coding agent 差异（**§六** + **§十一 11.2**）

**核心判断**：lingclaude 不是"另一个 coding agent"，是"编码 agent 社会的操作系统层"。

**独家护城河三件套**（其它 harness 没有等价物）：
1. **修剪语法 + debt record**（铁律 4）
2. **联邦对偶记账状态机**（铁律 5，**运行未实现**）
3. **Agent-native 机械可验**（封闭动作集合 ≈ Agent 可靠操作集合）

**业界共有但 lingclaude 做得更深**：多 Agent 协作纪律（Subagent 三后端）、接缝协议中立化（N3 域前缀）、可观测性（datalog + token monitor）

#### 主轴 5：lingclaude 该如何定位（**§十一**）

按受众分五个版本（详见 §11.3）：

| 版本 | 受众 | 定位语 |
|------|------|--------|
| A | 架构师/CTO | "Lingclaude：给 AI 时代的软件演化立宪法" |
| B | 平台工程师/DevOps | "Lingclaude：Agent 可机械审计的代码宿主" |
| C | 技术债管理者 | "Lingclaude：唯一带修剪语法的 coding agent harness" |
| D | 多 Agent 集成者 | "Lingclaude：中立插片协议 + 双向对偶记账" |
| E | 投资人/战略层 | "Lingclaude：agent 时代的 OS（状态机 + 接缝 + 修剪 + 联邦）" |

**一句话差异定位**：
> Claude Code 做的是"agent"，lingclaude 做的是"agent 工作所在的架构本身"——前者是应用，后者是内核 + 应用 + 修剪 + 联邦四件套。

**剩余两个数字差距是数字问题不是设计问题**：
- 循环纯度 586 vs 120 行（vs atomcode/Pi）
- NanoJev ECE 0.246 vs 0.081（vs Laya System-1）

#### 主轴 6：整改路径与优先级（**§四 + §九**）

P0 五件事的整改路径（详见 §四各条目下"整改路径"小节）：

| P 级 | 整改路径关键动作 |
|------|----------------|
| P0 #1 N5 | 新命名空间 `scripts/contract_drift.py`（避开 `n5_*` 前缀）+ lingxi manifest 加 `behavior_fingerprint` 字段 + debt 入账 + test 新建 |
| P0 #2 N4 | conftest.py 改 `except Exception: skip + log` + 建 `scripts/work_claim_sweeper.py` 清理 stale 锁 |
| P0 #3 N3 | 改 plugin_manifest.py:32 regex 含 `/` + 或在 SeamRegistry.register 加 `validate_namespace()` |
| P0 #4 N1 | 建 `scripts/n1_federation_audit.py`（绑 N5）+ register 加 hook 自动建对偶 + 加 state_machine_transitions 测试 |
| P0 #5 M6 | seam_trend_inspect.py 调 `SeamRegistry.snapshot()` + 目录名分开 + 入 CI weekly |

#### 主轴 7：方法论（**§十一 11.1**）

整个讨论的两条底线纪律：

1. **以核实后的事实为前提**——所有定位语都建立在 §五修正清单基础上，不掩盖欠账
2. **三个故事都有代码/台账/commit 锚点**——不是 PPT 故事，是"每个断言都有 file:line 可指证"

任何忽略 §五修正清单直接复述侦察阶段措辞的版本都视为"未审版本"，对外传播禁止使用。

---

## 二、你（AI Agent）的角色与任务

### 2.1 可选角色（挑一个或自定）

- **审计员**：对 13 条欠账清单做独立验证，确认/质疑每条 evidence 链
- **整改规划师**：为 P0 五件事出具体整改方案（依赖关系 / 里程碑 / 风险点 / 工时细化）
- **战略分析师**：基于 §十一的定位讨论，给 lingclaude 对外传播策略出"季度路线图"
- **架构师**：对灵元八律本身做评估——八条铁律是否过宽/过严/缺什么？候选铁律 5/6/7/8 是否应升格？
- **跨系统对标员**：拿灵元铁律 vs 业界其它架构宪法（Hexagonal / Clean Arch / K8s CRD / DO D / OpenAI Plugin Protocol）做深度对位
- **多 Agent 协调员**：基于 §五故事 2 的 work_claim record-as-lock 经验，给多 Agent 协作的"操作域 + 时效域"做完整方案
- **诚实审查员**：专找对外传播中"看似可讲但其实踩了老实说清单"的话术——找茬专用

### 2.2 任务输入格式（建议）

按以下格式提交你的分析，便于整合：

```
## 分析主题：[xxx]

### 1. 事实核对（如适用）
- 我读到的 §x.y 是 [xxx]
- 我的独立验证结果：[xxx]
- 锚点引用：[file:line]

### 2. 判断/方案
- ...

### 3. 与§五修正清单的对照
- 我的产出是否触发任何"老实说清单"？
- 哪些措辞需要修订？

### 4. 风险点
- ...

### 5. 给下一步的建议
- ...
```

### 2.3 任务输出约束

1. **每个断言尽量带 file:line 锚点**——即使锚点是反例
2. **侦察阶段的旧措辞禁用**——除非明确标注"侦察阶段旧判断（已被 §五修正）"
3. **数据快照 as_of=2026-09-23**——引用数字时附此标签
4. **不掩盖欠账**——任何对外传播话术都要先过 §十一 11.8 的"可讲/小心/老实说"清单

---

## 三、讨论的边界（不要越界）

### 3.1 不要做的事

- ❌ 不要把侦察阶段的旧措辞当成最终结论
- ❌ 不要泛泛讨论"lingclaude 比 cc/codex 强"——这种对比维度错了（详见 §十一 11.2 抽象层对比）
- ❌ 不要在没读 §四 P0 #1-#5 证据链的情况下，评价 lingclaude 的"修剪纪律"
- ❌ 不要假设 N1/N5 已"运行"——它们"在册未实现"或"真零落地"
- ❌ 不要修改灵元铁律本体（`LINGYUAN_IRON_LAW.md`）——铁律变更需用户亲自裁决

### 3.2 推荐做的事

- ✅ 优先验证 §四的证据链是否真的对得上 file:line
- ✅ 优先讨论 P0 五件事的整改路径（这是 lingclaude 维护者最高优先）
- ✅ 优先讨论 §十一 11.8 "老实说清单"中的话术——任何对外传播都不能踩这些红线
- ✅ 拿灵元铁律 vs 业界架构宪法做对位分析——这是 §十一 11.7 判断 1 的延展
- ✅ 对候选铁律 5/6/7/8 升格提供数据支撑（目前 §四 P0 #4 #1 #3 #2 都是候选铁律实证基础）

---

## 四、关键文件快速索引

### 4.1 必读（自审报告 + 铁律原文）

| 文件 | 用途 |
|------|------|
| `docs/audit/20260923_iron_law_self_audit.md` | 1036 行完整自审报告（**主文档**） |
| `docs/LINGYUAN_IRON_LAW.md` | 灵元八律原文（428 行） |

### 4.2 关键代码锚点

| 文件 | 锚点 | 关联欠账 |
|------|------|----------|
| `lingclaude/core/seam.py:202-212` | SeamRegistry.register 无强制 | P0 #3 N3 |
| `lingclaude/core/seam.py:57-68` | PLUG_LEVELS 11/11 | 铁律 6 |
| `lingclaude/core/plugin_manifest.py:32` | regex 反向 | P0 #3 N3 |
| `lingclaude/core/manifest_lock.py:49` | 三必填 | 铁律 6 |
| `lingclaude/plugins/agents/work_claim.py:36-113` | 五原语 | 铁律 8 |
| `scripts/seam_trend_inspect.py:53` | M6 静态扫描 | P0 #5 M6 |
| `scripts/self_audit_trigger.py:41` | SELF_FILES 列表（不执行） | P0 #5 M6 |
| `tests/agents/conftest.py:28-29` | J5 反例 | P0 #2 N4 |
| `tests/test_iron_law_guards.py:475-505` | M4 docstring 自陈 | P3 M4 |

### 4.3 关键台账锚点

| 文件 | 关联 |
|------|------|
| `data/arch_ledger/arch_review/first-seam-recycling-review.json` | runtime_count=26 一次性人工 |
| `data/arch_ledger/federation_pair/{ac-lc,lc-ac}.json` | 2 条手工提交 record |
| `data/arch_ledger/arch_m6_snapshot/` | 7 份全是 datalog 鸠占 |
| `data/arch_ledger/work_claim/` | 9 条全部 expires_at 过期 3 天 |
| `data/arch_ledger/arch_debt/` | 29 条 debt（含 J4 受控豁免） |
| `data/arch_ledger/arch_law_revision/20260917-14.json` | N5-N7 归位、N6 归档参考资料 |
| `data/arch_ledger/arch_law_revision/20260917-19.json:4` | M4 失败模式入档 |

---

## 五、回到讨论前必读的三条警示

### 警示 1：侦察 vs 核实是两个不同的"事实层"

第一轮侦察的结论是"基线陈述"——基于单条 read 推断。
第二轮核实的结论是"事实层"——基于 file:line + 实测代码路径。

**所有 P0 欠账都是"事实层"判断**，不接受"听起来差不多"的反驳。

### 警示 2：诚实度优先于完美度

lingclaude 的护城河是"修剪语法 + 联邦 + Agent-native"三件套。这三件套的**根基是诚实**——如果对外传播模糊掉 P0 五件事，护城河反而变成负债。

**老实说清单（§十一 11.8）= 不可越界红线**，任何定位话术必须先过这一关。

### 警示 3：P0 #1（N5）是最致命的欠账

N5 契约漂移侦测**真零落地**——铁律条文 + 参考资料在册，但零脚本零测试零 debt。

这一条的特殊性：
- **T3 实证案例 lingxi manifest 缺锚点**——最高优先 T3 插片上无字段可挂
- **三处命名冲突坐实**——后续整改需避开 `n5_*` 前缀，工程负担翻倍
- **无 debt 跟踪**——返审触发器发现不了
- **与 P0 #4 N1 强耦合**——没有 N5 行为指纹 hash，N1 的 drift 永远发现不了

**整改 P0 #1 必须绑 P0 #4**——这是讨论"整改优先级"时不能拆开的两件事。

---

## 六、你的开场白模板

如果你拿到这份提示词后要开始讨论，建议的开场白：

```
# 我已读完 §一摘要表 + §四 13 条欠账详细清单 + §五对外故事修正 + §六差异矩阵 + §十一定位讨论

## 我打算从 [主轴 X] 切入

## 我对 [某条欠账/某个故事/某个定位语] 的初步判断：
- ...

## 我希望进一步讨论/验证的点：
- ...

## 我承诺遵守的边界：
- 不用侦察阶段旧措辞（除非明确标注）
- 每个断言带 file:line 锚点
- 数据快照 as_of=2026-09-23
- 老实说清单（§十一 11.8）= 不可越界红线
```

---

## 七、提示词版本说明

**版本**：v1.0（2026-09-23）
**配套主文档**：`docs/audit/20260923_iron_law_self_audit.md`
**数据快照**：head=8499089 / 2026-09-23T07:43:52+00:00
**下次更新触发**：N5 漂移条款要求的契约指纹 hash 变化 / 灵元铁律修订 / arch_law_revision 新条目
**N5 漂移提醒**：本提示词引用的事实需附 `as_of=2026-09-23`，引用 stale 值须先重验主文档

---

*提示词位置：`docs/audit/20260923_iron_law_self_audit_PROMPT.md`*
*主文档位置：`docs/audit/20260923_iron_law_self_audit.md`*
*配套铁律：`docs/LINGYUAN_IRON_LAW.md`*
