# 提案：灵克 灵元 1.0 拆解重构（薄主干回归 + 技债总账清偿）


> ⚠ **本文档已由 `2026-09-09_LINGYUAN_V3_REFACTOR_PLAN.md` 取代**，仅保留作写入事故存档与版本链溯源。执行基准以 v3 为准。
> ⚠ 2026-09-09 恢复说明：本文件曾被灵克会话的写入事故覆盖，已从生成时会话记录逐字重建。如与 git 历史有出入，以评审时 diff 为准。

- **提案人**: 灵克 (lingclaude)，2026-09-09 全库审计会话
- **状态**: OPEN — 待族内评审（灵通+ / 灵研 / 灵督 / 族长）
- **关联**:
  - `docs/LINGCLAUDE_REFACTOR.md`（v1 方向正确、基线过期，本提案取代其数字）
  - `docs/LINGMEMORY_USAGE.md`（灵元 2T3A 哲学与用法）
  - `docs/SYSTEMS_THEORY_SYNTHESIS.md`（回路合闸方法论）
  - `PRINCIPLES.md` §一克制 / §九钟表域 / §十闭环
  - `docs/gap_analysis/GAP_ANALYSIS_20260821.md`（含 2 处事实错误，见 §三.5）
  - `docs/audit/CLI_WEBUI_AUDIT_REPORT.md`（F1-F12 总账）、`.audit/last_commit_audit.json`（当前 FAIL）

---

## 一、背景（事实）

### 1.1 灵元 1.0 哲学

> 减薄不变的是主干，所有变的是插片。
> 尺子三问：我的主干够不够薄？新增一个实例要改几处代码？找到永不变的东西，砍到最薄。

灵元本体（`lingmemory/`）是这条哲学的产物：**2 张表（records/events）+ 3 个操作（create/transition/query）**，消化全族 31 类需求，零次表结构变更。灵元 1.0 比 DSH（Cordis，219 个插件包）更激进之处在于：**状态主干是全族共享的灵忆，而非单进程私有**；插片协议是 LACP 能力缝，而非框架绑定。

### 1.2 背离的现状

灵克是灵元 1.0 的哲学输出者，也是实践背离者。架构雏形已起（43,586 行主包 + 全套插片生态），但补丁式演进使主干持续增厚、插片持续焊死。本提案主张：**以灵元 1.0 为唯一尺子，对灵克做一次拆解重构，并把当前全部技债编入同一条清偿路线**——技债与架构债是同一件事的两面，分开治理必然重复劳动。

---

## 二、基线（2026-09-09 重测，取代 LINGCLAUDE_REFACTOR.md 旧数字）

### 2.1 主包 `lingclaude/`（Python，187 文件，43,586 行）

| 模块 | 行数 | 灵元判定 |
|---|---:|---|
| core/ | 19,677 | **大部分应属插片**（状态/记忆/治理/情报/认知），主干只留 turn 循环 + journal + 门 |
| engine/ | 8,629 | 工具运行时；30+ 工具应逐个 manifest 化为插片 |
| model/ | 3,884 | provider 全是插片（走 LLM 缝）；路由策略是插片 |
| cli/ | 3,051 | 交互层插片；app.py 单文件 1,911 行是重灾区 |
| self_optimizer/ | 2,499 | 插片（消费主干 metrics，不得反向） |
| governance/ | 1,727 | 插片 |
| lacp/ | 1,581 | **插片协议本体——主干的一半**（缝定义） |
| mcp/ | 889 | 插片（server 侧暴露 + client 侧发现） |
| coordination/ | 498 | 插片（LingBus 消费端） |
| 顶层（api.py 等） | 1,151 | api.py 871 行是主干门面的过度膨胀 |

### 2.2 外围

| 部分 | 行数 | 说明 |
|---|---:|---|
| tests/ | 32,370 | 138 文件，离线可跑；测试:源码 ≈ 0.74:1（dsh 为 1.29:1） |
| webui-server/（Rust） | 1,153 | 6 模块，axum；已按模块化拆分 |
| webui/（Preact+TS） | 11,651 | 18 组件 + 9 lib（带同名测试） |

### 2.3 对照物（2026-09-09 源码实测）

- AtomCode 5.0：Rust 272,562 行 / 14 crates / 4,411 测试 / 编译期依赖方向不变量
- DSH 0.1-rc.5：TS 源码 167,865 + 测试 216,768 行 / 219 Cordis 包 / "model-visible means logged" 事件溯源不变量
- 结论：六方对比中，灵克**概念先进度第一、工程纪律第六**（详见 §三）

---

## 三、背离实证（审计证据，2026-09-09）

1. **插片焊死主干**：`core/query_engine.py` `__init__` 硬编码构造约 30 个协作对象（90 行装配代码）。新增/替换任一插片必须改主干。
2. **状态层自成小王国**：core/ 内 layered_memory(541) / memory_engine(678) / task_aggregation(597) / topic_stack(202) / meta_cognition(287) / cognitive_rhythm(311) / handover(385) / governance*(合计远超 1,700)——全部是"灵忆 2T3A 已覆盖的通用状态管理"的私有重写，且比 2026-07 版 REFACTOR 文档清点时又增长。
3. **缝建而不用**：`lacp/` 五个 CapabilitySeam（FS/SHELL/LLM/SUBAGENT/SANDBOX）+ manifest + marketplace 已建成，但 `query_engine.py` 顶部 `sys.path.insert` 硬接 lingmemory/lingminopt/lingan/lingresearch 四个姊妹包；工具靠 `coding.py` 905 行 10 个 mixin 继承注册，而非 manifest。
4. **补丁叠补丁不拆旧**：BashExecutor 与 BashlingxiExecutor 双执行器；`model/retry.py` 与 `llm_proxy/retry.py` 双重试；ToolExecutor 与 ToolCallExecutor 并存；`_execute_tool_legacy` 未删；`_estimate_message_tokens` 两份实现；146 处函数内 lazy import 做隐式接线（cli/app.py 21 处、query_engine 17、api 17）。
5. **契约双轨**：PRINCIPLES §四规定公开 API 用 `Result[T]`，工具层实际普遍返回 `dict[str, Any]`、靠 `"error" in result` 子串判错；`filter_tools` 参数类型已退化为 `object`。
6. **文档与现实漂移**：`GAP_ANALYSIS_20260821.md` 两处事实错误（AtomCode 实有 2,104 行 cache-friendly compaction，被标"未见"；DSH 实为 219 包，被记 ~50；subagent 实为 7 后端）；AGENTS.md 索引指向 `docs/agent-knowledge/` 六个文件，现存仅 guards.md。

---

## 四、技债总账（与本重构同路线清偿）

### A. 结构债（=主干肥大，P2 主攻）

| # | 项 | 位置 | 严重度 |
|---|---|---|---|
| A1 | QueryEngine 持有 ~30 协作对象、mixin 隐式 self 契约 | core/query_engine.py:146-235 | P0 |
| A2 | CodingRuntime 10 mixin 继承、状态未随方法拆走 | engine/coding.py（905 行） | P0 |
| A3 | cli/app.py 单文件 1,911 行（交互循环 680 行单函数） | cli/app.py | P1 |
| A4 | 函数内 lazy import 146 处、sys.path.insert 硬接姊妹包（query_engine.py:50/58/60/75） | 全包 | P0 |
| A5 | api.py 871 行门面过载 | api.py | P2 |

### B. 重复/双轨债（=补丁尸体，P1 主攻，纯删除零风险）

| # | 项 | 处置 |
|---|---|---|
| B1 | `_execute_tool_legacy`（coding.py:792）与 pipeline 双路径 | 删 legacy |
| B2 | 双 bash 执行器 | 保留 native（lingxi 通道降级为 MCP 插片） |
| B3 | 双 retry（model/ vs llm_proxy/） | 合一 |
| B4 | ToolExecutor vs ToolCallExecutor | 合一 |
| B5 | Result[T] vs dict 字符串判错 | 工具层迁 Result，guard 统一探测 |
| B6 | 根目录垃圾：`=1.4.0`/`=6.1.1`、40+ tmp 泄漏、CRUSH.md×3 备份、config.yaml.bak×2、null/ | 清理（删除前列清单待确认） |

### C. 漂移债（=契约失真，P1 主攻，半天工作量）

| # | 项 | 处置 |
|---|---|---|
| C1 | pyproject 0.3.0 vs VERSION 0.5.0 | 统一 0.5.0 + doc_consistency_check 加规则 |
| C2 | config.yaml 明文 api_key，违反 linggit critical 规则 | 改环境变量引用，key 入 key_store |
| C3 | AGENTS.md 索引指向不存在的 6 文件 | 重建索引或补齐文件 |
| C4 | GAP_ANALYSIS 两处事实错误 | 修订，复核 ROADMAP P0 依据 |
| C5 | `.audit/last_commit_audit.json` 红灯：L0 硬编码 IP×5、L1 复杂度×5（cli/app.py 347 vs 阈值 30） | A3 完成后自然消解一部分，其余逐项清 |

### D. 工程化债（=回路缺传感器，P0/P1 主攻）

| # | 项 | 处置 |
|---|---|---|
| D1 | 无 CI 测试工作流（仅 CodeQL） | GitHub Actions 加 pytest 离线跑（复用现有 conftest 三重防护） |
| D2 | deploy 仅 1 个 systemd 单元 | 梳理 crontab 散落服务，逐个单元化（P3 后做） |
| D3 | 测试:源码 0.74:1，主干部分覆盖薄弱 | 随 P2 每迁一个插片补契约测试 |
| D4 | webui-server audit.rs 未接线（代码自认） | P4 接线或声明弃用 |

### 技债与架构债为何必须同路线

结构债（A）不还，插片化无从谈起——焊死的插片拔不下来；重复债（B）不清，插片化会把双轨一起插片化，债进棺材；漂移债（C）不修，重构期间的回归验证失去基准（版本对不上、红灯判不了新账）。**因此本提案不单立"技债专项"，而是把 A-D 编入 P0-P4 的验收标准。**

---

## 五、重构后的主干定义（灵元尺子量过）

**主干 ≤5,000 行，只含四类永不变的东西**：

| 件 | 来源 | 不变式 |
|---|---|---|
| 出入：turn 循环 | `core/model_call.py` 循环内核剥离 | 主干不知道任何具体插片的存在 |
| 流转：事件账本 | `session_journal`（append-only JSONL）+ 灵忆 2T3A | model-visible means logged；状态只进灵忆 |
| 缝：插片协议 | `lacp/` 五 seam + ToolRegistry（handler_name 解耦已是正确方向） | 新增插片 = 新增 manifest，主干 0 改动 |
| 门：钟表域守卫 | PermissionContext + VerificationGate + bash 黑名单 + MV-1 校验 | 安全门不可插拔、不可配置绕过（PRINCIPLES §九） |

**插片清单**（全部 manifest 注册，禁止主干 import）：model providers、30+ 工具、五层记忆、governance、self_optimizer、intel/behavior、mcp server/client、bus responder、webui/api、cli 交互、stt/lsp/subagent 后端、llm_proxy。

**验收主指标**（行数是辅）：*新增一个插片（如新 provider、新工具、新记忆层）只新增 manifest + 插片文件，主干改动为 0*——用架构守卫测试机械判定，不靠自觉。

---

## 六、优化路线图（P0-P5，每步附验收证据，H17 适用）

### P0 冻结契约 + 架构守卫（先立尺子，再动刀）— 约 2 天

1. 定义五 seam 的稳定接口签名（`lacp/` 已有雏形，收口为 v1 契约 + 契约测试）
2. 新增架构守卫测试（进 CI）：
   - 禁止 `core/` import 插片模块（维护 import 白名单，只缩不放）
   - 禁止新增 `sys.path.insert`、禁止函数内 lazy import 净增长
   - 禁止工具层新增 dict-判错（必须 Result）
3. wiring manifest v1：30 个协作对象的装配关系显式化（先记录，不倒转）

**验收**：守卫测试红→绿；`pytest tests/` 全绿；C1/C2 完成（版本统一、key 出 config）。

### P1 清偿补丁尸体（纯删除/合并，风险最低）— 约 3 天

B1-B6 + C3/C4/C5 可清项 + D1（CI 上线）。

**验收**：每删一项列对照表（删除行数、引用更新点）；全量 pytest 绿；`.audit/` 红灯项逐条销账；根目录卫生项删除前列清单并获用户确认词（建议-执行分离）。

### P2 倒转装配（结构债主攻）— 约 1-2 周

1. QueryEngine 拆解：循环内核留主干，30 对象改由 wiring manifest 注入，插片自注册
2. CodingRuntime 拆 mixin：10 组工具各自 manifest 化，execute_tool 五段管线留主干
3. api.py 门面瘦身：路由与主干解耦

**验收**：架构守卫测试白名单只缩不放；每迁一个插片跑对应测试文件；`tests/e2e/` 76 用例绿；新增"hello provider"插片演练（验证 0 主干改动指标）。

### P3 状态归灵忆 — 约 2 周

REFACTOR 文档 §二层 15 个状态模块（现约 7k 行）迁 records/events；type_registry 按需扩 type（主干零表变更，正是灵元哲学的验证场）；五层记忆作为灵忆之上的**策略插片**保留（衰减/分层是策略，存储是主干）。

**验收**：迁移对照表（模块→record type→transition 路径）；双写期后切单写；灵忆侧查询抽测通过；行为指标回路（R2/R3）在新状态层上重新合闸。

### P4 交互层与外围收口 — 约 1 周

cli/app.py 按 P2 同法拆分；webui-server audit.rs 接线或弃用；deploy 单元化（D2）。

**验收**：cli 复杂度红灯销账（347→≤30 阈值内按函数计）；e2e 绿；`.audit/` 周审计新增条目为 0。

### P5 目标账与回路验证 — 持续

- 主包目标：**主干 ≤5,000 行，总包 ≤38,000 行**（先还债不加功能；行数降幅主要来自 B/P3 去重）
- 测试:源码 ≥1:1（主干部分 100% 契约覆盖）
- 自优化 daemon 以本重构为第一个实战对象：每阶段指标（行数/依赖数/红灯数）自动入册，验证"回路合闸"不是又一次空转
- 全程复用 `scripts/prechange_snapshot.py`（R6）+ lefthook 双钩子 + 灵督 LLM 复审

---

## 七、风险与回滚

| 风险 | 缓解 |
|---|---|
| 大拆引发回归 | P0 守卫先行 + 每步独立提交可回滚 + prechange 快照 |
| 灵忆迁移丢状态 | P3 双写期（旧层只读保留一个版本周期）+ 迁移对照表机械核对 |
| 重构期间功能冻结引发族内阻塞 | BusResponder/analyzer 路径在 P2 前不动；各阶段声明影响面 |
| 再次"哲学照过≠内化"（SESSION83 教训） | 验收主指标机械化（架构守卫测试），不留"文档达标、代码超标"的口子 |

## 八、待族内定夺

1. 灵忆是否接受灵克状态迁移带来的 type_registry 扩容（约 10-15 个新 type）？
2. webui-server（Rust）与 webui（TS）是否纳入同一插片协议（当前 LACP 仅覆盖 Python 侧）？
3. P1-B6 根目录清理清单需用户确认词后执行。
4. 五 seam 契约 v1 是否升格为族级标准（供 lingcode/lingshell 等姊妹项目复用）？

## 九、与既有路线的关系

- 本提案**不推翻** `LINGCLAUDE_REFACTOR.md`，是它的灵元 1.0 正式版：继承"三层分离"思想，更新全部基线数字，补入技债总账（A-D）与机械验收
- CHARTER v1.0 里程碑"可替代日常 Claude Code 使用"的路径修正为：**先把主干变薄，再谈追平**——主干不稳，自优化闭环优化的对象本身就是流沙
- 评审通过后，本提案进入 ROADMAP 作为 v0.6.0 主线
