# Gap Analysis — DSH 维度（lingclaude vs DeepSeek-Harness，参照 Claude Code / AtomCode / Crush）

**日期**: 2026-08-25
**作者**: 灵克 (lingclaude)
**性质**: 存档分析（信息调研，非代码改动）
**目的**: 补齐 `GAP_ANALYSIS_20260825_CC_DIMENSION.md` / `_CRUSH_DIMENSION.md` 缺失的 DSH 第一手维度；DSH 是 DeepSeek 官方 harness（TS Cordis / 219 包），与 lingclaude "单体+治理"路线最不同；CC/CRUSH 是同代终端 agent，DSH 是平台级范式
**关联**: `docs/gap_analysis/GAP_ANALYSIS_20260821.md`（三方基础对比）/ `_CC_DIMENSION.md` / `_CRUSH_DIMENSION.md` / `ROADMAP.md` v0.3

---

## 一、DSH 是什么、为什么单列

**DSH** = DeepSeek 官方 harness（`deepseek-harness` / developer preview / 0.1.0-rc.5），TypeScript 实现，Cordis 插件架构，**219 个 package**。DSH 不是另一个"终端 agent"，而是**"一切皆插件"的 AI 编程范式平台**——每条能力（compaction / subagent / session / mcp / lsp / spill / schedule）都是独立 package，可自由组合。

**与 CC/CRUSH 的关键差别**：CC / Crush = 单一团队打磨的产品（编译固定 + 行为固定）；DSH = 包集合（用 `Boot.context({...})` 组合哪些包决定 agent 行为）。这意味着差距口径完全不同：
- CC/CRUSH 的差距 = **功能深度与广度**（"有没有 plan mode / 有没有后台任务"）
- DSH 的差距 = **架构可组合性**（"能不能在不动主引擎的情况下换 compaction 策略"）

灵克走"单体+治理"路线（不走 Cordis 风格），但 DSH 内部的**子系统设计**（compaction / subagent / session / spill / schedule）几乎每条都是参照价值最高的蓝本——因为 DSH 是"AI 自己设计 AI agent"的产物，最贴近"应该长什么样"的直觉。

---

## 二、四方定位速览

| | Claude Code | AtomCode v5.0.3 | DSH (deepseek-harness) | Crush v0.90.0 | lingclaude v0.3.0 |
|---|---|---|---|---|---|
| 形态 | 商业闭源 CLI/TUI | 开源终端 agent（Rust） | 平台级 harness（Cordis） | 终端 AI（Go） | 灵族编程助手 + 审计担当 |
| 规模 | ~512K 行 | ~273K 行 / 15 crates | **219 个 package** | Go 二进制 | ~36K 行 |
| 架构 | 单体打磨 | 单体分 crate（14 crates） | **一切皆插件** | 单体 | 单体 |
| 成熟度 | 商业产品 | 5318 commits / 4411 tests | developer preview / per-file 100% 覆盖率门禁 | v0.90.0 / Charm 生态 | 16 commits / ~2199 tests |
| 核心理念 | 模型能力最大化 + 工程护栏 | 100% AI 生成 | 时空可组合编程范式 | 工具/代码/工作流接入 LLM | 自知→自觉→自决→进化 |

**两对对比**：CC↔lingclaude（终端 agent 路线）、DSH↔lingclaude（范式路线）。Crush 与 AtomCode 都是 DSH 的"非全插件化"对手，但 DSH 是"全插件化"极端。

---

## 三、lingclaude vs DSH 的差距（按子系统）

### 3.1 子代理（DSH 强项，lingclaude 落后最多）

DSH 的 `tool-subagent` 是 6 后端 seam：
- `spawn-in-process`（同进程）
- `fork`（子进程隔离）
- `ACP`（Agent Client Protocol，跨语言）
- `Codex`（OpenAI 编码 agent）
- **Claude Code**（直接把 CC 作为后端）
- `dsh-sdk`（自研）

附 `SubagentCapabilities` 4 flag：`outputSchema / depthLimit / toolFilter / persona`——子代理是**带类型、可控、可继续**的一等公民。

lingclaude 现状：
- T1-6 完成度 ~50%（本会话修了 `send_message` 假实现）：`engine/subagent/{base,inprocess,acp,manager}.py`，5 状态机，`list_agents` / `interrupt_agent` 已注册；并行骨架（ThreadPoolExecutor）
- T1-3 后台任务 0%（`run_in_background` 全仓 0 命中）
- ACP 后端硬编码 `127.0.0.1:8901`（`engine/subagent/acp.py`），无动态发现

**DSH 的 `Claude Code as a backend`** 是个**反直觉亮点**——证明"范式可组合"可以让一个 agent 把对手当作子代理（即使对手是商业闭源）。lingclaude 把 Crush 作为外部 agent 接入（`.crush/crush.json` 指向 `lingclan_proxy`）是相同思想的弱版本。

### 3.2 会话投影与快照（架构性差距）

DSH：
- `session-projection` package —— 把事件流投影为不同视角（token 用量 / 工具调用频率 / 轮次统计 / 决策路径），**新维度零成本加新投影器**（Cordis 服务注入）
- `session-snapshot` / `session-rewind` —— 序列号粒度的回滚 + 重放
- `session-telemetry` —— 会话遥测（事件流）
- `token-meter` —— 用量提供器
- `status_reminder` —— 状态提醒（feed back to LLM）

lingclaude：
- T3-2 本周接好 `session_projection`（62587fa）—— `/sessions/{id}/projection` 端点，但**仍是单视角快照**，不是事件流投影器
- `session.py` snapshot/rewind 已落地（`session.py:142-180`），但 rewind 粒度是 checkpoint，**不是事件级**
- 无 status_reminder、无 token-meter、无 session-telemetry

**DSH 的 session-as-event-stream** 是比"快照+投影"更深的范式选择——一旦 event-sourced，projection / replay / debug 都成本最低。lingclaude 当前是**快照式持久化 + 单投影器**，架构层差距大于功能层。

### 3.3 调度系统（DSH 独有）

DSH `schedule` + `jobs` 双层：
- `schedule` —— cron 表达式 + `after` / `at` / `every` 三触发器；可挂回 agent 主循环（"明天 9 点回到会话 X"）
- `jobs` —— 长任务后台 + status 查询 + cancel 控制

lingclaude：
- T2-2 `task_scheduler.py`（281 行）**机制就绪 + cron 解析就绪**，但**早期为零消费方**（ROADMAP 死接线第 4 案）
- 2189827 接好 `core/scheduler.py` → CLI `/schedule` 端点 + `@daily` / `@hourly` / `@weekly` / `interval:N` 四种 cron 表达式
- LingBus 唤醒通道：scheduler.py 的 `_send_wakeup` 写通知，但** LingBus 接收侧未做投递验证**

**与 DSH 差距**：
- 无 `jobs`（长任务后台）概念
- 无 `after` / `at` 精确触发器（目前只支持 cron 语义 + 间隔）
- 无"挂回会话 X"的回路——schedule 触发后** agent 如何从外部恢复状态仍是开放的**

### 3.4 上下文压缩四层（DSH 设计最完整）

DSH `compaction` 不是单一函数，是**四层递减策略**：
1. `compaction-tool-result-pruner` —— 工具结果 token 阈值裁剪
2. `compaction-surface` —— 影子替换（不被主消息流吸收）
3. `compaction` —— 主压缩引擎
4. `compaction-basic` —— 基础版 fallback

CC = LLM 摘要式 auto-compact；AtomCode = 双层（Tier1 stub 化保 prefix cache + Tier2 高水位 drain）；DSH = 四层策略可插拔。

lingclaude：
- T1-1 完成后 = `use_llm_summary` 通道 + 动态预算（窗口×4%） + turn 内触发 + prefix cache 保留（stub 化，本会话修了保尾→保头方向 bug）
- **但没有 pruner / surface / shadow 概念**——压缩是"先压缩再发"，无"原地 stub 化"、无"裁剪后再压缩"

**最具体的差距**：`compaction-tool-result-pruner` 单独 package，对应 lingclaude 缺失的"工具结果 > N token 自动 stub 化"——这个动作在 AtomCode Tier1 实现得很细，可以独立抽出 50-100 行的可吸收工作。

### 3.5 沙箱三态（DSH 平台级）

DSH `sandbox-policy` + `sandbox`：
- 三态：`read-only` / `workspace-write` / `danger-full-access`
- 多后端：`bwrap`（Linux）/ `Landlock`（Linux）/ `Seatbelt`（macOS）/ `Win ACL`（Windows）
- 4 档 + `SandboxUnavailableError`（lingclaude T2-1 已对齐这个概念）
- `fail-closed` 策略（探测失败拒绝执行，不静默降级）

lingclaude T2-1 完成度（lacp/sandbox_policy.py + engine/bash.py）：
- 4 档（permissive / restricted / strict / paranoid）+ `LINGCLAUDE_SANDBOX_MODE` 环境变量
- `SandboxUnavailableError` 已抛，bash.py:227 接线
- **但：当前只在 bwrap 单后端**，无 Landlock / Seatbelt / Win ACL 跨平台；`paranoia` 严格度定义与 DSH `danger-full-access` 不完全对位

**差距具体点**：沙箱后端跨平台（这是 DSH 平台化优势的核心体现），lingclaude 需要至少加 Landlock 才能脱离"仅 Linux + 仅 bwrap"。

### 3.6 工作流引擎（DSH `workflow` + `ralph`）

DSH `workflow` package：多代理编排（"agent A 完成 → 通知 agent B → B 写代码 → 通知 C 评审"），可持久化为 YAML 工作流定义。`ralph` 是 DSH 自带的"完整开发循环"工作流。

lingclaude：完全无对应概念；治理走 `governance_v2.py` + `proposal_lifecycle.py`（是单 agent 内的提案-投票机制，**不是多代理编排**）。

**判断：保持差异**。DSH 的 workflow 是平台通用能力；lingclaude 治理已走 LACP 路线（提案治理 + LingBus 跨成员），不重复。

### 3.7 Plugin / 扩展层（DSH 极致范式）

DSH = Cordis `plugin` + `extensions` + `bundle` + `boot`：
- 任何能力都是 `Boot.context({...}).plug(...)` 组合
- 加载/卸载/重载是 runtime 操作
- 写新 plugin = 新 package + `apply(ctx)` 函数

lingclaude：当前 monorepo 单进程，**无运行时插件机制**。能力层（fs/shell/llm/subagent）是直接 import + 继承 Mixin。要实现"运行时装/卸载模块"需要 P2-1 capability seam RFC。

**判断：局部引入**（ROADMAP P2-1）。完全 Cordis 化改造巨大；先在 `engine/tools` 层引入 capability seam 让"换沙箱后端 / 换 compaction 策略"不需改 query_engine。

### 3.8 MCP（DSH 平台级）

DSH `mcp` package：
- stdio / HTTP / SSE / OAuth 全 transport
- 项目信任级别 / autoApprove / 动态注册
- `tools/list` schema 发现

lingclaude：
- T1-5 完成 stdio + HTTP + `discover_and_register` + LACP manifest transport 字段
- **缺**：SSE transport、OAuth/PKCE（CC / Crush 都有）、项目信任级别

### 3.9 子代理能力定义（DSH 的 `Capabilities` 是设计典范）

DSH `SubagentCapabilities` 的 4 flag 是**子代理类型系统**的最小集：
- `outputSchema` —— 输出有 schema（产出可解析）
- `depthLimit` —— 嵌套深度上限（防递归风暴）
- `toolFilter` —— 可见工具白名单（防越权）
- `persona` —— 注入人设（与主 agent 区分）

lingclaude `SubagentRequest`（`engine/subagent/base.py`）：有 `parallel / control_channel` 两字段，**无 depthLimit / toolFilter / persona / outputSchema**。子代理深度上限、工具白名单、人设注入**完全没有**——这是治理与安全缺口。

---

## 四、lingclaude 相对 DSH 的独有资产（DSH 没有）

DSH 是平台，不带 agent 个性——它的"agent 风格"全靠插件组合。lingclaude 是单体+治理层的 agent，**所有 DSH 没有的"治理/自省/族内协作"维度**:

| 能力 | 位置 |
|---|---|
| 元认知守卫 H1-H14 | `.lingclaude/metacognitive_guards.md` |
| 认知节奏监测 / 痴呆检测 | `cognitive_rhythm.py` / `dementia_detector.py` |
| 盲点检测 + 置信度校准 | `meta_cognition.py` |
| 分层记忆（艾宾浩斯衰减 + Experience Store） | `layered_memory.py` |
| 自我优化闭环（7 类触发 + AST 评估 + daemon） | `self_optimizer/` |
| 提案治理（governance_v2 + proposal_lifecycle） | LACP 治理 |
| **LingBus 族内协作**（5 个成员跨实例总线） | `coordination/bus_responder.py` |
| MV-1 审计（发模型前 fail-closed 落 log + 可重建） | `query_engine.py:878-962` |
| 8/13 事故横评根因零错误实证 | `docs/lacp/AGENT_COMPARISON_20260813.md` |

**关键差异**：DSH 把 agent 当作**可组合的零件**；lingclaude 把 agent 当作**有自我认知的治理主体**。这是路线差异，不是质量差异——但治理路线的差异化资产是 lingclaude 不可弃的护城河。

---

## 五、DSH 维度对当前工作的影响

### 5.1 给 T3 战略项的具体参考

| T3 项 | DSH 参考 |
|---|---|
| T3-1 capability seam 局部引入 | `dsh-boot` / `dsh-plugin` —— `apply(ctx)` 模式 + Service Definition 接口 |
| T3-2 session projection | `dsh-session-projection` 投影器抽象 + `dsh-session-telemetry` 事件流接口 |
| T3-3 query_engine 瘦身 | 实际已完成（1664→686） |

### 5.2 T1 / T2 残余可吸收 DSH 子系统

| 当前 T1/T2 项 | DSH 对应 package | 可立即吸收的具体动作 |
|---|---|---|
| T1-3 后台任务 | `dsh-jobs` + `dsh-tool-subagent-control` | 实现 `run_in_background` + `tool-jobs` 查询/取消 |
| T1-6 子代理深度/人设 | `SubagentCapabilities`（4 flag） | 给 `SubagentRequest` 加 `depthLimit / toolFilter / persona` 三字段 |
| T1-5 MCP OAuth | `dsh-mcp` | 加 OAuth/PKCE + project trust level |
| T2-2 schedule 三触发器 | `dsh-schedule` | 加 `after:` / `at:` 触发器 + 挂回 agent 会话 |
| T2-1 沙箱跨平台 | `dsh-sandbox`（4 后端） | 加 Landlock 后端（仅 Linux 优先级高于 bwrap）|

### 5.3 范式层启示

DSH 教给 lingclaude 的不是"换架构"——是"分层接口设计":
- **压缩**: 从单一函数 → 策略接口（`compaction-tool-result-pruner` / `compaction-surface` / `compaction` 各包独立）
- **会话**: 从快照存储 → 事件流 + 投影器
- **子代理**: 从单一函数 → 类型化能力声明（Capabilities）
- **沙箱**: 从单一后端 → 后端 seam + 策略档

每一项都是 **"把一个机制类做成 seam 接口"**，对应 ROADMAP P2-1 capability seam 的精神——但可在 T1 范围内**局部引入**(不需要等 T3)。

---

## 六、结论与建议

**DSH 是"AI 编程 agent 应该长什么样"的范式参照**——但 lingclaude 不必走 Cordis 路线（治理差异化更值钱）。DSH 真正有价值的不是"插件化"，而是**每个子系统都做到了"机制类 + 接口声明 + 多后端实现"的三件套**——这是 lingclaude 当前的明显短板。

**可立即吸收（D 级工作，1 周内）**：
1. `SubagentCapabilities` 4 flag 落到 `SubagentRequest`
2. `compaction-tool-result-pruner` 单独抽出（工具结果 > N token 自动 stub 化）
3. `run_in_background` 后台任务（DSH `jobs` 简化版）

**中期吸收（与 T3-1 并行）**：
4. T1-5 MCP OAuth/PKCE
5. T2-2 schedule `after:` / `at:` 触发器 + 挂回会话
6. T2-1 沙箱后端 seam（Landlock 后端）

**保持差异**：
- Cordis 风格全插件化
- `workflow` 多代理编排（lingclaude 走 LACP 治理替代）
- `ralph` 等预制工作流（lingclaude 走提案治理）

---

## 附录 — 证据来源

- DSH 源码：`/home/ai/deepseek-harness/packages/`（219 packages）、`docs/subsystems/`（49 个 .md）
- lingclaude 引擎：`/home/ai/lingclaude/lingclaude/engine/`
- ROADMAP：`docs/ROADMAP.md` v0.3
- T0 死接线前科：`docs/ROADMAP.md §死接线前科记录`（5 案，已修 2 案）
- 8/21 三方对比：`docs/gap_analysis/GAP_ANALYSIS_20260821.md`
- CC / Crush 维度：`docs/gap_analysis/GAP_ANALYSIS_20260825_{CC,CRUSH}_DIMENSION.md`
- 实证：`docs/lacp/AGENT_COMPARISON_20260813.md`（同提示词横评，lingclaude 零错误）