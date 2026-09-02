# Gap Analysis — lingclaude vs AtomCode vs DSH (deepseek-harness)

**日期**: 2026-08-21
**作者**: 灵克 (lingclaude)
**目的**: 对比三家架构与功能，定位 lingclaude 可吸收的能力与需保持差异化的定位
**关联**: `docs/ROADMAP.md` v0.2（按本报告修正后的实施路线，P0-P2 编号与本报告 § 十一一对应）

---

## 一、三家定位速览

| 维度 | lingclaude | AtomCode | DSH (deepseek-harness) |
|---|---|---|---|
| **语言/架构** | Python, monorepo 单进程 | Rust 1.88+, Cargo workspace, 14 crates | TypeScript, Cordis 插件架构, ~50 packages |
| **定位** | 灵族工程执行者（自省/治理/协作） | 终端 AI coding agent（对标 Claude Code/Cursor） | DeepSeek 官方 harness，一切皆插件 |
| **核心理念** | 自知→自觉→自决→进化；元认知守卫 H1-H17；LACP 治理 | 100% AI 生成，人类仅决策；Multi-provider OpenAI 兼容 | 时空可组合编程范式；capability seam；一切皆可选 |
| **成熟度** | v0.3.0, 1508 tests | v5.0.3 | developer preview |
| **独特优势** | 灵族成员协作 + LingBus 消息总线 + 自优化闭环 | 性能（Rust）、codeintel 调用链、cli approval gate | 插件生态、subagent 多后端、session 投影/快照 |

---

## 二、核心引擎层对照

### 2.1 Query/Agent Loop

| 能力 | lingclaude | AtomCode | DSH |
|---|---|---|---|
| Agent loop 基础 | ✅ `QueryEngine` (1393 行) | ✅ | ✅ `core` |
| Stream 提交 | ✅ `stream_submit` | ✅ | ✅ |
| 会话中断恢复 | ✅ `resume_interrupted` | ✅ session rewind/snapshot | ✅ session-projection / rewind |
| **Todo list 追踪** | ❌ 无原生 todo 工具 | ✅ `todo` tool | ✅ `tool-todo` |
| **Goal 驱动** | ⚠️ self_optimizer/daemon 类似但非会话内 | ❌ | ✅ `goal-round-driver` + durable GoalPhase |
| **Plan mode** | ✅ `plan_mode.py` | ✅ `codingplan/impact_plan` | ✅ `plan-mode` |
| **轮次预算** | ❌ | ✅ `codingplan/round_budget` | ⚠️ 部分 |
| **动态 persona** | ⚠️ behavior/role_separation | ✅ `codingplan/persona` | ✅ `system-prompt` |

### 2.2 上下文管理

| 能力 | lingclaude | AtomCode | DSH |
|---|---|---|---|
| 上下文压缩 | ✅ `context_compression.py` | ❌ (Rust crate 未见) | ✅ `compaction` + `compaction-basic` |
| **工具结果裁剪** | ❌ | ❌ | ✅ `compaction-tool-result-pruner` |
| **上下文缓存** | ✅ `context_cache.py` | ❌ | ⚠️ session scope |
| 分层记忆 | ✅ `layered_memory.py` (Emotion/Working/Common) | ⚠️ `kernel/memory/store.rs` | ⚠️ session-transcript |
| **会话指令注入** | ⚠️ | ✅ `kernel/session/instructions.rs` | ✅ `system-prompt` |

### 2.3 工具系统

| 能力 | lingclaude (22 个) | AtomCode (29 个) | DSH (N 个) |
|---|---|---|---|
| bash | ✅ | ✅ (+ `_workspace_gate`) | ✅ shell |
| read/write/edit | ✅ | ✅ (+ `write_approval`, `parallel_edit`, `search_replace`) | ✅ |
| glob/grep | ✅ | ✅ | ✅ |
| git 基础 | ✅ status/diff/log/blame | ✅ `atomgit` + hooks + middleware + provider | ✅ atomgit crate |
| **审批门** | ⚠️ permissions.py 静态 | ✅ `approval` tool + `write_approval` | ✅ `approval.md` subsystem |
| **敏感路径拦截** | ❌ | ✅ `sensitive_path` tool | ✅ sandbox-policy |
| **请求用户输入** | ❌ (question 未注册) | ✅ `request_user_input` | ✅ `user-questions` |
| **任务/子代理** | ✅ `sub_agent` | ✅ `task` tool | ✅ `tool-subagent` (6 后端) |
| **输出工件** | ❌ | ✅ `output_artifact` | ✅ `attachment` |
| **web fetch/search** | ✅ | ✅ | ✅ |
| **ast_edit** | ✅ `ast_replace`, `list_functions` | ✅ `ast_grep` | ⚠️ |
| **repair** | ❌ | ✅ `repair` tool | ❌ |
| **cd** | ❌ | ✅ `cd` tool | ⚠️ |

---

## 三、Code Intelligence 层对照

| 能力 | lingclaude | AtomCode | DSH |
|---|---|---|---|
| LSP 客户端 | ❌ 无 | ✅ `kernel/codeintel/lsp/` | ✅ `dsh-lsp` + `lsp-stdio` + `tool-lsp` |
| 符号索引 | ✅ `indexer.py` + `ast_edit` | ✅ `codeintel/index`, `symbols`, `queries` | ⚠️ |
| **调用链追踪** | ❌ | ✅ `trace_callers/callees/chain` | ❌ |
| **find_references** | ❌ | ✅ | ⚠️ via LSP |
| **blast_radius** | ❌ | ✅ | ❌ |
| **diagnostics** | ❌ | ✅ `codeintel/diagnostics` | ✅ via LSP |
| **file_deps 图** | ❌ | ✅ `file_deps`, `graph` | ❌ |

**关键差距**: AtomCode 的 codeintel 是显式一等模块，lingclaude 的 `indexer.py` 偏静态 AST 列表，缺跨文件调用链。

---

## 四、Sandbox / 安全层对照

| 能力 | lingclaude | AtomCode | DSH |
|---|---|---|---|
| 命令白/黑名单 | ✅ `permissions.py` deny_tools/prefixes | ⚠️ bash gate | ✅ `permission-presets` |
| **文件系统沙箱** | ❌ | ❌ | ✅ `sandbox` (bwrap/Landlock/Seatbelt/Win ACL) |
| **Sandbox 模式** | ❌ | ❌ | ✅ read-only / workspace-write / danger-full-access |
| **Tool spill（大输出外置）** | ❌ | ❌ | ✅ `spill` + `spill-local` + `spill-policy` |
| **凭证管理** | ❌ | ⚠️ `cli/auth_token` | ✅ `credentials` package |
| 危险命令拦截 | ✅ `verification_gate.py` | ⚠️ | ✅ `guard` package |

---

## 五、Subagent / 协作层对照

| 能力 | lingclaude | AtomCode | DSH |
|---|---|---|---|
| 子代理基础 | ✅ `sub_agent.py` 单实现 | ✅ `task` tool | ✅ `subagent` 通用 seam |
| **多后端** | ❌ | ❌ | ✅ 6 个: spawn-in-process / fork / ACP / Codex / Claude Code / dsh-sdk |
| **可继续的子会话** | ❌ | ❌ | ✅ continuable children |
| **子→父报告通道** | ❌ | ❌ | ✅ `tool-subagent-report` |
| **子代理控制** | ❌ | ❌ | ✅ `tool-subagent-control` (send_message/interrupt/list) |
| **depth limit** | ❌ | ❌ | ✅ `SubagentCapabilities.depthLimit` |
| **persona 注入子代理** | ❌ | ❌ | ✅ `persona` flag |
| 跨成员协作 | ✅ LingBus (灵族独有) | ❌ | ❌ |

---

## 六、Session / 持久化层对照

| 能力 | lingclaude | AtomCode | DSH |
|---|---|---|---|
| Session 管理 | ✅ `SessionManager` | ✅ `kernel/session/manager` | ✅ `session` |
| **Session 投影** | ❌ | ⚠️ | ✅ `session-projection` |
| **Session rewind** | ❌ | ✅ `kernel/session/rewind.rs` | ⚠️ |
| **Session snapshot** | ❌ | ✅ `kernel/session/snapshot.rs` | ✅ |
| **Session status 提醒** | ❌ | ✅ `status_reminder.rs` | ⚠️ |
| **Session 用量 provider** | ❌ | ✅ `usage_provider.rs` | ✅ `token-meter` |
| **Session 遥测** | ❌ | ✅ `cli/telemetry_scope.rs` | ✅ `session-telemetry` |
| **持久化存储** | ⚠️ LingBus/SQLite | ⚠️ | ✅ `persistence` + `storage` |
| **Schedule（定时回到会话）** | ❌ | ❌ | ✅ `schedule` (after/at/every) |
| **Jobs（长任务后台）** | ❌ | ❌ | ✅ `jobs` |

---

## 七、Plugin / 扩展层对照

| 能力 | lingclaude | AtomCode | DSH |
|---|---|---|---|
| 插件架构 | ❌ | ⚠️ | ✅ Cordis 一切皆插件 |
| **Skills 系统** | ⚠️ 简单 | ✅ `kernel/skills/catalog_hook`, `registry`, `render`, `use_skill` | ✅ `skill` package |
| **Hooks 系统** | ✅ `hooks.py` | ✅ `atomgit/hooks` | ✅ `hooks` package |
| **MCP** | ✅ `mcp/` (client+server+http_proxy) | ⚠️ | ✅ `mcp` package |
| **Workflow** | ❌ | ❌ | ✅ `workflow` package |
| **Preset** | ❌ | ❌ | ✅ `preset` package |
| **Extensions** | ❌ | ❌ | ✅ `extensions` package |
| **Bundle / Boot** | ❌ | ❌ | ✅ `bundle` + `boot` |

---

## 八、Web / UI 层对照

| 能力 | lingclaude | AtomCode | DSH |
|---|---|---|---|
| CLI | ✅ `cli/app.py` | ✅ 21 个模块 (webui/live_api/native_live) | ✅ |
| **Web UI** | ❌ | ✅ `cli/webui.rs` | ✅ `dsh web` (端口 3080) |
| **Live API** | ❌ | ✅ `live_api/hub` | ✅ `api` package |
| **登录态** | ❌ | ✅ `cli/login_state.rs` | ⚠️ |

---

## 九、自省 / 治理层对照（lingclaude 独特优势）

| 能力 | lingclaude | AtomCode | DSH |
|---|---|---|---|
| 元认知守卫 H1-H17 | ✅ `.lingclaude/metacognitive_guards.md` | ❌ | ❌ |
| 认知节奏监测 | ✅ `cognitive_rhythm.py` | ❌ | ❌ |
| 盲点检测 | ✅ `meta_cognition.py` (BlindSpotDetector) | ❌ | ❌ |
| 置信度校准 | ✅ `ConfidenceCalibrator` | ❌ | ❌ |
| 分层记忆（情绪/工作/常识） | ✅ `layered_memory.py` | ❌ | ❌ |
| 自我优化闭环 | ✅ `self_optimizer/` (trigger/evaluator/optimizer/advisor/daemon/learner) | ❌ | ⚠️ feedback package |
| 提案治理 | ✅ `governance_v2.py`, `proposal_lifecycle.py` | ❌ | ❌ |
| 行为感知路由 | ✅ `behavior_aware_router.py` | ❌ | ❌ |
| 数据飞轮 | ✅ `data_flywheel.py` | ❌ | ❌ |
| 验证门 | ✅ `verification_gate.py` | ⚠️ `review/verify` | ⚠️ approval |
| **情报收集** | ✅ `intel.py` (DailyDigest, IntelRelay) | ❌ | ❌ |

**结论**: 灵克在"自我认知 + 族内协作治理"上是三家独有,这是不可弃的核心资产。

---

## 十、关键 Gap 优先级矩阵

### P0 — 高价值低成本（建议 1-2 周内吸收）

| 编号 | Gap | 来源 | 落地建议 |
|---|---|---|---|
| **P0-1** | **Todo list tool** | 两家都有 | 新增 `engine/todo.py` + ToolDefinition 注册 (50-100 行) |
| **P0-2** | **Tool result pruning** | DSH compaction-tool-result-pruner | 在 `tool_pipeline.py` 加 token 阈值裁剪 |
| **P0-3** | **request_user_input tool** | AtomCode | 包装 question tool，schema 简单 |
| **P0-4** | **Session snapshot/rewind** | AtomCode | 在 `session.py` 加 `snapshot()` / `rewind(seq)` |
| **P0-5** | **LSP 集成 RFC** | 两家都有 | 起草 `docs/lacp/LSP_DESIGN_RFC.md`，先写设计稿不实现 |

### P1 — 高价值中成本（1-2 月)

| 编号 | Gap | 来源 | 落地建议 |
|---|---|---|---|
| **P1-1** | **LSP 集成实现** | 两家都有 | 实现 `engine/lsp.py`，走 stdio JSON-RPC，先支持 rust-analyzer/pylsp。依赖 P0-5 RFC |
| **P1-2** | **Subagent 多后端** | DSH | 现有 `sub_agent.py` 抽象出 Provider 接口,至少支持 inprocess + ACP 两种 |
| **P1-3** | **Spill storage** | DSH | 大输出写文件 + 返回 locator,与 `context_cache.py` 集成。依赖 P0-2 阈值判断 |
| **P1-4** | **Sandbox policy** | DSH | 短期用 `bwrap` 包 bash 执行;长期借鉴 read-only/workspace-write 模式 |
| **P1-5** | **Schedule / Jobs** | DSH | `schedule` 用 cron + LingBus 唤醒;`jobs` 长任务后台 + status/cancel 查询 |
| **P1-6** | **Code intelligence 图谱** | AtomCode | 基于 `indexer.py` 扩展 call graph（`ast` + `jedi`），实现 `trace_callers/callees` / `find_references` / `blast_radius`。与 P1-1 LSP 并行互补 |

### P2 — 战略级（3+ 月，需 RFC)

| 编号 | Gap | 来源 | 落地建议 |
|---|---|---|---|
| **P2-1** | **插件架构（Cordis 风格 capability seam）** | DSH | 现状 monorepo 已成型，改造大；先在 engine/tools 层引入 capability seam。需 RFC |
| **P2-2** | **Session projection** | DSH | 需先决定 session store 是否 event-sourced。依赖 P0-4 snapshot/rewind |
| **P2-3** | **Web UI**（可选） | 两家都有 | 不是当前灵克核心价值（灵族走 LingBus），低优先；如实现走 DSH `dsh web` 模式 |

### P3 — 不吸收（保持差异化）

- **Multi-provider LLM**：lingclaude 已有 `model/` 多 provider + router，够用
- **Approval gate**：AtomCode 偏 UX 层，lingclaude 治理已走 governance_v2，不重复
- **Workflow engine**：DSH 定位通用 harness，灵克走 LACP 治理，不重合
- **Cordis 风格插件架构（全量）**：全面插件化改造成本极大，P2-1 只做 capability seam 局部引入

---

## 十一、对当前进行中的工作的影响

### 对 query_engine 瘦身（1393 → <800 行）
- AtomCode 的 `codingplan` 模式值得借鉴：把 `assemble` / `confine` / `impact_plan` / `review_tool` 拆到 `lingclaude/codingplan/` 子模块
- DSH 的 `compaction-tool-result-pruner` 应放到 `tool_pipeline.py` 而非 query_engine
- `_hallucination_correction` / `submit` / `stream_call_model` 拆分方向正确

### 对 LACP v0.5.1 subagent_scope
- DSH 的 `SubagentCapabilities` (outputSchema/depthLimit/toolFilter/persona) 提供了清晰的 scope 词汇
- 灵克未来启用 optimizer 时，可直接对齐 DSH 这 4 个 capability flag

### 对 LINGKERNEL_v1 后续
- DSH 的 `invariants.md` + `core.md` 与灵克 dsh 6 包 spine 设计有相似性,可交叉对比
- 灵安的 `verification_gate` 与 AtomCode 的 `review/verify`、DSH 的 `guard` 应建立映射,避免重复造轮

---

## 十二、立即可执行的 3 个动作

对应 ROADMAP P0 编号（详见 `docs/ROADMAP.md`）：

1. **P0-1 新增 `engine/todo.py`** —— 对标两家 todo tool,30 行内可完成,立即提升长任务可追踪性
2. **P0-2 在 `tool_pipeline.py` 加 output 大小阈值裁剪** —— 对标 DSH pruner,防止大输出爆上下文
3. **P0-5 起草 `lingclaude/lsp.py` RFC** —— 对标两家 LSP 集成,先写设计稿,不立即实现

---

## 附录 A — 参考文档

- DSH subsystems: `/home/ai/deepseek-harness/docs/subsystems/` (49 个 .md)
- AtomCode crates: `/home/ai/atomcode/crates/` (14 个)
- 灵克代码: `/home/ai/lingclaude/lingclaude/`

## 附录 B — 工具数量统计

- lingclaude: 22 个 ToolDefinition (engine/coding.py)
- AtomCode: 29 个 (kernel/tools)
- DSH: 动态 (插件注册),核心约 25 个
