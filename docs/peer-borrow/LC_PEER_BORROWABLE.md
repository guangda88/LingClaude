# lingclaude 与 8 个 agent harness 同行的架构对比与可借鉴项

> **写作纪律**:本报告区分**事实**与**推断**。
> - 事实:基于源码真读(lingclaude 本仓 + `/home/ai/deepseek-harness`)与开源仓库文档/博客原文,均附 source。
> - 推断:基于事实外推,标记"建议/可能"。
> - 部分探索项目为公开博客/二手评测,标注 confidence。
> **生成日期**:2026-09-20
> **基准**:lingclaude HEAD = `M .lingclaude/...`(详见 git status 顶部提交)

---

## 〇、lc 当前架构(真读摘要,作为对照基线)

### 0.1 工程哲学

灵元 1.0 铁律(`docs/LINGYUAN_IRON_LAW.md`):
- **薄主干**:主干=状态机(records/events/transition),任何变化走接缝(register type),主干零概念
- **分形插片**:连 provider/tool/sandbox/治理/自优化都是可变插片
- **2T3A**:records(值) + events(流) + transition(动作) 三原语 + test/audit 两轴
- **可声明**:LACP manifest v0.5.0,plugin 必须声明接口契约(transport/replaceable/output_recipient/dependencies)

### 0.2 当前结构(实测,非声明)

```
lingclaude/lingclaude/
├── api.py                     # FastAPI HTTP 门面(I3 抽出 external_query seam 后)
├── cli/                       # REPL 装配 + turn 调度 + 流式渲染 + Esc 监听
│   ├── repl.py                # 交互主循环(从原 app.py 713 行 _interactive_loop 拆出)
│   ├── commands.py            # 斜杠命令处理器
│   ├── repl_turn.py           # 回合执行与可观测性
│   ├── repl_io.py             # 流式渲染 + Esc 监听
│   ├── full_tui.py            # 常驻全屏 TUI
│   ├── render_facade.py       # 渲染门面
│   └── input_queue.py         # 异步输入泵 + 队列
├── core/                      # 主干,QueryEngine + 协作者
│   ├── query_engine.py        # 多 mixin 装配(turn/model/lifecycle/submission/mcp)
│   ├── query_engine_{turn,model,lifecycle}_mixin.py
│   ├── model_call.py          # 模型调用 + 循环检测 + 配置热重载
│   ├── session.py             # SessionManager + StateStore 双写
│   ├── hooks.py               # 6 个 HookType(PRE/POST_TASK, ON_ERROR/STOP, PRE/POST_COMPACT)
│   ├── layered_memory.py      # 5 层记忆 + Ebbinghaus 5 维衰减
│   ├── task_manager.py        # 任务栈(active/pending/completed)
│   ├── seam.py                # 进程内 SeamRegistry + 10 类 SeamType + Plug Level L1/L2/L3
│   ├── wiring.py              # WIRING_MANIFEST 装配(数据,非代码)
│   ├── policy_loader.py       # YAML 策略热更
│   ├── skill_index.py         # SKILL.md 匹配
│   ├── data_flywheel.py       # 行为飞轮
│   └── (略:governance_integration, degradation_detector, dementia_detector, hallucination_guard, ...)
├── coordination/              # 跨仓协同
│   ├── bus_responder.py       # LingBus 任务响应器(灵信 → 灵克)
│   ├── bus_consumer.py
│   ├── message_signer.py
│   └── alert.py
├── governance/                # 异议制治理引擎(灵研"机制胜于内省"论文)
│   ├── governance_v2.py       # Blast Analysis + Objection(severity: blocking/concern/note)
│   ├── governance_router.py   # 外观模式
│   ├── proposal_lifecycle.py
│   ├── cognitive_state.py
│   └── roster.py
├── lacp/                      # 灵族协议
│   ├── manifest.py            # v0.5.0 plugin manifest(transports×6, replaceable×4, deps×3)
│   ├── trace.py               # v0.4.0 trace(phase×4, outcome×6 含 intuitive/unverified, human_context)
│   ├── capability_seam.py     # 进程内能力缝 + 双签(SignedProvider)
│   ├── cross_repo_seam.py     # 跨仓路径单源(env 覆盖)
│   ├── l7_engine_seam.py
│   ├── sandbox_policy.py
│   ├── trace.py
│   └── marketplace.py
├── plugins/agents/            # 12 子灵族成员 + 外部 agent
│   ├── agent_family.py        # 参数化公共基类(McpAgentPluginBase,manifest 全驱动)
│   ├── agent_lingxi.py        # 范式
│   ├── (12 子成员:lingflow/lingcreate/lingmessage/lingminopt/lingresearch/lingtongask/lingweb/lingyang/lingzhi + agent_zhibridge + os_resource + bus_bridge)
│   ├── work_claim.py          # 铁律 8 跨仓 work claim
│   └── (proj_*: lingchu/lingdai/lingkang/linglv/lingshang/lingsheng/lingshi/lingyi)
├── self_optimizer/            # 自优化(灵元 P0-P5)
│   ├── daemon.py              # 24h 节流 + 会话结束触发 + benchmark 实证门禁
│   ├── trigger.py             # 8 类触发
│   ├── evaluator.py           # StructureEvaluator(AST violations)
│   ├── optimizer.py           # SynchronousOptimizer → lingminopt MinimalOptimizer
│   ├── advisor.py
│   ├── benchmark.py           # P0 实证门禁(best_params 须过"基准分不回退")
│   └── learner/patterns.py    # PatternRecognizer
├── seams/                     # 业务缝
│   ├── external_query.py      # LLM 直连兜底链 / GitHub / PyPI / 版本
│   └── research_crew.py
├── engine/                    # CodingRuntime(多 mixin)
│   ├── coding.py              # 主 runtime,10 mixin 装配
│   ├── coding_wiring.py       # CODING_WIRING_MANIFEST(对位 query_engine)
│   ├── tool_pipeline.py       # 5 段管线 pre→guards→execute→post→finalize
│   ├── tool_registration.py
│   ├── tool_router.py
│   ├── file_ops.py / file_read.py / file_edit.py / ast_edit.py
│   ├── bash.py / bash_lingxi.py / bash_network.py
│   ├── sandbox_provider.py
│   ├── sensitive_path_gate.py
│   ├── verification_gate.py   # H17 闭环申报
│   ├── verify_cadence.py
│   ├── lsp_{provider,registry,session}.py
│   ├── mcp_{client,proxy}.py
│   ├── plan_mode.py
│   ├── todo.py
│   ├── background.py
│   ├── subagent/
│   └── tool_handlers/         # BashToolsMixin / FileToolsMixin / ...
├── webui_seam.py              # 跨进程 webui service definition + consumer 声明
└── mcp/                       # 内置 MCP server
```

### 0.3 量化指标(已有数据)

- **QueryEngine 协作者**:WIRING_MANIFEST 收敛 55 项 → 6 行主干预留(Q4 拆 mixin 后)+ YAML 热更(P1-1)
- **Pluggable 类型**:10 类 SeamType(provider/tool/sandbox/transport/memory/governance/self_opt/agent/multimodal/orchestrator/resource),每类声明 Plug Level L1/L2/L3
- **Hook 类型**:6 个(PRE/POST_TASK, ON_ERROR/STOP, PRE/POST_COMPACT),priority 排序
- **LACP trace**:v0.4.0 含 human_context + outcome×6 + phase×4 + cost + caller_chain + actor_role
- **Plugin manifest**:v0.5.0 含 transports×6 + replaceable×4 + dependencies×3 + output_recipient + HMAC-SHA256 signature
- **治理**:异议制(severity×3),blast_analysis + cognitive_assessment 联动
- **记忆**:5 层 × Ebbinghaus 5 维衰减
- **peer 自评得分**(docs/EVALUATION_LINGYUAN_1.0_vs_PEERS.md):代码能力⭐⭐⭐⭐⭐(重构最强)、记忆⭐⭐⭐⭐⭐、安全⭐⭐(沙盒弱)

---

## 一、八个外部项目精读(架构特征 + 核心借鉴点)

> 标注:**[C1]=code 阅读置信度(从高到低:源码 > 一手 blog > 二手评测)**
> **来源**全部列出,**borrowable_for_lc** 列出"具体可借鉴的工程做法 + 落到 lc 的对应文件"

---

### 1.1 Codex Host/CLI(openai/codex)[C1: **主仓源码真读**(Bash 克隆到 /tmp/codex-probe, 1.4GB+)]

> **⚠️ 重要纠错(agent 真读后)**:`openai/codex-harness` 这个仓库 **不存在** — 所谓"Codex Harness"在 OpenAI 语境里 = 单仓内的 **框架层**(`codex-rs/core` + `codex-rs/protocol`),外部调用走 **JSON-RPC surface**(`codex-rs/app-server`)。详见 §1.6 codex-harness 部分。

**Repo**:github.com/openai/codex (Apache-2.0, 71k stars, Rust ~94.7%, 60+ crates)
**Repo size**:~665 releases,~5000 commits
**真读文件**(已克隆到 /tmp/codex-probe):
- `codex-rs/protocol/src/protocol.rs`(5000+ 行,Op + Submission + Event + EventMsg + 各种 items)
- `codex-rs/core/src/lib.rs`(`#![deny(clippy::print_stdout, clippy::print_stderr)]` 编译期锁)
- `codex-rs/core/src/codex_thread.rs`(Thread = Session + 持久化元数据)
- `codex-rs/core/src/session/session.rs`(`pub(crate) struct Session`)
- `codex-rs/core/src/client.rs`(ModelClient scoped to session lifetime)
- `codex-rs/core-plugins/src/lib.rs`(marketplace constants)
- `codex-rs/app-server-protocol/src/lib.rs`
- `codex-rs/app-server/src/lib.rs`
- `sdk/python/README.md`(自动生成 Python SDK)

**架构(真读细化)**:
```
Surface:        codex-rs/cli(TUI,ratatui) + codex-rs/exec(non-interactive) + codex-rs/code-mode + codex-rs/app-server(JSON-RPC on stdio/ws/control-socket)
               + codex-rs/exec-server + sdk/python(pip install openai-codex) + sdk/typescript
Session:        CodexThread = Session + ThreadConfigSnapshot + ThreadStartupMetadata + ThreadSettingsOverrides
               + ThreadMemoryMode(Enabled/Disabled) + disabled_plugin_ids: Option<Vec<String>>(per-thread plugin toggle)
Core:           codex-rs/core(纯 harness,无 UI,无 print)
               ├ codex_thread.rs(对外句柄)
               ├ session/(Session, InputQueue, McpRefresh, StepContext, StepSettings, TurnContext, turn, turn_input, world_state)
               └ agent/(multi-agent 子层,OnceLock<MultiAgentVersion> 懒激活)
Protocol:       codex-protocol(跨 crate contract)
               Submission { id, op, trace, parent_turn_id, root_turn_id }(带因果 ID)
               Op = #[non_exhaustive] enum,29+ 变体(命令式,严格 decoupled intent)
               Event { id, msg } 与 Submission.id 关联
               EventMsg = enum,1500+ 行(事件式,decoupled observation)
Plugins:        codex-plugins(loaded cache, marketplace, git policy, executor hooks, MCP overlay, bundled metrics)
               marketplace 来源:openai-curated / openai-api-curated / openai-bundled / openai-bundled-alpha / openai-primary-runtime
```

**核心特性(真读版)**:
1. **SQ (Submission Queue) / EQ (Event Queue) pattern** — 文件头注释原话:"Uses a **SQ/EQ pattern** to asynchronously communicate between user and agent"。"minimal" 不是"抽象少",而是 **接口有且只有 Op→Event 两个方向**。所有内部复杂度(sandbox, MCP, tools, attach)都在 `Op::TurnInput` 触发后的核心循环里消化,框架层不暴露第二接口
2. **Surface-agnostic + frame-as-policy**:`#![deny(clippy::print_stdout, clippy::print_stderr)]` 编译期拒绝 println,事件外送完全靠 `Event` channel — **code-as-policy 的最锋利表达,把解耦编译期锁死**
3. **Session / Thread / Turn 三层边界**:
   - **Session = 运行时机器**:`pub(crate) struct Session`,持有 `tx_event: Sender<Event>`(EQ 出向)+ `agent_status: watch::Sender<AgentStatus>`(状态广播)+ `state: Mutex<SessionState>`(runtime state)+ `thread_settings_persistence: Semaphore`(持久化 IO 与 state **解耦**,注释明示 "Keep this separate from `state` so storage I/O does not block runtime state access")
   - **Thread = Session 的对外封装 + 持久化元数据**:持有 `ThreadConfigSnapshot`(模型/permission/environments/personality 冻结快照)+ `ThreadStartupMetadata` + `ThreadHistoryMode`(Legacy/Paginated) + `disabled_plugin_ids: Option<Vec<String>>` per-thread plugin toggle
   - **Turn = 一次用户驱动的执行单元**:`Op::TurnInput { request, mode, reply }` 触发后创建 `TurnContext`,可中途 `Op::TurnSettings`(改模型)/ `SteerSubmission`(steer)/ `SuspendTurnAndShutdown`(suspend)/ `RecoverTurn`(recover)/ `Op::Interrupt`(abort)→ `EventMsg::TurnAborted`
   - **`parent_turn_id` + `root_turn_id`** 支撑 turn 树(多 agent 因果跟踪)
4. **ToolCall **不是** 统一抽象** — 显式拆成 7 个分支,各有独立 approval gate / event pair:
   | 工具种类 | 用户决策 Op | Agent 事件 |
   |---|---|---|
   | Shell exec | `ExecApproval { id, turn_id, decision }` | `ExecCommandBegin/End/OutputDelta` |
   | Patch 文件改 | `PatchApproval { id, decision }` | `PatchApplyBegin/Updated/End` |
   | MCP tool | — | `McpInvocation` + approval metadata |
   | Elicitation(MCP 问用户) | `ResolveElicitation { server_name, request_id, decision, content, meta }` | `ElicitationRequestEvent` |
   | User question | `UserInputAnswer { id, response }` | `RequestUserInputEvent` |
   | Permission request | `RequestPermissionsResponse { id, response }` | `RequestPermissionsEvent` |
   | Dynamic tool(运行时声明) | `DynamicToolResponse { id, response }` | `DynamicToolCallRequest` |
   - 实现侧的一致性用 **approval id ↔ submission id** 关联保证(`McpToolApprovalMetadataMap: HashMap<(String, String), Weak<...>>` 用 weak ref 打破 cycle,见 `core/src/session/session.rs:52-53`)
   - **不统一的原因**:可独立打 policy(patch vs exec vs MCP vs dynamic 各走不同 sandbox)、可独立做 durability(dynamic 可重新声明,exec 不行)、可独立打 telemetry(guardian_checkpoint.rs 是 patch 专用,stream 重试是 exec 专用)。codex 选择当 **状态机节点** 而非抽象类型
5. **Event 模型**:`Sender<Event>` (mpsc::Sender),buffer 限定 `transport::CHANNEL_CAPACITY`,消费者慢生产者按 channel 策略背压 — **backpressure 靠 rust mpsc + watch + Semaphore 手拼,不用 axum/q link** — 显式选择(bann 类型化 channel 才能强约束契约)
6. **顺序保证**:`Submission.id` ↔ `Event.id` 关联,Session 里 `Submission` 排队 FIFO 推入 `state.input_queue`(`session/input_queue.rs`),处理按队列顺序 turn → 调模型 → 收响应 → 发 events。Events 出 Session 按 FIFO 单 `Sender<Event>`,Rust mpsc 语义保证同一 consumer 视角下不交叉
7. **取消语义**:`CancellationToken`(`tokio_util::sync::CancellationToken`)在 Thread/SubAgent/Spawn child 各层都持有,`Op::Interrupt` 不抢断 background terminal(`Op::CleanBackgroundTerminals` 才真的收),"软取消 vs 硬收" 二分与 SQ 解耦
8. **Op 是 decoupled intent**(无 ack,无完成语义,纯 "do this"),Event 是 decoupled observation(id-correlation with Submission)。**消除常见反模式** — 把 Op 当 ack 触发导致请求与 ack 顺序耦合。`Op::RunUserShellCommand` 注释原话:"Output is streamed via ExecCommand* events and the UI regains control upon TurnComplete" — 所有中间结果是 Event,Op 一次性离手
9. **多 agent 模式**:`OnceLock<MultiAgentVersion>` 懒激活,默认单 agent,多 agent 加在 turn 树上 — `InterAgentCommunication / SpawnRequest / AgentControl / AgentScope / AgentVisibility / DeliveryReceipt` 6 个新 Op
10. **Plugin 系统**:marketplace(curated / api-curated / bundled / bundled-alpha / primary-runtime)+ per-thread disable(`Op::ThreadSettings { disabled_plugin_ids }`)+ plugin bundles + executor hooks + git policy + MCP overlay routing
11. **Dynamic tool 运行时注册**:`Op::DynamicToolResponse` + `dynamic_tools` module — clients 在 loop 启动后可加 tool(与 static MCP tools 不同)
12. **自动 schema 生成**:Op / Event / config 全部通过 `schemars+ts_rs` 自动 emit JSON schema + TypeScript 类型,**无 hand-maintained mirror**
13. **多 surface SDK 自动 emit**:Python SDK(`pip install openai-codex`)+ TypeScript SDK 都从同一个 Rust protocol definitions 自动生成
14. **三 OS 沙箱**:`SandboxType::MacosSeatbelt | LinuxSeccomp | WindowsRestrictedToken`,**platform dispatch by `get_platform_sandbox()`**(`sandboxing/src/manager.rs:48-62`),每 OS 后端独立 crate + `#[cfg(target_os = ...)]` 门控。Per-OS sandbox policy 文件**嵌入**(如 `seatbelt_base_policy.sbpl`)启动时加载,profile DSL `codex-rs/execpolicy/` 提供 user-authored prefix/network rules,**amend 进 OS policy**(`blocking_append_allow_prefix_rule`)
15. **execpolicy 是 pure Rust,不是 WASM**(`execpolicy/src/policy.rs:1-70`):`Policy/Rule/NetworkRule/Decision/AmendError`,**全树 ripgrep 无 wasmtime/wasmer** — 之前流传的"WASM policy engine"是错误信息
16. **Apply-patch 是 bespoke DSL,不是 unified diff**(`apply-patch/src/parser.rs:1-40` Lark grammar):`*** Begin Patch` / `*** Add File:` / `*** Update File:` / `*** Delete File:` / `*** Move to:` / `*** End of File` markers + `+`/`-`/` ` 行前缀 + `@@` change-context。`invocation.rs` 用 tree-sitter bash parser 找 heredoc body — **已知失败模式**:模型输出不触发 bash parser 就 silently loses intent
17. **App-server 不是严格 JSON-RPC 2.0**(`rpc.rs:1-2` 显式):Wire 是 `{id, method, params?, trace?}`,**不发送也不期望 `jsonrpc: "2.0"` 字段**
18. **submit channel bounded + event channel unbounded**(`session/mod.rs:587-588`):`async_channel::bounded(SUBMISSION_CHANNEL_CAPACITY)` for submissions(防 stall 模型),`async_channel::unbounded()` for events(防 leak 慢 surface)— **显式 trade-off 但代码未文档化**
19. **Method routing 由 Rust macro `client_request_definitions!` 生成**(`app-server-protocol/src/protocol/common.rs:506`),1800+ 行 literal method definitions(initialize/server/diagnostics/thread/{start,resume,fork,archive,delete,unsubscribe,increment_elicitation}/...),然后 export 到 TS(`ClientRequest.ts`)和 JSON-schema(`codex_app_server_protocol.schemas.json`)。**`JsonSchema` 在 production build 是 no-op alias**,只在 test mode 存在 — 对 JSON-RPC 系统意外
20. **Per-turn retry + pre-sampling compaction budget check**(`session/turn.rs:183-222` `run_pre_sampling_compact`):预算检查在调模型前,**用户拿到 graceful failure 而不是错误流**
21. **Token gating via `Op::SuspendTurnAndShutdown` + `SuspendTurnOutcome`**(`codex_thread.rs:439-467`):worker 把 thread 交回不同进程,原 `turn_id` 保留 — **显式支持 swap-out workers**
22. **SQLite rollout**:session state 持久化到本地 SQLite,**不上云**
18. **`Op::Compact + ThreadMemoryMode`**:context-bounded long threads 不重启 agent,通过 Op 显式触发压缩

**与 lc 现有 engine/ core/ 对照**(agent 跨仓真读):
- `lingclaude/core/` 当前装的是 **横切关注点**(cognitive_rhythm, comfort_zone, audit_collector, governance, hallucination_guard, fact_checker, context_cache, context_compression, file_lock, hooks, intel, l5_conversation_loop, ...)。**没有一个是 harness loop 抽象**
- `lingclaude/engine/` 当前装的是 **工具实现**(bash, ast_edit, file_edit, grep, git, mcp_client, plugin_runner, sub_agent, plan_mode, coding.py)。**没有 turn/thread 边界**
- `lingclaude/cli/input_queue.py` 在 cli/ 里 — **loop 和 UI 没拆开,框架(SQ)的物理形态住在 UI crate 里**

| codex | lc 现状 | 问题 |
|---|---|---|
| `codex-rs/protocol`(SQ/EQ + Op/Event) | 无对应。`cli/input_queue.py` 是隐式 SQ,**且无 schema** | 无 wire contract,无 JSON-RPC surface,无 SDK |
| `codex-rs/core`(`CodexThread` / `Session` / `TurnContext`) | `core/` 是横切,`engine/` 是工具,`cli/repl_turn.py` 是 loop 但被 UI 污染 | loop 没有独立 crate,`#![deny(print)]` 等价物缺失(P0-5 粘贴爆发乱码教训同根因) |
| `codex-rs/app-server`(JSON-RPC)**框架本身** | `lingclaude_sdk/` + `codex-providers/` 是 data plane,但无 wire protocol | 任何 surface(WebUI/IDE/Lark)接入都得 reverse-engineer |
| `codex-rs/cli`(TUI) | `cli/full_tui.py` + `cli/repl.py` 兼渲染 + loop + approval gating | 三合一,无法 swap surface |
| `codex-plugins`(marketplace + disabled_plugin_ids) | `engine/plugin_runner.py` 单点注册;`lingclaude_plugins/` 目录式 | 无 marketplace/upgrade/policy;`disabled_plugin_ids` 无 per-thread 粒度 |

**对 lc 可借鉴的具体做法**(真读版):

| 借鉴点 | 做法 | lc 落地路径 | 难度 |
|--------|------|------------|------|
| **Op/Submission + Event/EventMsg SQ/EQ contract** | 抽 wire-level protocol, id correlation | 新 `lingclaude/protocol/`(抽 `Submission/Op/Event/EventMsg` 四个 core type + JSON Schema auto-emit) | medium |
| **CodexThread-as-single-facade** | Thread = Session + 持久化元数据 + per-thread plugin toggle,作为框架对外最小句柄 | 新 `lingclaude/core/thread.py`(薄包装 conversation loop + SQ/EQ channel, 暴露 ThreadConfigSnapshot + ThreadSettingsOverrides) | medium |
| **`#![deny(print)]` 等价** | ruff rule + pre-commit 禁 framework 层 print(P0-5 粘贴爆发根因) | `pyproject.toml` + `.pre-commit-config.yaml`(B006 / T201 with allowlist)+ CI 步 | low |
| **`Op::ThreadSettings { disabled_plugin_ids }`** | per-thread plugin toggle, 支持 "no MCP network this turn" | `engine/plugin_runner.py` 加 `thread_overrides: dict[str, bool]` + `ThreadContext.__init__` 应用 overrides | low |
| **Marketplace + per-thread toggle** | curated/bundled registries + disable | 新 `engine/marketplace.py` + 扩 `engine/plugin_runner.py` | high |
| **Auto-emit JSON schema + TS types** | pydantic TypeAdapter.json_schema() + 自动 emit | `lingclaude/protocol/schemas.py` emit schemas/*.schema.json, webui/ 直接消费 | low |
| **app-server JSON-RPC 作为 surface boundary** | surfaces 不直接 import core | 新 `lingclaude/daemon/`(own protocol), webui/typescript/cli 通过 JSON-RPC | high |
| **McpToolApprovalMetadataMap with Weak** | weak ref 打破 tool call ↔ approval 循环 | `engine/mcp_client.py` 加 approval gate 时, 存 side-table key=(server_name, request_id) + weak refs | medium |
| **`Op::Compact + ThreadMemoryMode`** | context-bounded long threads, lift compactor 背后 Op | `core/context_compression.py` 现有 compactor 升为 first-class Op event, WebUI/TUI 显式触发 | low |

**建议拆分(最小, P0 优先级)**:
1. 新 `lingclaude/protocol/` — `Submission/Op/Event/EventMsg` 四个 core type + id correlation + JSON Schema auto-emit
2. 把 `core/l5_conversation_loop.py` + `engine/sub_agent.py` 抽出到 `lingclaude/engine/loop/`(或新 `lingclaude/core_thread/`), 暴露 `LingClaudeThread` 作为框架对外最小句柄
3. 把 `cli/input_queue.py` 移到 `protocol/` 或 `engine/loop/`
4. 加 ruff rule + pre-commit: framework 层(任何不在 cli/ 的 code)禁 stdout/stderr 写
5. 新 `lingclaude/daemon/` — 内部 JSON-RPC 接管, WebUI/TS/CLI 都通过它

**Sources**:openai/codex@main(`/tmp/codex-probe`), openai.com/index/unlocking-the-codex-harness, deepwiki.com/openai/codex/1.3-architecture-overview, grapeot.me/share/codex-cli-internals-survey-20260314

---

### 1.2 Hermes WebUI / agent-gateway 家族[C1: **多 repo 源码真读** + 真读 lc agent-gateway 对比]

> **⚠️ 重要纠错(agent 真读后)**:Hermes 自称"自学习 loop"**实际是 on_pre_compress + MemoryProvider 钩子**,**没有真正的"执行 → 提炼 → 沉淀为可重用 skill"闭环**。

**Repos**(真读覆盖 4/5 个,2 个 clone 失败):
- **NousResearch/hermes-agent**(MIT,~96k stars, **Python 3.11+ 全栈**)— 核心 agent
- **JPeetz/Hermes-Studio**(MIT,Node.js + TS,单进程 server-entry.js + Vite/React)
- **EKKOLearnAI/hermes-web-ui**(BSL-1.1 商业源码,pnpm workspaces 5 包)
- **nesquena/hermes-webui**(Python FastAPI,ARCHITECTURE.md 87KB + CHANGELOG 2MB)
- **joeynyc/hermes-hudui**(MIT,Python FastAPI 后端 + React/TypeScript 前端,19 个 tab dashboard)
- ❌ `hermes-webui/hermes-webui` + `orgina/hermes-webui` — 私有,clone 失败

**真读对比关键发现**:
- 5 个候选仓库中 **2 个不可达**(= 社区分流严重)
- Hermes 共同点是 **dashboard / monitoring / 多通道 gateway**,**没有任何一个**实现 lc 那种 2T3A record-first 哲学
- 真正"自学习"能力**无**;记忆层只有 on_pre_compress 钩子 + MemoryProvider 抽象

**架构(NousResearch 为例)**:
```
hermes_cli/main.py
    → GatewayRunner (run.py + 30+ mixin: run_startup/run_goals/run_turn_runner/run_shutdown/run_profile_reconcile)
    → platform_registry 自注册 adapters(Telegram/Discord/Slack/WhatsApp)
    → per-session AIAgent (agent/ 目录 100+ 模块)
    → provider adapter (anthropic/openai/bedrock/azure/codex_responses)
    → state.db SQLite 存 sessions/messages/tool_calls
```

**核心特性(NousResearch/hermes-agent 真读版)**:
1. **多平台消息网关**:Telegram/Discord/Slack/WhatsApp 等,每平台独立 adapter,**platform_registry 自注册零硬编码 if/elif**
2. **GatewayRunner 主循环**(run.py 200+ 行 import + 30+ mixin):telegram 连接超时分级(initial 45s vs reconnect 180s),`_TELEGRAM_NOISY_STATUS_RE` 把压缩/限流/重试日志从 chat 流剥离
3. **house-room peer 模式**:跨实例联邦
4. **per-session AIAgent LRU cache + cgroup 内存压力预算**:`agent_cache_pressure.py` 探测 `memory.high/memory.max`,自动预算 0.65×cgroup 限,**protect_recent=8 不驱逐热 session**
5. **profile 路由(specificity 累加 AND 匹配)**:`profile_routing.py` 中 `guild_id 2 + chat_id 4 + thread_id 8 + user_id 16` 累加,AND 匹配取最高分
6. **自研 cron**:`cron/jobs.py + cron/scheduler.py + cron/constants.py` 用 `jobs.json + fcntl 跨进程锁 + croniter 表达式 + fire_claim at-least-once`(FIRE_CLAIM_TTL_SECONDS=300, SKEW=60, INACTIVITY_HEADROOM=3),**非 OS cron**
7. **MemoryProvider 抽象层**:`agent/memory_provider.py` ABC,生命周期 `initialize→system_prompt_block→prefetch→sync_turn→shutdown`,`plugins/memory/<name>/` 装载,**honcho/hindsight 等第三方可插**
8. **on_pre_compress 钩子**:MemoryProvider 在压缩前落 checkpoint
9. **SKILL.md 模板替换**:`agent/skill_preprocessing.py` 中 `${HERMES_SKILL_DIR}/${HERMES_SESSION_ID}` 模板替换 + !`cmd` 内联 shell

**joeynyc/hermes-hudui 真读核心**:
1. **FastAPI + LocalOriginMiddleware**(loopback-only by default + opt-in remote via `_remote_access_enabled()`)
2. **WebSocket 单例** + **fs 事件驱动**(`file_watcher` → `ws_manager.broadcast_data_changed`),**NFS/WSL1 用 HERMES_HUD_FORCE_POLLING=1 兜底**
3. **19 个 tab**:executive/memory/skills/sessions/replay/cron/health/costs/analytics/plugins/gateway...
4. **ChatEngine 起 hermes chat -q -Q 子进程 + ChatStreamer 行级正则剥盒线装饰**(run_state 跟 first_token/process_start/total 时序)
5. **collector 读 ~/.hermes/ JSON/SQLite** 报 **managed/direct/unavailable 三态** + safe_actions 列表
6. **Safe Share 默认脱敏**(token/email/path/工具参数),导出 **Ed25519 本地签名 + 本地 hash**

**lc agent-gateway 真读 + 对比**(agent 跨仓读):
- lc `plugins/agents/proj_agent_gateway/server.py`:**FastMCP stdio 5 工具**(agent_invoke/chat/batch/status/list),`_AGENTS` 表数据驱动 cc/codex/crush/opencode/ac,`QUOTA_ARGV/PROFILE_ARGV` 透传 model/provider,**fallback opencode→crush,`_COOLDOWN_S=900` 健康门禁**
- lc `plugins/agents/proj_agent_gateway/plugin.py`:AgentSeam 协议 run/abort/status,**JSON-RPC id 锚定**,健康探针 initialize 验证 FastMCP 可达
- lc `manifest.agent.json`:trust_level=T3, plug_level=L2, federation_pair=lc-ac

**lc vs Hermes 对比**:
- **lc 已有**:FastMCP stdio + 5 工具面 + _AGENTS 表 + QUOTA/PROFILE 透传 + 健康门禁 + 真并行 + agent_run record + AgentSeam 三动作
- **lc 缺**:WebSocket file_watcher 实时推送、specificity 多维路由、plugin 自注册 + deferred loaders、cgroup 内存压力预算、MemoryProvider ABC + on_pre_compress、自研 cron(fcntl + fire_claim)、Loopback-only 安全中间件、ChatStreamer 行级装饰过滤、managed/direct/unavailable 三态

**弱点(警觉)**:
- NousResearch:1300+ 文件 gateway 层(Python)单仓,GatewayRunner 主类超大(run.py 200+ 行 import + 30+ mixin 拼),模块边界靠 MRO 拼装**不是显式 DI**,新人 onboarding 极重;state.db SQLite 单文件,scale 100+ sessions/s 后 fsync 压力大;profile_routing 隐式按 specificity 静默匹配,**无 dry-run,误配难发现**
- joeynyc:单进程 WebSocket 广播,所有 tab 共享一条 broadcast,**大消息会阻塞**;LocalOriginMiddleware 仅防 cross-site,**不能防 LAN snooping**;Replay 本地 Ed25519 签的是本机完整性**不是第三方背书**(README 自己声明);collector 全靠读 ~/.hermes/ JSON/SQLite,hermes 一升级 schema 就要同步跟
- JPeetz:单进程 server-entry.js + Vite dev 双端口,Vite proxy 转发到 server;DEVLOG 53KB 但 FEATURES-INVENTORY 31KB 维护负担
- EKKOLearnAI:BSL 1.1 **非 OSI 开源**,商业落地要评估授权;5 包 monorepo + 固件 + desktop,build 链复杂度高
- nesquena:ARCHITECTURE.md 87KB + CHANGELOG 2MB,**文档超载**;代码本体仅 api/ + mcp_server.py + bootstrap.py,**核心实现薄**
- **Hermes 家族共通:5 个候选中 2 个 clone 失败**(= 私有或删库),**社区分流严重**

**对 lc 可借鉴的具体做法**(真读版):

| 借鉴点 | 做法 | lc 落地路径 | 难度 |
|--------|------|------------|------|
| **specificity 累加 AND 路由**(user_id 16 + thread 8 + chat 4 + guild 2) | 多维 profile 路由 | `core/scheduler.py` 单 key → specificity 多维 + `plugins/agents/proj_agent_gateway/server.py` 的 `_AGENTS` 表扩 route 字段 | medium |
| **platform_registry 自注册 + deferred loaders** | plugin 目录懒注册,代码改插件不改主机 | `core/seam.py` SeamRegistry + `plugins/agents/*/plugin.py` 的 register | medium |
| **WebSocket + file_watcher 实时推送** | fs 事件 → `ws_manager.broadcast_data_changed(data_type)` | `webui_seam.py` 改 watcher 推送 + `plugins/agents/proj_agent_gateway/plugin.py` 的 `_record_health` | low |
| **Loopback-only + opt-in remote 中间件** | 安全模型:0.0.0.0 不默认 | `webui_seam.py` + `api.py` 加 `LocalOriginMiddleware` + `_remote_access_enabled()` env 开关 | low |
| **collector 读 ~/.hermes/state.db + tool_calls 反查面板** | agent_run → SQLite 工具调用反查 | `core/layered_memory.py`(SqliteStoreBase)+ `data/agent_runs/` 迁 SQLite | medium |
| **自研 cron(jobs.json + fcntl + croniter + fire_claim at-least-once)** | 跨进程 cron + 持久化 | `core/scheduler.py` + `data/cron/` 子目录(新建)| medium |
| **AIAgent LRU cache + cgroup 内存压力预算** | 高频 ac 调 lc 守卫场景 | `core/seam.py` SeamType.AGENT 缓存 + `plugins/agents/proj_agent_gateway/plugin.py` per-session cache | high |
| **MemoryProvider ABC + on_pre_compress 钩子** | 第三方 memory 可插(honcho/hindsight) | `core/layered_memory.py` 抽 MemoryProvider ABC + `plugins/memory/<name>/` | medium |
| **ChatStreamer 行级装饰过滤** | 提纯 tokens,剥盒线 | `plugins/agents/proj_agent_gateway/server.py:218-258` `_run/_enrich` 加行级过滤层 | low |
| **managed/direct/unavailable 三态 + safe_actions** | 双击确认危险操作 | `plugins/agents/proj_agent_gateway/server.py:170-211` `_COOLDOWN/_fallback_of` 扩三态 | low |

**Sources**:github.com/NousResearch/hermes-agent, github.com/JPeetz/Hermes-Studio, github.com/nesquena/hermes-webui, github.com/joeynyc/hermes-hudui, github.com/EKKOLearnAI/hermes-web-ui(BSL 1.1),github.com/hermes-webui/hermes-webui(私有,clone 失败),github.com/orgina/hermes-webui(私有,clone 失败)

---

### 1.3 Orca(stablyai/orca)[C1: **源码真读**(Bash 克隆到 /tmp/orca-read,version 1.4.197)]

**Repo**:github.com/stablyai/orca (MIT, version 1.4.197, ~53k stars,Electron + React 19 + TS + Zustand)

**真读关键修正**(原 web search 综述有几处不准):
- **"Worktree pool/lock" 是错的** — 实为 slot-budgeted tick(每 `worker-start` 调 `addWorktree` with `--no-track -b <branch>`,**不是 pool/lock 模型**)
- **`coordinator.start` 是 RETIRED** — `coordinator-start` 现在是 no-op skill-loader,用户必须手动 `task-create / task-update` 组合 task DAG
- **Agent hibernation 是 renderer-only**,**不是 daemon**
- **CLI ↔ UI 是 bidirectional unified** — same RPC contract,both renderer 和 CLI 调相同 methods
- **lc 已经发明了 worktree+claim 模式**(`scripts/worktree_node.py` + `plugins/agents/work_claim.py`)— Orca 借鉴主要强化现有

**架构(真读)**:
```
src/main/         Electron main(git/worktree-add.ts, daemon/(PTY), ssh/, browser/(CDP + agent-browser-bridge), runtime/orchestration/(Coordinator + SQLite DB))
src/preload/      contextBridge IPC surface
src/renderer/     React + Zustand(browser-pane/, store/slices/, lib/agent-hibernation-*)
src/relay/        WebSocket relay daemon(dispatcher.ts JSON-RPC, pty-handler.ts, agent-exec-handler.ts, git-handler-*.ts, ssh-pty-*.ts, skill-install-handler.ts)
src/cli/          scriptable orca binary — specs/orchestration.ts(declarative CommandSpec)+ handlers/orchestration/*.ts(impure)
src/shared/       contracts (orchestration-rpc-contract.ts, skill-bundle-manifest.ts) + zod schemas
native/           computer-use-{linux,macos,windows}, keyboard-layout-macos, notification-status-macos
mobile/           iOS/Android React Native companion
```

**核心特性(真读版)**:
1. **Worktree-as-isolation**(真读):`git worktree add --no-track -b <branch>` per agent + `branch.<branch>.base` config 持久化 + `push.autoSetupRemote=true`(line 230-239)。SSH 路径(`relay/git-handler-worktree-ops.ts:32-198`)**comment 显式说 "Mirrors local addWorktree exactly"** — SSH/local 两个维护一份
2. **Slot-budgeted tick coordinator**(`coordinator.ts:107-114`):`while (!stopped) { tick(); sleep(pollIntervalMs); }`,每 tick 流程 = `processMessages / processEscalations / reblockTasksWithPendingGates / warnStaleDispatches / dispatchReadyTasks / checkConvergence`。`dispatchReadyTasks` 算 `slotsAvailable = maxConcurrent - dispatched.length`,fetch `baseDrift` snapshot **once per tick**,每 task 派一个 terminal(slot-based,**NOT pool/lock**)
3. **CLI orchestration 真读细化**(`specs/orchestration.ts:5-281`):
   - run: `run-create / run-use / run-current / run-list / run-show`
   - inter-agent: `send / check / reply / inbox`
   - task: `task-create / task-list / task-update`(server-side abbreviation `--brief`)
   - worker: `worker-start / worker-show / worker-read / worker-stop / worker-abandon / worker-release / worker-retain / worker-list`
   - dispatch: `dispatch / dispatch-show`(read-only outcome probe)
   - decision gates: `gate-create / gate-resolve / gate-list`
   - ask / request-show
   - **RETIRED** `coordinator-start / coordinator-stop`(throws `orchestration_migration_required`)
   - 完整 RPC contract: `ORCHESTRATION_MUTATION_METHODS` + `RETIRED_ORCHESTRATION_METHODS`(run, runStop)
4. **Inbox 持久化真读**:`orchestration-db.ts`(better-sqlite3 + **WAL + synchronous=NORMAL + busy_timeout=5000**),`messages` 表 columns = `id / run_id / delivery_contract / from_handle / to_handle / subject / body / type / priority / thread_id / payload / sender_pane_key`。特殊 rowids: `UNBOUND_RUN_ID='run_unbound'`(terminals in no Run)+ `ORCHESTRATION_LEGACY_RUN_ID='run_legacy_local'`(pre-Run data)。`getUnreadMessages` filter `WHERE to_handle=? AND read=0 AND delivery_contract='current_delivery' ORDER BY sequence` — **current_delivery is FIFO batch contract**。**SAVEPOINT `message_insert_batch` + `WORKER_DONE_MESSAGE_SAVEPOINT`** atomic multi-message inserts
5. **Decision gate 真读**(`decision-gate-store.ts:1-187`):`createGate` SAVEPOINT requires task exists + no active worker + no active dispatch + transitions task to `blocked`;`resolveGate` updates gate `resolved` + task to `ready`;`timeoutGate` guards status='pending'(avoid overwrite user-resolved)
6. **Agent hibernation 真读**(`agent-hibernation-pane-eligibility.ts`):**renderer-side planner**,不 daemon。Eligibility 需要 `state==='done' AND not interrupted AND no subagents AND dispatchStatus ∈ {completed,failed,circuit_broken} AND no live resume anchor AND isResumableTuiAgent AND has provider session AND getAgentResumeArgv AND idleMs window since `effectiveIdleStart = max(stateStartedAt, foregroundLastSeenAt, ptyBindingFirstSeenAt, boundaryResolvedAt)`**。**floor-anchored idle clock** 为防 OSC 9999 repaints / reconnect replays 推进 `updatedAt` 重启 countdown。`agent-background-session-exit.ts` 的 `isProvenProcessExit` required to drop identity,synthetic loss 只 retire transport。**Hibernation 60s tick** — comment 提到 "would miss a boundary written and cleared between two samples"
7. **Worker terminal-state enum**:`active | reclaimable | retained | release_pending | release_unknown | released` — **separate accounting from task lifecycle**(a completed task can still own a live terminal)
8. **Mutation idempotency**:`mutation-request.ts` 包裹每个 state-mutating CLI call with `--retry-request <id>`;daemon 记录 outcome;**replay replays the recorded outcome**;`request-show` 是 safe post-hoc probe
9. **Design Mode 真读**(`design-mode.mdx:1-32` + `agent-browser-bridge-capture-commands.ts`):cursor 变 picker → click captures `outerHTML + neighborhood + computed CSS + cropped screenshot + (optional) source-map file/line` → ships as one attachment 到 active agent terminal。Capture 走 `withSerializedScreenshotAccess` + `acquireAutomationVisibility(webContentsId)` lease,compositor paints fresh frame inside screenshot lock
10. **Skills Framework 真读**(`skill-bundle-manifest.ts`):`SKILL_BUNDLE_SCHEMA_VERSION=1` + `AGENT_PLUGIN_SCHEMA_V1='https://agent-plugins.org/schemas/1.0.0/plugin.schema.json'` + per-skill SHA-256 + ordered by name + case-fold uniqueness + `validateSkillPackagePath` + SKILL.md required + **max 512 files / 32MB**。Install contract:discriminated union ingress(`download-grant / staged-upload / local-file`)+ conflictDecisions(`keep-local / replace-unmodified / replace-and-discard-local`)+ placementResult topology(`canonical-copy / provider-alias / independent-copy`)
11. **PTY = separate daemon process**(`src/main/daemon/`):protocol-versioned client hello + session ownership grants + checkpoint serializer + source-credit flow control。UI 是 thin consumer
12. **CLI 身份声明**:`terminal-identity.ts:13-30` 显式 flag > `ORCA_TERMINAL_HANDLE` env > live probe + pane remint,`validateEnvHandle` 拒绝 stale env after remint。CLI 读 `ORCA_TERMINAL_HANDLE` + `ORCA_PANE_KEY` env 声明身份,same binary work as both UI-driven and headless

**真读到的关键文件**:
- `src/cli/specs/orchestration.ts:5-281`(full CommandSpec surface)
- `src/main/runtime/orchestration/coordinator.ts:38-317`(Coordinator class executeLoop)
- `src/main/runtime/orchestration/db/orchestration-db.ts:1-51`(SQLite + WAL)
- `src/main/runtime/orchestration/db/messages/message-insert.ts:1-100`(inbox persistence)
- `src/main/runtime/orchestration/db/decision-gates/decision-gate-store.ts:1-187`(gate lifecycle)
- `src/relay/git-handler-worktree-ops.ts:32-198`(SSH parity, "Mirrors local addWorktree exactly")
- `src/main/git/worktree-add.ts:178-244`(local worktree creation, `--no-track -b`)
- `src/renderer/src/lib/agent-hibernation-pane-eligibility.ts:1-163`(renderer-side hibernation + floor anchoring)
- `src/renderer/src/lib/agent-hibernation-pane-age.ts:1-107`(floor-anchored idle clock rationale)
- `src/shared/skill-bundle-manifest.ts:1-164`(V1 schema)
- `src/shared/orchestration-rpc-contract.ts:1-50`(RPC contract enumeration)
- `src/cli/handlers/orchestration/mutation-request.ts:1-22`(`--retry-request` idempotency)
- `src/main/browser/agent-browser-bridge-capture-commands.ts:1-100`(Design Mode capture)

**弱点(警觉)**:
- ~2500 test files for production code,heavy test overhead
- Contract enumeration hand-maintained — drift 风险
- **`coordinator.start` 是 RETIRED**,用户必须手动组合 task DAG — **no auto-decomposition**(`decompose()` throws "No tasks found")
- Two SQLite DBs with overlapping responsibilities
- SSH parity hand-maintained — `relay/git-handler-worktree-ops.ts` comment 多次说 "Mirrors local addWorktree exactly"
- Agent hibernation **renderer-only**,daemon/orchestration DB **no idle concept**
- Decision-gate status enum 静默太宽(`pending / resolved / timeout`)**无 `cancelled`**;`timeoutGate` guards 仅防 overwrite resolved,不防 concurrent resolve vs timeout races

**对 lc 可借鉴的具体做法**(真读版):

| 借鉴点 | 做法 | lc 落地路径 | 难度 |
|--------|------|------------|------|
| **Worktree 配置持久化 + SSH parity** | `--no-track -b <branch>` + `branch.<name>.base` + `push.autoSetupRemote=true` | `scripts/worktree_node.py:71` 补这两个 `git config` 调用 + scan-cache invalidation + `--no-track` | low |
| **Slot-budgeted tick coordinator** | `maxConcurrent - dispatched.length` slots + per-tick base snapshot + DAG convergence | `coordination/bus_responder.py:38-80` 加 `max_concurrent` + ready-queue + per-tick base-snapshot + DAG-converged termination | medium |
| **Floor-anchored idle clock** | `effectiveIdleStart = max(stateStartedAt, foregroundLastSeenAt, ptyBindingFirstSeenAt, boundaryResolvedAt)` 防 repaint/reconnect restart countdown | `core/scheduler.py` 或 `scripts/worktree_node.py` TTL checks 应用同 floor anchoring | low |
| **`--retry-request` idempotency + `request-show` 探针** | mutation CLI call 带 `orchestrationRequestId`,daemon 记录 outcome,replay 复放 | `data/atom_code_call.jsonl` 加 `request_id` 字段,`cli/commands.py` callers 传 `--retry-request` | medium |
| **Decision gate as first-class lifecycle blocker** | `task.status='blocked'` 直到 gate resolves,`createGate` SAVEPOINT requires no active worker/dispatch | `data/arch_ledger/decision_gates/` 子目录 `{id, task_id, question, options_json, status, resolution, resolved_at}`;task dispatch 加 gate state 检查 | high |
| **SQLite + WAL + SAVEPOINT 高频 inbox** | better-sqlite3 + WAL + busy_timeout=5000 + SAVEPOINT message_insert_batch | `data/arch_ledger/` 热路径(LingBus inbox, atom_code_call receipts)迁 SQLite;JSON 作 human-readable export | high |
| **Worktree + claim dual-record**(lc 已有 + Orca extras) | worktree_node.py + work_claim.py 已发明,加 Orca extras | `scripts/worktree_node.py` + `plugins/agents/work_claim.py` 同步强化 | low |
| **SSH/long-path aware worktree** | `windowsLongPathGitArgs(targetDir, platform)` — SSH host's git matters, not client's | `coordination/` 加 remote-server worktree support 时同步 | medium |
| **Skill bundle V1 schema + conflictDecisions** | zod 512-file cap / 32MB cap / case-fold uniqueness / SKILL.md required / conflict decisions / placement topology | `skills/` 加 `manifest.schema.json` + install orchestrator 替换 ad-hoc file copies | medium |
| **CLI ↔ UI single RPC contract** | `ORCHESTRATION_RPC_METHODS` set enumerated once in shared/,used by both renderer and CLI | `lingclaude/coordination/bus_responder.py` + `lingclaude/cli/repl.py` define `CoordinationRpc` enum | medium |
| **Background-exit proven-vs-synthetic** | `isProvenProcessExit` required to drop identity,synthetic loss 只 retire transport | `lingclaude/coordination/bus_consumer.py` 采用 proven-vs-synthetic exit classification | low |
| **Worker terminal-state enum** | `active / reclaimable / retained / release_pending / release_unknown / released` 与 task status 分离 | `lingclaude/plugins/agents/work_claim.py` 加 `terminal_state` 字段独立于 claim state | low |

**Sources**:github.com/stablyai/orca(/tmp/orca-read clone, version 1.4.197), writeble.com/2026/06/28/orca-introduces-typescript-based-desktop-ade, Quriosity-agent/articles/2026-05-14-orca-parallel-agent-ide-control-plane-en.md, github.com/stablyai/orca/.../docs/site/content/docs/browser/design-mode.mdx

---

### 1.4 Penguin Harness(Prism-Shadow/penguin-harness)[C1: **源码真读**(Bash 克隆到 /tmp/penguin-read)]

**Repo**:github.com/Prism-Shadow/penguin-harness (Apache-2.0, TS monorepo, Node >= 24, pnpm 11)
**作者**:Yaowei Zheng(LlamaFactory 作者)+ PrismShadow AI Team
**完整真读 JSON**:`/home/ai/lingclaude/.atomcode/notes/penguinharness-rsi-deep-read-2026-09-20.json`

> **⚠️ 架构校正**:web search 综述的 `core/ cli/ sdk/ desktop/ web/` 不是真布局。
> **真布局**:`packages/{cli,core,desktop,docs,hmr,landing,server,web}` + `plugins/`(17 个 npm 包,各自 plugin.json + skills/<n>/SKILL.md + hooks/*.mjs)
> **没有 "sdk" 包** —— `@prismshadow/penguin-core` 本身就是 SDK(index.ts 导出 Agent/Session/createAgent/createSession/omnimessage/trace/plugins/hooks)

**架构(monorepo)**:
```
packages/core    @prismshadow/penguin-core — SDK(context_engine, OmniMessage, Agent/Session, Trace writer, hooks runner)
packages/cli     commander-based CLI, thin HTTP/SSE client to server
packages/server  HTTP/SSE backend, CLI/Desktop/Web 共享
packages/desktop Electron shell, forks penguin-server as utilityProcess
packages/web     Vite/React SPA, server 同源服务
packages/hmr     runtime hot-update platform code(POST /api/hmr/upgrade)
plugins/         17 npm 包(@penguinharness/<name>),每个 self-contained(plugin.json+skills+hooks)
examples/        sample apps
```

**核心特性**:
1. **RSI(Recursive Self-Improvement)** —— **"Optimizer" 和 "Evaluator" 不是 TS 模块,是 agent 跑 SKILL.md 协议**(4 个 skill):
   - `agent-initialization`(103 行)— AGENTS.md + skills + thinking_level
   - `benchmark-design`(206 行)— Pilot=1 Run/Case, publish gate=score<85, status=published/failed/draft
   - `agent-evaluation`(140 行)— 4 个 failure code,返回纯协议 YAML, rubric 私有
   - `agent-optimization`(131 行)— **8 步循环: evidence → hypothesis → Candidate → evaluate → accept/rollback**;**Snapshot 规则**:每次 round 前 `agent_state/` 原子 `tar.gz` 到 `<target>/snapshots/v<version>.tar.gz`(排除 `.vault.toml`),版本号单调递增,**已拒绝版本不重用**
2. **"Agents build agents"**:一句话生成 self-contained 应用, RAG recipe included,**嵌入 agent 的 AGENTS.md = 应用 persona**
3. **1000+ model 适配**:DeepSeek/GLM/Kimi/Qwen/GPT/Gemini/Claude/Inkling + 任何 OpenAI 协议端点
4. **Append-only JSONL Trace,设计目的就是 crash-torn-tail recovery**:
   - **每条记录走单次 `write(2)` on O_APPEND** —— **显式拒绝 Node `fs.appendFile`**,因为 512 KiB 分片会在进程崩溃时留下 torn 行,**粘到后续所有记录破坏整文件**(writer.ts:71-101)
   - **per-instance Promise chain 串行化** 解决 LLM 流 + 并行 tool 多 producer 竞态
   - **`probeTornTail`** 每 shard 首写探最后字节是否 `\n` 决定补前缀换行
   - **child session 消息永不写入本 trace**,只用 `subagent` 指针事件记 child session id,否则污染本 session 统计
5. **Plugin 库(17 plugins, 4 categories),文件即真源**:plugin.json + skills/<n>/SKILL.md + hooks/*.mjs,**不编译,不解缓存**
6. **Skills 安装** = 在 `agent_state/skills/<n>/` 丢 SKILL.md。**Frontmatter**(name+description)**自动注入系统提示词**;full body "index first, body on demand" —— agent 真用时通过 shell 读
7. **Goal mode + continual learning 都是 hook package**(Node 脚本,不是 core logic):start.mjs 写 GOAL.json, stop.mjs 读 Trace 派下一轮
8. **Privacy boundary**:Test Agent 只看 `statement/`,永远不看 `rubric/` / 黄金答案 / 评分规则;Optimizer 永远不看 Evaluator 私有;**污染规则** —— private info 进 optimizer context → restore active Candidate + stop
9. **Scoreboard YAML 权威化**:`benchmarks/<id>/scoreboard.yaml` 是 single source of truth,**禁止 server/frontend/script 重算或校验**;Score = 2dp rounded; cost = 6dp; duration_ms = nearest int
10. **Live-server 锁**(per data root):second `penguin server` 同 root → refuse + 报 URL;second `penguin web` → 直接 attach 已存在实例
11. **CLI = 薄 HTTP/SSE 客户端**:`penguin run` 是 Session POST + SSE 订阅;`penguin chat` REPL 复用同 primitives
12. **Agent Company mode**(CEO/HR/Finance/Researcher/Mirror 数字孪生, all-hands channel 董事会批准)

**真读到的关键文件**:
- `packages/core/src/trace/writer.ts:1-278`(append-only JSONL + torn-tail heal + 单 write(2))
- `packages/core/src/plugins/index.ts:1-533`(PLUGIN_CATEGORIES ×4, workspacePluginRoot, parseSkillFrontmatter)
- `plugins/agent-tuning/skills/agent-optimization/SKILL.md:1-131`(8 步循环 + snapshot 规则)
- `plugins/agent-tuning/skills/agent-evaluation/SKILL.md:1-140`(4 failure codes, private rubric)
- `plugins/agent-tuning/skills/benchmark-design/SKILL.md:1-206`(Pilot 设计 + publish gate)
- `plugins/agent-tuning/skills/agent-initialization/SKILL.md:1-104`(AGENTS.md 行为层)
- `packages/cli/src/commands/run.ts:1-180`(SSE 客户端模式)
- `packages/cli/src/commands/serve.ts:331-381`(live-server 锁)
- `packages/desktop/src/main.ts:1-60`(Electron utilityProcess 模式)

**弱点(警觉,不要照搬)**:
- Scoreboard YAML 可手编,无 DB 事务;并发写可能 clobber,他们的兜底是"append + verify before reporting"
- Snapshot 排除 `.vault.toml` → secrets 不快照,但跨机恢复没解决
- Trace writer 的 torn-tail heal 插字面空行 → 依赖 reader 容忍,严格 parser 会断
- 两套协议版本(YAML over subagent + skill 指令)易漂移, SKILL.md 硬编码措辞("Return only the clean YAML")缓解
- Goal mode hook 无沙箱, malformed `stop.mjs` 会卡死 loop
- Desktop Electron utilityProcess → RAM 成本 > TUI/REST-only
- **Evaluator 评分模型本身的漂移是静默的**,无自检

**对 lc 可借鉴的具体做法**(真读版):

| 借鉴点 | 做法 | lc 落地路径 | 难度 |
|--------|------|------------|------|
| **Snapshot-before-every-round** | `tar.gz` 版本化(已拒绝不复用) | `self_optimizer/optimizer.py` 加 `SnapshotManager`,`OptimizationRequest` 扩 `snapshot_path` 字段 | medium |
| **Authoritative scoreboard YAML** | append+verify,禁止重算 | `self_optimizer/benchmark.py` 把 `scoreboard.yaml` 作为 canonical artifact,锁 schema | low |
| **Append-only JSONL + 单 `write(2)` O_APPEND** | **显式拒绝 `fs.appendFile`**(512KiB 分片 torn 行) | `core/layered_memory.py` + journal sink,加 tail probe | low |
| **Skills frontmatter 自动注入 + body on demand** | 索引在前, body shell 读 | `.claude/skills/` 确认 frontmatter auto-inject | low |
| **Optimizer/Evaluator 拆双角色 + 隐私边界** | rubric 私有,污染规则 | `self_optimizer/evaluator.py` 拆双角色,显式 visibility list | high |
| **`preinstall: false` 控制默认加载** | opt-in capability | lc skill/hook metadata 加 `preinstall` 字段 | low |
| **Live-server 锁 per data root** | second `server` refuse / second `web` attach | `core/scheduler.py` 加 `~/.lingclaude/data/.lock` 或 per-project lock,区分 refuse vs attach | medium |
| **Goal mode = hook package** | start/stop.mjs 拆 hook, core 只跑 hook | `core/scheduler.py` + `cli/commands.py` 拆 core orchestration + pluggable hook packages | medium |
| **CLI = 薄 HTTP/SSE 客户端** | 共享 server 边界 | `cli/commands.py` + `cli/repl.py` 抽 server 边界,`model_call.py` 变 client | high |

**Sources**:github.com/Prism-Shadow/penguin-harness, penguin.ooo/docs/skills, penguin.ooo/docs/self-improvement, everydev.ai/tools/penguinharness,**本地 JSON**:`.atomcode/notes/penguinharness-rsi-deep-read-2026-09-20.json`

---

### 1.5 Hermes Agent + Hermes Studio(NousResearch + 社区 dashboard)[C1: **双 repo 深度真读**(Bash 克隆到 /tmp/hermes-read)]

> **⚠️ 真读校正(对 §1.2)**:Hermes "self-improving loop" **更精确** = "LLM-judge background review fork that proposes MEMORY.md edits every 10 turns" — **不是** true self-learning。**lc `self_optimizer + PatternRecognizer` 已经超过它**,**不要把它当 self-improvement 黄金标准**。
>
> **Hermes-Studio = thin reverse proxy + JSON store + 30+ UI features**,**NO business logic**(都跑 Hermes FastAPI endpoints)。**不要 over-credit Studio**。

**Repos**(双 repo 真读):
- `github.com/NousResearch/hermes-agent`(MIT, Python 3.11+ FastAPI gateway + asyncio + small Node TUI/Web bundle)
- `github.com/JPeetz/Hermes-Studio`(MIT, TS + React 19 + TanStack Router Vite SSR)

**架构(真读)**:
```
hermes-agent: providers/ registry → plugins/model-providers/<name>/(39 plugins) → agent/transports/<api_mode>.py(4 transports) → AIAgent
              gateway/platforms/<name>.py(built-in)+ plugins/platforms/<name>/(plugin adapters)→ BasePlatformAdapter.build_source()→ gateway/session.py:664 build_session_key()→ SessionStore(state.db)
              cron/(27 submodules,jobs/scheduler/scheduler_delivery/scheduler_tick/suggestions 完整 pipeline)
              agent/(memory_manager + memory_provider ABC + skill_manager_tool + skill_bundles + turn_context + turn_finalizer + background_review)
Hermes-Studio: thin reverse proxy: routes/api/* → fetch(HERMES_API_URL=127.0.0.1:8642)
              local state stores: src/server/{crew,workflow,task,template,agent-definitions,event,cost,run,local-session}-store.ts → .runtime/*.json
              UI screens: screens/{crews,conductor,chat,memory,settings,jobs,agents,audit,operations,...}/ — React + TanStack Router
              capability probe: server/gateway-capabilities.ts detects sessions/skills/jobs/enhancedChat/memory/config degrades to portable mode
```

**核心特性(真读版)**:

**hermes-agent:**
1. **Self-improving loop(真读精确)**:agent-curated `MEMORY.md/USER.md/SOUL.md`(frozen snapshot for prefix-cache)+ `skill_manager` tool 创建 SKILL.md at `~/.hermes/skills/<category>/<name>/` + **post-turn background review fork**(每 10 turn `_tick_memory_nudge → should_review_memory`,LLM-judge 决定是否 save) — **sustained overhead 因每 turn costs LLM tokens**
2. **跨 session recall 真读 3 层**:
   - (i) `state.db` messages lineage via `resolve_resume_session_id()` walks `parent_session_id` chain
   - (ii) `MemoryStore` frozen snapshot at session start
   - (iii) external `MemoryProvider`(Honcho dialectic multi-pass with depth cap)
3. **MemoryProvider ABC**:honcho / mem0 / hindsight / supermemory 4 个外部 provider + `agent/memory_manager.py:1-120` 的 fan-out + `_signature_params` + `normalize_tool_schema`
4. **Multi-channel gateway(BasePlatformAdapter ABC)**:**26+ adapters**(Telegram/Discord/Slack/WhatsApp/Signal/BlueBubbles iMessage/WeChat/WeCom/Email/Feishu/Dingtalk/Line/Matrix/IRC/SMS/Mattermost/Teams/GoogleChat/HomeAssistant/SimpleX/MQTT/A2A/Photon/Raft/Ntfy)。**Mixed built-in vs plugin**:BlueBubbles/WeChat/WhatsApp/Signal built-in,Telegram/Discord/Slack plugins — **不一致**(警觉)
5. **39 model provider plugins**:`ProviderProfile` dataclass + 4 transports(anthropic_messages / codex_responses / chat_completions / bedrock_converse)+ community `models.dev` catalog(4h TTL)
6. **MCP tri-role 真读**:
   - consumer(`tools/mcp_tool_discovery.py:443` register_mcp_servers)
   - server(`mcp_serve.py:264,458,685-689,692`,**10 tools**)+ `EventBridge + _ToolHandlers + _TOOL_NAMES + create_mcp_server`
   - reverse-server for codex(`agent/transports/hermes_tools_mcp_server.py:47,61`,`EXPOSED_TOOLS + _build_server`)
   - **plugin-catalog exact-SHA pin + removed.yaml blocklist**(安全 primitive)
   - optional-mcps with 66 pre-vetted manifests
7. **Cron 真读 27 submodules**:`cron/jobs.py` + `cron/scheduler.py` + `cron/scheduler_delivery.py` + `cron/scheduler_tick.py` + `cron/suggestions.py`
   - self-hosted `jobs.json` + `croniter` + `fcntl` 锁
   - `fire_claim` at-least-once
   - 4-lane delivery(bot-chat / live adapter / deferred queue / local)
   - **consent-first self-triggered** via `cron/suggestions.py:add_suggestion → create_job_with_scheduler_registration`(后台观察 → 提议 → 用户同意 → 调度)
   - **fire_claim at-least-once via fcntl works only on Unix**(msvcrt fallback on Windows **unverified**)
8. **State 层真读**(state.db SQLite, schema v30):
   - WAL mode
   - `hermes_state.py` SessionDB mixin stack,_open_writer / _lockguard / _read_ctx / _execute_write
   - `hermes_state_common.py:338-580,708-915` SCHEMA v30 + FTS SQL
   - `hermes_state_messages.py` append_message, replace_messages, archive_and_compact, **resolve_resume_session_id, get_resume_conversations, rewind_to_message**
   - `hermes_state_search.py:1061,1167,983,793` **FTS5 routing + CJK + LIKE fallback + _sanitize_fts5_query**
9. **Compression/rewind lineage 真读**:
   - soft-archive watermark
   - `parent_session_id` chain
   - `/undo /retry` 跨 session
10. **Voice memo transcription 真读**:provider-pluggable(faster-whisper default + Groq/OpenAI/Mistral/xAI/ElevenLabs/DeepInfra),**gateway-enriched before AIAgent sees it**
11. **Delivery ledger 真读**:`gateway/delivery_ledger.py:33-36,41-50,59` durable obligation ledger state machine(`pending → attempting → delivered/failed/abandoned`,**flood-aware**)

**Hermes-Studio:**
1. **Multi-agent crews 真读**:**pure Studio-level fan-out**(one session per member),**NOT an in-Studio orchestrator**。`POST /api/crews/:id/dispatch → POST /api/send-stream` per member sessionKey,tracked via SSE `/api/chat-events`
2. **Conductor V2 真读**:**gateway-native orchestrator**,spawn one orchestrator session via cron job,**orchestrator uses native create_task tool** to spawn worker subagent sessions;Studio polls `/api/sessions` every 3s。**maxParallel 和 supervised 是 PROMPT HINTS to orchestrator,不是 enforced by Studio**(警觉)
3. **Profile-scoped workspaces 真读**:`~/.hermes/profiles/<name>/` per-crew-member,FileExplorer passes `?profile=` on every fetch,**path-traversal guarded server-side**
4. **Visual workflow builder**:`workflow-builder.tsx:46-69,506-603` SVG canvas + **Kahn BFS topo layers + DFS cycle detection** + per-layer dispatch via SSE run_end listener
5. **Interactive Knowledge Graph 真读**(`knowledge-browser-screen.tsx:182-246,248-518,680-699`):
   - **独立 filesystem scan** of `~/.hermes/knowledge/**/*.md` with YAML frontmatter `type` field
   - **`[[wikilink]]` regex 抽取 edges**
   - **force-directed 280 iter simulation**(无第三方 graph library)
   - **5 type colours**:guide / project / reference / concept / note(全 from frontmatter)
   - degree-sized nodes + drag / hover-zoom / pan
   - **no chokidar watcher** — every API call re-scans O(files)
6. **Cron Job Manager 真读**(`jobs-screen.tsx:99-120,122-424,432-436,529-657`):
   - thin proxy to Hermes `/api/jobs` + live SSE EventSource for `/v1/runs/{id}/events`
   - `refetchInterval 30s`
   - `DELIVERY_OPTIONS=[local, telegram, discord]`(**UI/help copy claims Slack/Email 但实际只有这 3 个 — 撒谎**,警觉)
7. **Execution Approvals 真读**(`approval-card.tsx:90-118`):
   - **4 button scopes**:Approve Once / This Session / Always Allow / Deny
   - **sessionStorage persistence**(`approvals-store.ts:12-165` ApprovalRequest status pending/approved/denied/always-allowed)
   - **dual-strategy resolution**:`native /api/sessions/{key}/approve → fallback /approve chat command`(`approvals.$approvalId.approve.ts:36-101`)
   - **dual-strategy silently fallback to chat command if gateway endpoint fails** — receipt 可能 not show in chat immediately(警觉)
8. **MCP Server Management UI**(`mcp-settings-screen.tsx:28-39,213-385,491-519,530-550,552-781`):
   - Stdio / HTTP tabs
   - "Save to Config" → `PUT /api/mcp/servers`(writes `~/.hermes/config.yaml` server-side)
   - `POST /api/mcp/reload` live reload
9. **Audit Trail**:`/audit` cross-session SQLite event-store with phase/args/result expansion + date/type filters
10. **Templates 真读**:7 built-in crew templates + 4 conductor templates(Research / Build / Review / Deploy)+ Clone crew API。**templateType 字段**

**真读到的关键文件**:

hermes-agent:
- `agent/skill_bundles.py:24-120`(YAML schema + scan_bundles)
- `agent/memory_manager.py:1-120`(MemoryProvider ABC + fan-out)
- `agent/skill_manager_tool.py:1-80`(skill creation entry + `_guard_agent_created_enabled` + `HERMES_HOME=~/.hermes/skills`)
- `agent/models_dev.py:27,39,74,425`(`MODELS_DEV_URL` + `ModelInfo` + `fetch_models_dev` 4h TTL + ETag)
- `providers/__init__.py:77,93,136,377`(register_provider, _discover_providers 4-step)
- `providers/base.py:41`(ProviderProfile 8 hook methods)
- `mcp_serve.py:264,458,685-689,692`(EventBridge + _ToolHandlers + create_mcp_server)
- `tools/mcp_tool_discovery.py:443`(register_mcp_servers consumer entry)
- `agent/transports/hermes_tools_mcp_server.py:47,61`(EXPOSED_TOOLS for codex)
- `agent/anthropic_adapter.py:289,420,438,471,595,752`(build_anthropic_client + _auth_style + build_anthropic_kwargs)
- `hermes_state.py:164,442-447,633-676,742,875,938`(SessionDB mixin stack)
- `hermes_state_common.py:338-580,708-915`(SCHEMA v30 + FTS SQL)
- `hermes_state_messages.py:286,537,653,1005,1054,1156,1312`(append_message, resolve_resume_session_id, rewind_to_message)
- `hermes_state_search.py:1061,1167,983,793`(FTS5 routing + CJK + LIKE fallback)
- `agent/agent_init.py:1246,1278-1279`(_init_memory + char budgets 2200/1375)
- `agent/turn_context.py:412,649,977,1047`(_tick_memory_nudge + restore_or_build_system_prompt)
- `agent/turn_finalizer.py:449,647-657`(background review spawn on should_review_memory)
- `agent/background_review.py:339-356`(_MEMORY_ROUTING_BLOCK + _MEMORY_REVIEW_PROMPT)
- `plugins/memory/honcho/dialectic.py:23,51,56,65,93,114,125,146,154`(dialectic cadence + multi-pass)
- `gateway/platforms/base.py:1814,1886,1887-1889,1897,3920,4187,4201,4539`(BasePlatformAdapter ABC + delivery ledger)
- `gateway/platform_registry.py:41,104,389`(PlatformEntry + PlatformRegistry singleton)
- `gateway/session.py:65,472,664,687-688,763,766`(SessionSource + build_session_key + SessionStore)
- `gateway/delivery_ledger.py:33-36,41-50,59`(durable obligation ledger state machine)
- `cron/jobs.py:769,1750`(parse_schedule + create_job)
- `cron/scheduler.py:991,1985,2222,2283,2435,2519,3826`(_get_parallel_pool + run_job)
- `cron/scheduler_tick.py:7`(tick file lock)
- `cron/scheduler_delivery.py:531,823,106,1115,1161,1229,1388`(relay + bot-chat + transport resolver)
- `cron/suggestions.py:7-9,39,167`(consent-first self-triggered via add_suggestion)
- `kanban_db_dispatch.py:2720,2841,2868`(subagent spawn via subprocess.Popen)

Hermes-Studio:
- `src/server/knowledge-browser.ts:6-30,76-114,116-124,160-190,266-296,373-398`(WikiPageMeta + frontmatter + wikilink extraction + runForce 280 iter)
- `src/server/memory-browser.ts:5-175`(MEMORY.md / memory/*/memories/* browser)
- `src/server/crew-store.ts:17-159`(Crew + CrewMember types + .runtime/crews.json)
- `src/server/workflow-store.ts:1-99`(DAG persistence .runtime/workflows.json keyed by crewId)
- `src/server/event-store.ts`(SQLite event log for /audit)
- `src/server/template-store.ts:19-207`(7 crew + 4 conductor templates with templateType)
- `src/server/chat-event-bus.ts + send-run-tracker.ts + run-store.ts`(SSE event hub + dedup)
- `src/server/gateway-capabilities.ts:11,13-14,37,142,220`(HERMES_API env probe)
- `src/screens/crews/components/workflow-builder.tsx:46-69,506-603`(topoLayers + dispatchLayer + SSE run_end)
- `src/screens/jobs/jobs-screen.tsx:99-120,122-424,432-436,529-657`(statusForEvent + JobCard + refetchInterval 30s)
- `src/screens/memory/knowledge-browser-screen.tsx:182-246,248-518,680-699`(runForce + GraphCanvas)
- `src/screens/settings/mcp-settings-screen.tsx:28-39,213-385,491-519,530-550,552-781`(ServerDialog + Save to Config + Reload)
- `src/screens/chat/components/approval-card.tsx`(4-button ApprovalCard with 4 scopes + sessionStorage)
- `src/lib/approvals-store.ts:12-165`(sessionStorage-persisted ApprovalRequest)
- `src/screens/conductor/hooks/use-conductor-gateway.ts:1159-1267,676-730`(sendMission + worker tracking)
- `src/routes/api/hermes-jobs.ts:14-73`(thin proxy forward)
- `src/routes/api/crews/$crewId.dispatch.ts:64-96`(dispatch loop)
- `src/routes/api/crews/$crewId.clone.ts:51-90`(clone crew + mintSession per member)
- `src/routes/api/conductor-spawn.ts:99-110,113-121,134-163`(spawn orchestrator as cron job)
- `src/routes/api/approvals.$approvalId.approve.ts:36-101`(dual-strategy resolution)
- `src/routeTree.gen.ts:172-174,377-379,587-590`(auto-generated route table)
- `FEATURES-INVENTORY.md`(1.0.0 React 19 + TanStack Start + Vite 7 + Tailwind 4 + Zustand stack)

**弱点(警觉)**:
- Hermes "self-improving loop" = pluggable memory + on_pre_compress,**不是** true self-learning。lc `self_optimizer + PatternRecognizer` 已经超过它
- Hermes gateway platform registry 用 30+ mixin MRO stacking on single GatewayRunner class — **不是** explicit DI
- **Multi-platform sessions NOT unified per-user**:each platform creates distinct session_key(platform.value baked in)— Relay 只 frontend 看起来 unified,backend state 不 merge
- Hermes-Studio 是 thin reverse proxy + JSON store — **NO business logic,NO real-time state of its own**;单点 failure = HERMES_API gateway
- Hermes-Studio Cron UI claims Slack/Email delivery 但 actual DELIVERY_OPTIONS=[local, telegram, discord](UI/help copy 撒谎)
- Knowledge Graph no chokidar watcher — every API call re-scans filesystem O(files),node types only from YAML frontmatter `type` field(heading not parsed)
- Conductor V2 maxParallel 和 supervised 是 PROMPT HINTS,**不是** enforced by Studio
- Execution Approvals dual-strategy silently fallback to chat command if gateway endpoint fails — receipt 可能 not show in chat immediately
- Mixed built-in vs plugin platform adapters(Telegram/Discord/Slack plugins,BlueBubbles/WeChat/WhatsApp/Signal built-in)— 不一致
- Cron `fire_claim` at-least-once via fcntl **works only on Unix**(msvcrt fallback on Windows unverified)

**lc 当前优势(超过 Hermes)**:
- **LayeredMemory 5-layer + Ebbinghaus 5-dimension decay** — 比 Hermes 平铺 MEMORY.md/USER.md/SOUL.md **更复杂**
- **arch_ledger 完整治理** — Hermes 无对应
- **SeamRegistry 跨仓契约** — Hermes plugin-catalog 但无 seam dependency graph
- **self_optimizer + PatternRecognizer** — 已经超过 Hermes 的 background review fork

**对 lc 可借鉴的具体做法**(真读版):

| 借鉴点 | 做法 | lc 落地路径 | 难度 |
|--------|------|------------|------|
| **Frozen MEMORY.md/USER.md/SOUL.md snapshot at session start**(prefix-cache stability) | init 时冻结,mud-session writes 落盘但不更新 prompt | `core/layered_memory.py:541 build_context_injection` 加 `_frozen_snapshot` 属性;`record_experience` 仍写但不 invalidate snapshot mid-session | low |
| **MemoryProvider ABC + on_pre_compress hook**(honcho/mem0/hindsight/supermemory) | 灵犀(lcx MCP server)或灵安(lingan)plug in 真实外部 backends 不重写 lc core | `core/layered_memory.py:470 LayeredMemory` 加 Provider ABC;layer 4 Shared 变 pluggable provider surface | medium |
| **Knowledge Graph 1310 行单 React 文件**(force-directed 280 iter + 5 type colours + wikilink edges) | ~600 LOC TS/React 完整 reference impl | 新 `webui-server/memory_viewer/` 扫 `~/.lingclaude/memories/**/*.md` + frontmatter type + wikilink edges,force-directed SVG with hover-zoom | low |
| **Execution Approvals 4-scope dual-strategy** | 4 按钮(Once/Session/Always/Deny)+ sessionStorage + 双后端(native gateway → fallback chat command) | `webui_seam.py:32 permission endpoint` 加 scope 字段;加 ApprovalCard React 组件 | medium |
| **Cron consent-first self-triggered suggestions** | 后台 review 提议 recurring tasks,用户同意才调度 | `core/scheduler.py` + `core/data_flywheel.py` 加 pattern_detector;通过 arch_audit 后写到 governed suggestion store | medium |
| **FTS5 with 3 tokenizers + CJK routing + fail-open** | 灵研(lingresearch) cross-session corpus recall | `core/sqlite_store_base.py` + `ExperienceStore` in `layered_memory.py:236` 扩 SCHEMA messages_fts / messages_fts_trigram / messages_fts_cjk;route by `_contains_cjk()` | low |
| **Per-session specificity-weighted routing**(platform=16, thread=8, chat=4, user=2) | 灵信(lingmessage) 多通道 delivery routing | `core/scheduler.py` 扩 session_key construction with weighted specificity;参考 `gateway/session.py:664-688` | medium |
| **MCP tri-role + exact-SHA pin + removed.yaml blocklist** | 灵犀(lcx)增强:同时是 MCP consumer(灵网/灵扬 plugins)+ MCP server(暴露给 Claude Code)+ reverse-server(暴露给其他 harnesses) | `lingclaude/mcp/lcx/` + `data/arch_ledger/arch_audit_state/` 加 removed.yaml blocklist;MCP plugin SHAs pin 在 `arch_reference/` | high |
| **Delivery ledger**(durable obligation state machine pending→attempting→delivered/failed/abandoned, flood-aware) | 灵信(lingmessage) 跨 agent message delivery 持久化 + flood protection | `data/arch_ledger/work_claim/` + `work_claim_log/` 扩 delivery_obligations table | medium |
| **Session lineage via parent_session_id chain + soft-archive** | 灵犀 MCP server sessions 长期压缩,保留 /undo /retry / 跨 session resume | `core/layered_memory.py` + `sqlite_store_base.py` 加 sessions + messages tables with parent_session_id lineage;Layer 2 Experience decayed below threshold soft-archive | high |
| **Profile-scoped workspaces** `~/.hermes/profiles/<name>/` with active_profile symlink | 灵字辈 12 agent 隔离 sessions/skills/config(灵研 research corpus vs 灵创 sandbox 分离) | `.lingclaude/profiles/<name>/` 模式;`Hermes-Studio profiles-browser.ts:49-57` 参考 | medium |

**`borrowable_priority_order_p0_to_p3`**:
- **P0 low effort**:Frozen MEMORY.md snapshot(prefix-cache stability)+ FTS5 with 3 tokenizers + Knowledge graph ~600 LOC
- **P1 medium**:MemoryProvider ABC + on_pre_compress hook + Execution approvals 4-scope dual-strategy + Cron consent-first self-triggered + Per-session specificity-weighted routing
- **P2 high value high effort**:MCP tri-role with exact-SHA pin + Session lineage via parent_session_id chain + soft-archive

**Sources**:github.com/NousResearch/hermes-agent(真读,/tmp/hermes-read), github.com/JPeetz/Hermes-Studio(真读,/tmp/hermes-read), hermes-agent.nousresearch.com/docs/, github.com/NousResearch/hermes-agent/blob/main/LICENSE, **lc existing reference**: `data/arch_ledger/arch_reference/ref-hermes-webui.json`

---

### 1.6 codex-harness(openai/codex-harness)[C1: **真读发现仓库不存在**]

> **⚠️ 重要纠错(agent 真读后)**:`openai/codex-harness` 这个仓库 **不存在** — site:github.com 搜索零命中,OpenAI 官方 README 明确"external PRs not accepted"。所谓"Codex Harness"在 OpenAI 语境里 = 单仓内的 **框架层**(`codex-rs/core` + `codex-rs/protocol`),外部调用走 **JSON-RPC surface**(`codex-rs/app-server`)。
>
> 详见 §1.1,这里把 web search 综述的旧认识替换为真读结论。

**与 openai/codex 的真关系**:
- **同仓**,不是独立仓,不是 submodule,不是 superset
- version 维度上 `codex-harness` **没有独立 version** — 主仓 `CHANGELOG.md` 按 `codex-cli`/`codex-sdk` 发布,`core` crate 没有发版号,与主仓 commit 同频(本次 read 时 `openai/codex@main` HEAD 是 release v0.149.0 时代)
- 名次上,"harness"在 OpenAI blog 出现(`developers.openai.com/blog/codex-as-a-platform`)是 marketing 词;在仓库里它就是 `codex-rs/core`

**真读最小抽象集合**(详见 §1.1 真读版):
- **Submission / Op / Event / EventMsg** — SQ/EQ pattern 跨 crate contract
- **Session**(运行时机器)+ **Thread**(持久化外壳 + per-thread plugin toggle)+ **Turn**(执行单元)
- **`#![deny(clippy::print_stdout)]`** — 编译期锁死解耦
- **ToolCall 显式拆 7 分支**(shell/patch/MCP/elicitation/user-question/permission/dynamic-tool),**不是统一抽象**
- **Op 是 decoupled intent**(无 ack)+ **Event 是 decoupled observation**(id correlation)

**真读关键文件**:
- `/tmp/codex-probe/codex-rs/protocol/src/protocol.rs`(5000+ 行,Op + Submission + Event + EventMsg + 各种 items,SQ/EQ doc comment verbatim)
- `/tmp/codex-probe/codex-rs/core/src/lib.rs:1-80`(`#![deny(print)]` 规则)
- `/tmp/codex-probe/codex-rs/core/src/codex_thread.rs:1-100`(ThreadConfigSnapshot + ThreadSettingsOverrides)
- `/tmp/codex-probe/codex-rs/core/src/session/session.rs:1-80`(Session struct with state/setup separation)
- `/tmp/codex-probe/codex-rs/core/src/client.rs:1-120`(ModelClient scoped to session)
- `/tmp/codex-probe/codex-rs/core-plugins/src/lib.rs:1-60`(marketplace constants)
- `/tmp/codex-probe/codex-rs/app-server/src/lib.rs:1-100`(message_processor + transport)
- `/tmp/codex-probe/sdk/python/README.md:1-60`(自动生成 SDK)

**对 lc 可借鉴的具体做法**(整合 §1.1 + 真读):

| 借鉴点 | 做法 | lc 落地路径 | 难度 |
|--------|------|------------|------|
| **Op/Submission + Event/EventMsg SQ/EQ contract** | 抽 wire-level protocol, id correlation | 新 `lingclaude/protocol/`(抽 `Submission/Op/Event/EventMsg` 四个 core type + JSON Schema auto-emit) | medium |
| **CodexThread-as-single-facade** | Thread = Session + 持久化元数据 + per-thread plugin toggle | 新 `lingclaude/core/thread.py`(薄包装 conversation loop + SQ/EQ channel, 暴露 ThreadConfigSnapshot + ThreadSettingsOverrides) | medium |
| **"Minimal harness"哲学(真读版)** | **接口有且只有 Op→Event 两个方向**,所有复杂度消化在 `Op::TurnInput` 触发后的核心循环里 | 写 `docs/CORE_SURFACE_CONTRACT.md` 明确 core 不反向依赖 surface; 抽 Submission/Op/Event/EventMsg 四个 type 作为框架对外 contract | medium |
| **`#![deny(print)]` 等价** | ruff rule + pre-commit 禁 framework 层 print | `pyproject.toml` + `.pre-commit-config.yaml`(B006 / T201 with allowlist)+ CI 步 | low |
| **`Op::ThreadSettings { disabled_plugin_ids }`** | per-thread plugin toggle | `engine/plugin_runner.py` 加 `thread_overrides: dict[str, bool]` + `ThreadContext.__init__` | low |
| **Auto-emit JSON schema + TS types** | pydantic TypeAdapter.json_schema() + 自动 emit | `lingclaude/protocol/schemas.py` emit schemas/*.schema.json, webui/ 直接消费 | low |
| **app-server JSON-RPC 作为 surface boundary** | surfaces 不直接 import core | 新 `lingclaude/daemon/`(own protocol), webui/typescript/cli 通过 JSON-RPC | high |

**Sources**:openai/codex@main(`/tmp/codex-probe` clone to 1.4GB+), openai.com/index/unlocking-the-codex-harness, deepwiki.com/openai/codex/1.3-architecture-overview

---

### 1.7 Oh My Hermes(rlaope/oh-my-hermes,真仓库)[C1: **重要校正** — 原描述仓库 404]

> **⚠️ 重要校正(agent 真读后)**:用户描述的 `github.com/xcv58/oh-my-hermes` 在 GitHub **404(不存在)**。`oh-my-hermes` 在公开代码中**唯一真仓库**是 `rlaope/oh-my-hermes`(NousResearch Hermes Agent 的 OMH skill pack,**不是 Go P2P package mgr**)。原 §1.7 的 P2P 描述无对应公开实现,就地校正。

**真仓库**:`github.com/rlaope/oh-my-hermes`(license 未明,Hermes Agent 本身 MIT)
**语言**:Markdown / YAML(Hermes skill pack,**不是 Go**)

**架构**:
```
Hermes Agent(NousResearch)runtime 层
    ↓
OMH skill pack 层(skills/*, config.yaml plugin hooks)
    ↓
OMH backend CLI(给 wrappers / agents / automation / maintainers 用)
```

**核心特性**:
1. **Agent Install Protocol**(`/INSTALL_FOR_AGENTS.md`)— 定义 pasteable protocol,给 AI agent 用的 install 命令
2. **Hermes profile config.yaml** 选 OMH store(每 profile 自己一个,可显式共享)
3. **用户面只有 3 个命令**:`omh setup / omh update / omh doctor`
4. **backend CLI** 给 Hermes Agent + wrappers + coding agents + 自动化 + maintainers 用

**对 lc 可借鉴的具体做法**:

| 借鉴点 | 做法 | lc 落地路径 | 难度 |
|--------|------|------------|------|
| **不适用 / 校正项** | `xcv58/oh-my-hermes` 不存在;`rlaope/oh-my-hermes` 是 Hermes plugin 不是 P2P package mgr,**没有可借鉴的 P2P 协议** | 若坚持 P2P package mgr 思路,改参考 Go 社区真正存在的(athens / go-tuf / velvet 等) | n/a |

**Sources**:github.com/xcv58/oh-my-hermes → HTTP/2 **404(不存在)**, github.com/rlaope/oh-my-hermes(唯一), raw.githubusercontent.com/rlaope/oh-my-hermes/main/README.md

---

### 1.8 Omarchy(omacom/omarchy)[C1: **源码真读**(Bash 克隆到 /tmp/omarchy-read)]

**Repo**:github.com/omacom/omarchy(原 basecamp/omarchy 已 301,MIT)
**作者**:David Heinemeier Hansson(DHH, Ruby on Rails / 37signals)

**真读关键校正**:
- omarchy 是 **DHH 在 Omarchy 项目下的 Arch Linux distro**(完整 distro,非孤立仓库)
- **`omarchy-mise-install` 是 lazy stub 真出口**(不是抽象描述)
- **`omarchy-agent-crash` 是 systemd-coredump → agent 真实现**(52 行)
- **lazy stub 模板**实际 4 行 bash 写到 `~/.local/bin/<cmd>`(omarchy-mise-install:51-57)

**架构(真读)**:
```
bin/omarchy(1093 行主路由脚本,扫描 omarchy:* metadata, COMMAND_ROUTE / GROUP_DESCRIPTIONS 字典)
bin/omarchy-mise-install(59 行:rm → heredoc 写 ~/.local/bin/<cmd> → chmod)
bin/omarchy-agent(145 行:case-by-case 把 agent 名转 command[] + args,**路由 13 个 agent** 同一 interface)
bin/omarchy-theme-set(393 行,colors.toml → 9 应用切色 + post_theme_commands fan-out)
bin/omarchy-theme-set-pi(写 Pi 的 $HOME/.pi/agent/themes/omarchy-system.json,**主题→agent UI 同步**)
bin/omarchy-agent-crash(52 行,**systemd-coredump → agent 真转诊**)
bin/omarchy-install-preinstalls(调 omarchy-refresh-applications 重建 stubs)
themes/<name>/colors.toml(31 行 + 背景图 + icons.theme + vscode.json + neovim.lua + shell.lock.toml)
default/hypr/* + Quickshell(QML)桌面 shell
agents/skills/(诊断 + 接受测试 + 视觉验证等)—— **agents 是 source-of-truth 的协作者,不是用户**
```

**核心特性(真读版)**:
1. **Agents as first-class citizens 真读实现**:`omarchy-agent` 路由 **13 个 agent** 同一 interface:`claude / codex / opencode / agy / copilot / crush / grok / hermes / openclaw / muse / cursor-agent / pi / ori`
2. **Lazy stubs 真读**:mise-managed stub 模板 = `mise use -g + mise x + chmod`(`omarchy-mise-install` 写 4 行 bash 到 `~/.local/bin/<cmd>`);**stub 模板全 22 行** in `install/user/mise.sh` 包括 `codex / claude / crush / antigravity-cli agy / gh / copilot / opencode / playwright / pi / omp / grok / cursor-agent / ghui / hunk / hey / basecamp / cf / ori / hermes / muse`
3. **Theme 同步到 agent UI 真读**:`post_theme_commands` 数组 fan-out,parallel 调 `omarchy-theme-set-pi / -claude / -hermes / -t3code / -vscode` —— 任何 agent UI 都可加对应 `omarchy-theme-set-<agent>` 同步
4. **colors.toml 真读**:`themes/tokyo-night/colors.toml` 31 行,**9 基础色 + bright_* 变体 + background 4 阶**;`themes/<name>/` 单 schema → `default/themed/*.tpl` 模板覆盖
5. **systemd-coredump 故障转诊**:`omarchy-agent-crash` 抓 PID / comm / exe / signal / time,拼 prompt 给默认 agent + diagnose-crash skill
6. **400+ `omarchy-*` CLI 真读**:`bin/omarchy` 主路由 1093 行,扫 `omarchy:*` metadata,`COMMAND_ROUTE / GROUP_DESCRIPTIONS` 字典驱动

**弱点(警觉)**:
- 是 distro 不只是工具,迁移难度 medium-high(Quickshell + Hyprland 强绑定 Linux)
- **lazy stub 假设 mise 在 PATH**;若目标机没 mise,要先 bootstrap
- colors.toml 单源靠 template-renderer 覆盖各家(部分 app 没有 .tpl 需手写)

**对 lc 可借鉴的具体做法**(真读版):

| 借鉴点 | 做法 | lc 落地路径 | 难度 |
|--------|------|------------|------|
| **omarchy-mise-install 4 行 lazy stub 模板**(`mise use -g + mise x + chmod`) | sub-agent lazy 拉取,自动 cache 在 `~/.cache/lingclaude/mcpo/<member>/` | `plugins/agents/` 写 4 行 wrapper bash 模板,首次调用时 pip install + chmod | low |
| **omarchy-agent 路由 13 个 agent 同质接口** | case-by-case 转 command[] + args,**让 agent 当同质 CLI 调用** | `cli/commands.py` 抽 `AgentRouter` 类,路由 12 个灵族 + proj_* | low |
| **post_theme_commands fan-out** | theme-set 是 hook,任何 agent UI 可加对应 `<agent>-theme-set` | `~/.lingclaude/theme.toml` + `cli/display.py` + `repl.py` 消费 + fan-out hook 数组 | low |
| **systemd-coredump → agent 改写成 faulthandler + SIGUSR1 dump** | 抓 PID/comm/exe/signal/time 拼 prompt 喂默认 agent + skill | `core/scheduler.py` 加 crash handler,Python `faulthandler.dump_traceback_later()` + SIGUSR1 触发,自动喂 `self_optimizer/daemon.py` | medium |
| **colors.toml 31 行 schema** | 单一来源 → 9 应用切色 + agent UI 同步 | `~/.lingclaude/theme.toml`(类似 schema)+ `display.py` + `repl.py` + webui 共享 | low |

**Sources**:github.com/omacom/omarchy(clone /tmp/omarchy-read), distrowatch.org/omarchy, zhichai.net/en/topic/178634469, install/user/mise.sh(22 行 stub 模板), bin/omarchy-mise-install(4 行 lazy stub 模板), bin/omarchy-agent-crash(52 行真转诊)

---

### 1.9 Pi(earendil-works/pi,原 badlogic/pi-mono)[C1: **源码真读**(Bash 克隆到 /tmp/pi-read)]

> **⚠️ 重要校正(agent 真读后)**:
> - `github.com/badlogic/pi-mono` 已 **301 迁移**到 `github.com/earendil-works/pi`
> - `agent-loop.ts` 是 **857 行**(不是流传的 418 行)
> - 当前 monorepo **12 packages**(agent/ai/chord/client/coding-agent/durable/evals/protocol/server/session-backends/telemetry/tui),**没有** `pi-web-ui / pi-mom / pi-pods`
> - HookMap **11 键**(不是流传的 20+)

**Repo**:`github.com/earendil-works/pi`(原 `badlogic/pi-mono` 301 到此,MIT,~96k stars)
**作者**:Mario Zechner(libGDX 作者,2026-04 → Earendil / Lefos 收购)

**架构(monorepo,严格自下而上,12 packages)**:
```
packages/ai                LLM API + transform-messages 跨 provider 迁移(966+999 行)
packages/agent             agent loop(857 行)+ types(463)+ harness/(3548 行)= ~5370 行
packages/coding-agent      CLI + modes + tools + extensions(主文件 main.ts 985 行)
packages/coding-agent/src/modes  interactive / print / json / rpc 4 模式
packages/agent/src/harness/session/jsonl  append-only DAG(2.5K 行)
packages/chord             application-composition runtime(DSH 也用)
packages/durable           durable conversation + task + document runtime
packages/telemetry         vendor-neutral telemetry contracts
+ client / evals / protocol / server / session-backends / tui
```

**核心特性(真读版)**:
1. **857 行核心 agent loop**(`/tmp/pi-read/packages/agent/src/agent-loop.ts`;核心 `runLoop` ~120 行)
2. **4 默认工具** + `ls/find/grep`(in `packages/coding-agent/src/core/tools/`)
3. **4 种执行模式**:interactive / print(`-p`) / json / RPC(`modes/interactive + modes/json-event.ts + modes/print-mode.ts + modes/rpc`)
4. **11 个 HookMap 生命周期扩展点**(`/tmp/pi-read/packages/agent/src/harness/agent-harness.ts:430-500`,**HookMap 11 键**):
   - `before_run / before_drive / before_run_end / transform_context / before_request / before_payload / after_response / before_tool / after_tool / before_compaction / before_navigation`
   - `hooks.ts:89-125` 用 aggregate 路由,**gate via EffectGate**
5. **Append-only DAG JSONL session**(`/tmp/pi-read/packages/agent/src/harness/session/jsonl/storage.ts:145`):`fileSystem.appendFile` + `EntryBase{id,parentId,seq,timestamp,type}` + **commit.ts:insertEntry**。**Branch / Fork first-class**(`SessionBranchExistsError`, `session.ts:66-210`)
6. **跨 provider context migration 真读**(`/tmp/pi-read/packages/ai/src/api/transform-messages.ts:235 行`):
   - 算 `isSameModel(provider+api+modelId 都等)`
   - 同 model:thinking 块带 signature 保留(为回放),OpenAI 加密 reasoning 即使文本空也保留
   - 跨 model:拆 thoughtSignature、redacted thinking 丢弃、thinking text 降级为 text 块、toolCallId 按目标 model 规则 normalize(OpenAI Responses ID 450+ 字符 → Anthropic `^[a-zA-Z0-9_-]+$` max64)
   - **二遍扫**:orphaned toolCall(没配对的 toolResult)插 synthetic 'No result provided' 错误结果
7. **Mid-session model switch 真读**:`LaneConfiguration{model.provider, model.id, thinkingLevel, activeToolNames}` 持久化每个 lane 状态;`agent-loop.ts:188-197 prepareNextTurn` 真正换 model;当前 lane 后续 entry 继承新 model 配置,旧 entry 保留旧 model(回放按 entry 自己的 model 重放)
8. **Skills**(`SKILL.md`, agentskills.io XML schema)+ **Prompt Templates**(`/tmp/pi-read/packages/agent/src/harness/skills.ts` 396 行)
9. **无内置 permission system**;沙箱靠 Gondolin / Docker / OpenShell(`/tmp/pi-read/README.md:40-48`)
10. **extensions 模式**:no sub-agent / no MCP / no plan mode / no permission popup 默认,全 extensions

**真读到的关键文件**:
- `/tmp/pi-read/README.md`(全 116 行, 12 packages + permissions 不内置 + supply-chain hardening)
- `/tmp/pi-read/packages/agent/src/agent-loop.ts`(857 行;`runLoop` 162-279, `prepareNextTurn` model switch 184-198, `executeToolCalls` parallel/sequential 464-540)
- `/tmp/pi-read/packages/agent/src/harness/hooks.ts`(533 行;`HookRegistry + EffectGate + aggregate` 11 hooks)
- `/tmp/pi-read/packages/agent/src/harness/agent-harness.ts:430-500`(HookMap 11 键, 签名 strict)
- `/tmp/pi-read/packages/agent/src/harness/types.ts`(463 行;`Result<T,E> + Skill + PromptTemplate + AgentHarnessTool`)
- `/tmp/pi-read/packages/agent/src/harness/session/types.ts`(150 行;`EntryBase{id,parentId,seq,timestamp,type}` + `EntryType` union 4 元素 + `LaneConfiguration{model,thinking,activeTools}`)
- `/tmp/pi-read/packages/agent/src/harness/session/jsonl/storage.ts:145`(`fileSystem.appendFile` 真 append-only)
- `/tmp/pi-read/packages/agent/src/harness/session/jsonl/io.ts:118`(`serializeJsonlTransaction`)
- `/tmp/pi-read/packages/agent/src/harness/session/session.ts:164-360`(`Branch` class + `StorageBackedBranch` + `createBranch / appendMessage`)
- `/tmp/pi-read/packages/ai/src/api/transform-messages.ts`(235 行,**跨 provider migration 真实现**)
- `/tmp/pi-read/packages/ai/src/types.ts:999`(`LLM message shape`)
- `/tmp/pi-read/packages/agent/src/types.ts:151-352`(`ThinkingLevel + AgentLoopConfig.convertToLlm`)

**弱点(警觉)**:
- **stale claims**(418 行 / pi-web-ui / pi-mom / pi-pods / 20+ hooks)需要校正
- permission **不内置**, 沙箱留给用户(Gondolin/Docker/OpenShell 三选一)
- monorepo 包多,新人 onboarding 较重
- PII/secret 处理 + 沙箱 + 工具审批全靠 extensions,**默认信任栈较薄**

**对 lc 可借鉴的具体做法**(真读版):

| 借鉴点 | 做法 | lc 落地路径 | 难度 |
|--------|------|------------|------|
| **Append-only DAG session + LaneConfiguration mid-session model switch** | `EntryBase{id,parentId,seq,timestamp,type}` JSONL + 每 entry 带 `LaneConfiguration{model,thinking,activeTools}` | `core/layered_memory.py`(620 行)重构 Layer0-4 schema → Entry union | high |
| **transform-messages.ts 跨 provider thinking 迁移** | `isSameModel` + 同 model 留 signature,跨 model 拆 + toolCallId normalize + synthetic 空 toolResult 防 orphan | `core/model_call.py` 的 `ModelAdapter.migrate_thinking(messages, src, dst)` 挂 transform-messages 235 行移植 | medium |
| **HookMap 11 生命周期扩展点 + EffectGate admitted/closed** | `before_run / before_drive / before_run_end / transform_context / before_request / before_payload / after_response / before_tool / after_tool / before_compaction / before_navigation` | `core/hooks.py`(6 HookType 现有)扩到 11+,按铁律 8 调整不能任意改 system prompt / tool 集合 | medium |
| **Branch / Fork first-class**(StorageBackedBranch + SessionBranchExistsError) | `session.ts:66-210` 真实现 fork | `core/session.py` 加 `Branch` class,持久化到 JSONL | medium |
| **RPC mode**(JSON over stdin/stdout) | `modes/rpc` 已有 | 新加 `cli/rpc.py`(JSON 协议供 IDE / 测试嵌入) | medium |
| **极简哲学** | "我不需要的我不建",core 干净 use-case 缺口靠 extensions 填 | lc peer 自评"工具过多",审视每个 capability,**问"有真实 use case 吗"** | medium |

**Sources**:github.com/earendil-works/pi(clone /tmp/pi-read, depth=1), 原 github.com/badlogic/pi-mono 301, api.github.com/repos/earendil-works/pi, juejin.cn/post/7616266014872289343, torchtree.com/en/post/pi-coding-agent

---

### 1.10 DSH — DeepSeek Harness(/home/ai/deepseek-harness)[C1: **本地源码深度真读**(本地仓 + vendored Cordis)]

> **⚠️ 重要校正**:web search 综述的"4 Runtime Modes (Standard/Code PTC/Minimal/Creator)" **不存在**。
> 真实 runtime variants 是:**web (full UI) / headless (one-shot CLI) / JSON-RPC stdio / ACP (automation-only)**。
> "Code Mode" 是 `run_code` tool(把 tools collapse 成 Python/TS 程序调用),**不是 runtime mode**。

**Repo**:本地 /home/ai/deepseek-harness(deepseek-ai/deepseek-harness,MIT,~180k stars in 2 weeks)
**关键架构事实**:
- **Cordis kernel 完整 vendored** at `vendor/cordis/src/`(9 文件 ~3000 行,DSH patches Cordis when needed — 如 session fork semantics)
- ~50 packages 严格分组:spine / capabilities / host / extensions / bundles / apps
- Python SDK at `python/sdk + python/sdk-runtime`(drives harness via JSON-RPC stdio)

**架构(三层 + 八类插件)**:
```
Application Layer     web / headless CLI / JSON-RPC stdio / ACP / Python SDK
Core Subsystems      agent-loop / session / system-prompt / tools / scope / agent-default-model + llm / fs / shell / subprocess / terminal / lsp / web / skill / mcp / acp / sandbox / storage / compaction / subagent / schedule / jobs / plan / todo / settings / credentials / presets
Cordis Plugin Kernel Plugin / Service / Context(isolate) / Fiber(state machine) / Event / Effect
Bundles              base / headless / web-app
```

**核心特性(本地真读版)**:
1. **Everything is a Plugin**(连 model adapter + agent loop 都是 plugin,核心只暴露 Fiber state machine `PENDING→LOADING→ACTIVE/FAILED→UNLOADING→DISPOSED`)
2. **Append-only session log as SSoT 真读细化**:
   - `Session` class(`packages/core/session/src/index.ts:1-1158`)
   - `deriveMessages` fold(LLM history 是 derived,**never stored separately**)
   - **`replaceGeneration` 缓存**:projection invalidation O(new nodes)
   - **`SessionStore.fork()`** — fork is first-class
   - **`SESSION_FORMAT_VERSION = 0`** 单整数 pin;**incompatible logs rejected on load, no migration**("foundation over blast radius")
   - `SessionHeader` 含 cwd / parentSession / seedLength / origin / delegationDepth / agentPreset
   - **`Lossless JSON validation` at append site**:rejects BigInt / Date / sparse arrays / Maps before storage
3. **Per-session isolate realms**:`Context.is` symbol-keyed brand,a preset 服务 **不能 leak 到 global store**;`mountPreset` 调 `leakedServices()` audit 拦截
4. **Capability seams(Service Definition / Provider / Consumer 三元组)**:同一 shape,backends register onto `ctx` keys,consumer side 是 tool that calls `ctx.<service>`
5. **Profiles + Bundles + Patches 配置层叠**:
   - bundles(shipped)→ profile patch.yml → home patch.yml → --patch overlay
   - **patches replace whole row config**(last write wins per row id)
6. **Reversible effects**:`ctx.effect() / ctx.on()` **auto-unwrap on dispose** — HMR / config-reload 自动 unwind,无 manual teardown
7. **Typed events × 4**:`emit / parallel / serial / waterfall`(checked at code-gen against dispatcher call site,**internal/dispatch event 是 single best diagnostic primitive**)
8. **Tool pipeline 5 段**:`tools/pre-execute → tools/execute → tools/post-execute` waterfall + `tools/result` emit(对比 lc 5 段 `pre→guards→execute→post→finalize`)
9. **Platform sandbox runner chain**:`bwrap → landlock → seatbelt → windows-acl`,**fail-closed**(任意一个可用就启用)
10. **`node:vm` sandbox 显式 NOT a security boundary**(cordis-host-runner/README.md 明确)— **dynamic packages = bash-access trust stance**
11. **Dual-half dynamic packages**(cordis-host-runner + cordis-client-runner):agent can `define` 新 plugin at runtime,host half evaluated in `node:vm` with cordis-service traps,client half 送 browser,**run is a round trip**(cordis/request-run)— **canonical "agent rewrites its own runtime" recipe**
12. **Pre-release**:`THERE WILL BE COMPATIBILITY-BREAKING CHANGES`(README),SESSION_FORMAT_VERSION pinned at 0,无 migration step chain
13. **DeepSeekAdapter**(`packages/llm/llm-deepseek/src/adapter.ts:1-200`):fetch + SSE,`httpErrorCode` taxonomy,capabilities via `modelInfo`
14. **Per-session schedule**:Session-event-driven,per-Agent idle phase,**no timer when agent busy**(避免 busy-agent 重复触发)
15. **storage registry**:named, multiple side-by-side,JSON backend `atomic whole-file rewrite`(无 torn-write 风险)

**真读到的关键文件**:
- `vendor/cordis/src/{index,context,registry,fiber,events}.ts`(Cordis kernel)
- `packages/core/session/src/{index,types,surface}.ts`(append-only log 真读)
- `packages/core/agent-loop/src/agent.ts:1-497`(`ReactLoopAgent`:turn→step→assistant/chunk→assistant/message→tool/call→tool/result)
- `packages/core/scope/src/index.ts:1-205`(`createScope`, `scopeTarget` routing filter)
- `packages/llm/llm/src/index.ts:1-948`(`LlmRuntime`, `AdapterRegistration.replace()`, `llm/stream` waterfall, `prepareCall` binding)
- `packages/preset/agent-presets/src/mount.ts:1-381`(`mountPreset`, `leakedServices` audit, `inactiveRows`)
- `packages/sandbox/sandbox-local/src/index.ts:1-100`(platform runner chain)
- `packages/extensions/cordis-host-runner/src/{lifecycle,registry,sandbox}.ts`(dual-half dynamic packages)

**弱点(警觉)**:
- Pre-release, SESSION_FORMAT_VERSION=0,**no migration step chain**
- ~50 packages + ~1000-line docs each + dual README.zh mirrors — **onboarding tax heavy**
- node:vm sandbox **NOT a security boundary** — dynamic packages 是 bash-access trust stance
- `CompositeError` swallowing + weakmap-keyed registrations + hidden dual-half round-trip state — failures contained via per-listener try/catch **可隐藏 real bugs**
- Tests require real API key for e2e;**100% per-file coverage gate fragile**
- "4 Runtime Modes" framing 是错的 — 实际是 4 个 runtime variants(web / headless / JSON-RPC stdio / ACP)

**对 lc 可借鉴的具体做法**(真读版):

| 借鉴点 | 做法 | lc 落地路径 | 难度 |
|--------|------|------------|------|
| **Append-only session log as SSoT**(真读版) | `deriveMessages` fold + `replaceGeneration` 缓存 + `SessionStore.fork()` + `SESSION_FORMAT_VERSION` pin | `core/layered_memory.py` 转换:layered cache → event log + derive projection;governance/seams/plugins 对齐 projection slices | medium |
| **Capability seams 三元组**(Service Definition / Provider / Consumer) | 加 agent type = documented 3-step change | `plugins/agents/` 重构 work_claim + agent_dispatch 为 seam triples;引 `plugins/agents/<capability>/<role>/` 布局 | medium |
| **Reversible effects + plugin lifecycle**(ctx.effect / ctx.on auto-unwrap on dispose) | HMR / config-reload 自动 unwind | `core/scheduler.py` + `cli/repl.py` 引 `EffectTracker` 持有 plugin effects,config-change 时 unwind | medium |
| **Profiles + Bundles + Patches**(last write wins per row id) | lingxi/lingzhi/lingflow ship opinionated profiles 不 fork default | `data/arch_ledger/` 加 `profiles/` + `cordis.patch.yml` 类比物 | low |
| **Model-facing events `@mode` tags + dispatch mode per event** | 防止 silent-veto bugs | `lc-guard` MCP + `plugins/agents/agents` 注解 session/event, agent/inbox/* modes + registry sanity check | low |
| **Dual-half dynamic packages** | agent 运行时 mount 新 tool with proper teardown | `data/arch_ledger/work_claim/` 扩 lock contract 覆盖 dynamic plugin activation; sandbox-python for host half; reuse lc-guard MCP for approval surface | high |
| **`replaceGeneration` 缓存 + replace event** | compaction 写 replace 不 break downstream readers | `core/layered_memory.py` 加 `replaceRange(seqStart, seqEnd)` event + cached projection invalidation | medium |
| **Tool pipeline(5 段 waterfall + result emit)** | audit / timeout / redaction plugins 后插 | `core/model_call.py` 拆 tools/pre / tools/execute / tools/post waterfalls | medium |
| **SESSION_FORMAT_VERSION pin + "foundation over blast radius"** | 防 schema-drift tax | `data/arch_ledger/` 每个子目录加 `SCHEMA_VERSION` 常量,refuse cross-version reads | low |
| **Per-session isolate realms + `leakedServices()` audit** | preset 服务不能 leak global store | `plugins/agents/work_claim.py` 加 `isolate` 标记,leases per-session not process-global | high |

**`answer_to_layered_memory_question`**:
- **判断**:"worth borrowing but shape 不同"。DSH append-only log 是 conversation surface,**model history 是它的 fold**。lc 当前 layered_memory 是 cache(project memory + journal + arch_ledger 是 derived products),**不是 log**。
- **DSH 模式** = layered_memory 变 log;project_memory/journal/agents_md 变 derived projections(各自 fold);governance rules 变 projection-time validators,**不 storage-time**。**这是比换层更大的 refactor**。
- **第一步**:在 `layered_memory.py` 引 single `lc_session.append(type, data, ...)` sink,每个现存 source(agent_md / journal_entry / work_claim_checkout / work_claim_release)对应一个 event type。然后让现有 readers fold log,**不读自己的文件**。完成后 surface replace(compaction)是 single primitive。

**Sources**:本地 /home/ai/deepseek-harness(完整真读), genztech.blog/p/deepseek-open-sources-dsh-agent-harness-mit, martianlee.github.io/posts/2026-09-05-deepseek-harness-architecture, github.com/cordiverse/cordis(vendored framework), github.com/cordiverse/paper(programming paradigm paper)

---

## 二、八项目横向对比矩阵

| 维度 | Codex | Hermes WebUI | Orca | Penguin | Hermes Agent | codex-harness | Oh My Hermes | Omarchy | Pi | DSH | **lc 当前** |
|------|-------|-------------|------|---------|-------------|---------------|-------------|---------|----|-----|------------|
| **核心范式** | surface-agnostic core | 多 agent gateway | worktree ADE | RSI 自进化 | self-improving loop | minimal harness | P2P pkg mgr | OS-level agent | 极简 418 行 | everything is plugin | A2T3 records+events+transition |
| **主语言** | Rust | TS | TS+Electron | TS | TS | Rust | Go | Bash+Lua | TS | TS | **Python** |
| **Surface 多样** | 5+(CLI/Web/VS Code/App/JetBrains) | Web dashboard | IDE/CLI/Web/Mobile | CLI/Web/desktop | Web/MCP/CLI | 框架层(由上层 surface 化) | CLI | OS shell | CLI/RPC/JSON | Web/CLI/SDK/ACP | **CLI/WebUI/MCP** |
| **多 agent 调度** | 通用 + subagent | 多 agent gateway | worktree pool | Optimizer→Evaluator | crews | 通用 | n/a | n/a | extensions | subagent | **12 灵族 + 8 proj** |
| **跨 session memory** | SQLite rollout | SQLite + KG graph | session 文件 | trace 透明 | SQLite + embedding | framework 层 | n/a | n/a | append-only DAG | append-only SSoT | **5 层 + Ebbinghaus(无 embedding)** |
| **跨 provider 适配** | OpenAI protocol | OpenRouter+200+ | 25+ agents | 1000+ models | 200+ models | framework 层 | n/a | 10 lazy stubs | 15+ providers / 300+ models | OpenAI compat | **多 provider,已在 waterfall** |
| **沙箱隔离** | 三 OS 强隔离 | web 端 dashboard | worktree | trace-only | 端到端批准 | framework 层 | n/a | OS 级(Linux) | 无(默认) | sandbox plugin | **in-process,弱** |
| **会话 fork/resume** | ✅ Thread fork/resume | dashboard UI | UI | ✅ snapshot+rollback | ✅ cron + recall | framework 层 | n/a | n/a | ✅ DAG parent_id | ✅ append-only 派生 | **session rewind 部分** |
| **MCP** | first-class(MCP server + 调度) | server + UI | plugin | first-class | first-class | framework 层 | n/a | n/a | extension | first-class | **server + proxy** |
| **IDE 集成** | VS Code/JetBrains/Xcode | web | 设计模式 | 无 | 无 | framework 层 | 无 | shell-level | RPC | ACP 协议 | **0 个 IDE 插件** |
| **配置层叠** | TOML | YAML | TS | YAML | YAML | framework 层 | n/a | toml | JSON | **profiles+bundles+patches** | **YAML manifest + policy_loader** |
| **行数(核心 agent loop)** | codex-rs/core 多 crate | dashboard 多组件 | 多模块 | Optimizer+Evaluator 多文件 | agent+studio 多模块 | ~minimal | n/a | 多 shell | **418 行** | 多 packages | **~200 行主干 + 多 mixin** |
| **Stars/Popularity** | 71k | 多 repo 分散 | 53k | 2.2k | 96k | 同 codex | 小 | 30k+ | 96k | 180k | n/a(自研) |
| **License** | Apache-2.0 | 多 MIT | MIT | Apache-2.0 | MIT | Apache-2.0 | MIT | MIT | MIT | MIT | **MIT** |

---

## 三、lc 可借鉴的具体架构(分层清单)

> 按灵元铁律"薄主干 / 分形插片"组织,每条都说**借鉴谁 + 借鉴什么 + 落到 lc 哪个文件 + 难度 + 优先级**

### 3.1 主干层(铁律 1 域:变化全走接缝)

| # | 借鉴源 | 借鉴什么 | 落地 lc 文件 | 难度 | 优先级 |
|---|--------|----------|-------------|------|--------|
| M1 | DSH | **Append-only session log as SSoT**(LLM history 是 derived) | `core/session.py:SessionManager` | medium | P0 |
| M2 | Pi | **append-only DAG session**(JSONL + parent_id) | `core/session.py:Session` | high | P1 |
| M3 | Pi + codex-harness | **Op 抽象 / 统一事件矩阵** | `core/hooks.py` 扩到 20+ HookType | medium | P1 |
| M4 | DSH | **Profiles + Bundles + Patches 配置层叠** | `core/wiring.py` + `core/policy_loader.py` | medium | P1 |
| M5 | DSH | **4 Runtime Modes**(Standard/Minimal/Code/Creator) | `cli/repl.py` 加 `--mode` flag | medium | P2 |
| M6 | Pi | **RPC mode**(JSON over stdin/stdout) | 新加 `cli/rpc.py` | medium | P1 |
| M7 | Codex | **surface-agnostic core 边界契约**(core 不反向依赖 surface) | 写文档:`docs/CORE_SURFACE_CONTRACT.md` | low | P0 |
| M8 | codex-harness | **Session/Thread/Turn 三层抽象** | `core/session.py` 加 Turn | medium | P2 |

### 3.2 调度层(铁律 8:work claim / 跨域协同)

| # | 借鉴源 | 借鉴什么 | 落地 lc 文件 | 难度 | 优先级 |
|---|--------|----------|-------------|------|--------|
| S1 | Orca | **CLI orchestration 层**(send/check/reply/inbox + task gate) | `coordination/` 加 `task_cli.py` | medium | P1 |
| S2 | Orca | **Decision gate**(任务级,默认开) | `governance/governance_v2.py` 加 GateHook | medium | P2 |
| S3 | Hermes Studio | **Execution approvals 流**(三态:approve/reject/ask) | `core/permissions.py` | medium | P0 |
| S4 | Penguin | **Multi-agent Optimizer/Evaluator RSI** | `self_optimizer/daemon.py` 加 agent_versioning | high | P2 |
| S5 | Codex | **worktree 隔离**(每 proj_* 加 git worktree 包装) | `plugins/agents/proj_*/` | high | P3 |
| S6 | Hermes Agent | **Cron scheduler**(agent 自触发) | `self_optimizer/scheduler.py`(新) | medium | P2 |
| S7 | Codex | **Agent hibernation**(idle 杀进程) | `engine/background.py` 加 LRU eviction | low | P2 |

### 3.3 工具层(铁律 1 + 沙箱)

| # | 借鉴源 | 借鉴什么 | 落地 lc 文件 | 难度 | 优先级 |
|---|--------|----------|-------------|------|--------|
| T1 | Codex | **WASM policy engine**(执行策略可声明) | `lacp/sandbox_policy.py` 升级 DSL | high | P2 |
| T2 | Codex | **三 OS 沙箱**(Linux Landlock / macOS Seatland) | `engine/sandbox_provider.py` | high | **P0**(peer 自评短板) |
| T3 | Codex | **Apply Patch**(unified-diff 跨 surface) | `engine/apply_patch.py`(新) | medium | P1 |
| T4 | Codex | **compile-time tool schema**(Pydantic v2) | `engine/tool_registration.py` | low | P1 |
| T5 | DSH | **MCP + ACP 双协议** | `engine/mcp_proxy.py` + 新加 `engine/acp_proxy.py` | high | P2 |
| T6 | Pi | **跨 provider context migration**(thinking 转换) | `core/model_adapter.py` | medium | P2 |
| T7 | DSH | **Code(PTC)模式**(模型写代码组合多 tool) | 新加 `engine/ptc_runtime.py` | very high | P3 |

### 3.4 记忆层(铁律 1:layered_memory + Ebbinghaus)

| # | 借鉴源 | 借鉴什么 | 落地 lc 文件 | 难度 | 优先级 |
|---|--------|----------|-------------|------|--------|
| Me1 | Hermes Agent | **跨 session 语义 memory**(SQLite + embedding) | `core/layered_memory.py` 加 `memory_embedding.py` | medium | P1(peer 自评 P1) |
| Me2 | Hermes Studio | **knowledge graph 可视化** | webui 加 graph view | medium | P2 |
| Me3 | Pi | **append-only DAG**(JSONL + parent_id) | `core/session.py` | high | P2 |
| Me4 | Hermes Agent | **session-start recall**(最近 N 个相关 experience 注入) | `core/query_engine.py` 初始化阶段 | medium | P1 |
| Me5 | Penguin | **Full trace 透明**(每个 tool call 审计) | `engine/tool_pipeline.py` 5 段每段 emit trace | low | P1 |

### 3.5 Surface 层(铁律 1:多 surface 共享 core)

| # | 借鉴源 | 借鉴什么 | 落地 lc 文件 | 难度 | 优先级 |
|---|--------|----------|-------------|------|--------|
| Su1 | Codex | **App Server JSON-RPC**(IDE/Web 集成稳定 API) | 新加 `mcp/app_server.py` 或独立 `app_server/` | high | P1 |
| Su2 | Codex | **stdio / WebSocket / Unix socket** 三 transport | 新加 transport 层 | high | P2 |
| Su3 | Pi | **4 种模式(interactive/print/json/RPC)** | `cli/repl.py` 加 `print / json / rpc` 模式 | medium | P1 |
| Su4 | Omarchy | **Theme 统一**(单 toml 跨 webUI/CLI) | `~/.lingclaude/theme.toml` | low | P2 |
| Su5 | Omarchy | **系统脚本 API 化**(lingoptimize/lingaudit/lingclaim/lingsuggest) | `cli/` + shell wrapper | low | P2 |
| Su6 | Omarchy | **故障转诊**(崩溃 → 自动喂自优化 daemon) | `core/hooks.py` ON_ERROR 钩子 + daemon trigger | medium | P2 |
| Su7 | Omarchy | **Lazy stubs**(首次用才下载) | `plugins/agents/` mcpo lazy cache | low | P2 |
| Su8 | Hermes Studio | **Cron job manager UI** | webui 加 cron UI | medium | P2 |

### 3.6 协议层(LACP + MCP + ACP)

| # | 借鉴源 | 借鉴什么 | 落地 lc 文件 | 难度 | 优先级 |
|---|--------|----------|-------------|------|--------|
| P1 | Codex | **App Server JSON-RPC 协议** | `lacp/app_server_protocol.md`(新) | medium | P1 |
| P2 | DSH | **ACP(Agent Client Protocol)适配** | `engine/acp_proxy.py`(新) | high | P2 |
| P3 | Hermes Studio | **MCP server management UI** | webui MCP tab | medium | P2 |
| P4 | Codex | **ToolSpec compile-time schema** | `engine/tool_registration.py` | low | P1 |

---

## 四、lc 优化方向的最终汇总(优先级 + 实施序列)

### 4.1 P0(必须做,影响可用性)

> **lc peer 自评 + 8 项目对比共同指向的硬缺口**

1. **沙箱进程级隔离**(T2,借鉴 Codex)
   - 当前 `engine/sandbox_provider.py` 是 in-process,**peer 自评沙盒⭐⭐**,明确短板
   - **实施**:Linux Landlock(轻量、不需 root)+ macOS Seatland(`sandbox-exec`),默认走 bwrap fallback
   - **预期**:peer 自评沙盒⭐⭐ → ⭐⭐⭐⭐

2. **Execution approvals 流**(S3,借鉴 Hermes Studio)
   - 当前高危操作只能"拦截报错",**无 pause-approve-resume**
   - **实施**:`permissions.py` 三态(approve/reject/ask),ask 走 webui `permission` endpoint,会话用 `TaskManager.pending` 栈挂起
   - **预期**:peer 自评操作审批⭐⭐ → ⭐⭐⭐⭐

3. **Append-only session log as SSoT**(M1,借鉴 DSH)
   - 当前 `SessionManager._state_store` 是旁路,`Session.messages` 仍是真源
   - **实施**:把"session events"作为 single source of truth,`Session.messages` 改为 derived
   - **预期**:可复现性、可审计性、fork/resume 一致性大幅提升

4. **surface-agnostic core 边界契约**(M7,借鉴 Codex)
   - 当前 `app.py` HTTP 路由 / `cli/repl.py` REPL / `webui_seam.py` 各有薄薄一层,boundary 不清晰
   - **实施**:写 `docs/CORE_SURFACE_CONTRACT.md`,明确 core 不反向依赖 surface;3 surface 注入同一 `QueryEngine`(bus_responder 已踩过坑)
   - **预期**:peer 自评 IDE⭐ → ⭐⭐(基础)

### 4.2 P1(应该做,影响竞争力)

5. **append-only DAG session**(M2,借鉴 Pi)
6. **Op 抽象 / 统一事件矩阵**(M3,借鉴 Pi + codex-harness)
7. **Profiles + Bundles + Patches 配置层叠**(M4,借鉴 DSH)
8. **RPC mode**(M6,借鉴 Pi)— 为 IDE 集成铺路
9. **CLI orchestration 层**(S1,借鉴 Orca)— task gate + inbox + dispatch
10. **Apply Patch 工具**(T3,借鉴 Codex)— unified-diff 跨 surface 编辑
11. **compile-time tool schema**(T4,借鉴 Codex)— Pydantic v2
12. **App Server JSON-RPC**(Su1,借鉴 Codex)— IDE/Web 集成稳定 API
13. **跨 session 语义 memory**(Me1,借鉴 Hermes Agent)— 加 embedding
14. **session-start recall**(Me4,借鉴 Hermes Agent)— 最近 N 个相关 experience 注入
15. **Full trace 透明**(Me5,借鉴 Penguin)— 5 段管线每段 emit
16. **4 种执行模式**(Su3,借鉴 Pi)— interactive/print/json/RPC

### 4.3 P2(可做,锦上添花)

17. **4 Runtime Modes**(M5,借鉴 DSH)— Standard/Minimal/Code/Creator
18. **Decision gate**(S2,借鉴 Orca)
19. **Multi-agent RSI**(S4,借鉴 Penguin)— agent_versioning
20. **Cron scheduler**(S6,借鉴 Hermes Agent)
21. **Agent hibernation**(S7,借鉴 Codex)
22. **WASM policy engine**(T1,借鉴 Codex)
23. **ACP 协议适配**(T5 + P2,借鉴 DSH)
24. **跨 provider context migration**(T6,借鉴 Pi)— thinking 转换
25. **knowledge graph 可视化**(Me2,借鉴 Hermes Studio)
26. **Session/Thread/Turn 三层抽象**(M8,借鉴 codex-harness)
27. **Theme 统一**(Su4,借鉴 Omarchy)
28. **系统脚本 API 化**(Su5,借鉴 Omarchy)
29. **故障转诊**(Su6,借鉴 Omarchy)
30. **Lazy stubs**(Su7,借鉴 Omarchy)
31. **Cron job manager UI**(Su8,借鉴 Hermes Studio)
32. **MCP server management UI**(P3,借鉴 Hermes Studio)
33. **App Server JSON-RPC 协议文档**(P1,借鉴 Codex)
34. **ToolSpec compile-time schema 协议**(P4,借鉴 Codex)

### 4.4 P3(战略储备)

35. **worktree 隔离**(S5,借鉴 Orca)— 每 proj_* 加 git worktree
36. **Code(PTC)模式**(T7,借鉴 DSH)— 模型写代码组合多 tool
37. **"Agents build agents"**(借鉴 Penguin)— 一句话生成 agent
38. **P2P plugin 仓库**(借鉴 Oh My Hermes)
39. **多通道接入**(借鉴 Hermes Studio)— Telegram/Discord/Slack

---

## 五、对 lc 当前架构的反思(基于 8 项目对比)

### 5.1 lc 已领先的项目(对比中 = 第一梯队)

| 维度 | lc 已实现的 | 同类项目对应 |
|------|------------|------------|
| **Plugin manifest 协议**(LACP 0.5.0) | transports×6, replaceable×4, deps×3, output_recipient, HMAC-SHA256 | DSH 没显式 manifest,Cordis 隐式;Codex ToolSpec 是 enum 不是 schema |
| **trace schema** | v0.4.0 phase×4, outcome×6(含 intuitive/unverified), human_context, cost, caller_chain | Codex trace 简单;DSH 的 session events 更强但未提 human_context |
| **治理:异议制** | Evidence-Based Objection(severity×3)+ blast_analysis + cognitive_assessment | 8 项目无一个走异议制,都是 voting 或 decision gate |
| **记忆分层 + Ebbinghaus 衰减** | 5 层 × 5 维衰减 | Hermes 是平铺 + embedding,无分层衰减 |
| **Pluggable 10 类 + Plug Level L1/L2/L3** | process registry + 缺席查语义 | DSH 有 reversible registration 但无明确等级 |
| **薄主干 + 分形插片哲学** | 灵元铁律 §一/§七 | DSH Cordis 类似但**lc 更严**(铁律 1 显式声明) |
| **跨仓路径单源** | `cross_repo_seam.py` | 8 项目无跨仓问题 |
| **2T3A record 原语** | create/transition/query 三动作 | 8 项目无 record-first 哲学 |

### 5.2 lc 已落后 / 缺失的项目(对比中 = 必须补)

| 短板 | 谁做得好 | lc 当前的差距 |
|------|---------|-------------|
| **进程级沙箱** | Codex(三 OS)+ Omarchy(OS 级) | in-process 黑名单 |
| **Execution approvals** | Hermes Studio | 三态权限 |
| **多 agent fork/resume DAG** | Pi(JSONL+parent_id)+ Codex(Thread fork) | 单链 session |
| **跨 session 语义 recall** | Hermes Agent(embedding) | 纯关键词 |
| **App Server 协议(IDE 集成)** | Codex(JSON-RPC)+ DSH(ACP)+ Pi(RPC) | 0 个 |
| **Full trace 透明** | Penguin | 仅 self_optimizer 触发时 |
| **Agent hibernation** | Orca | 线程池无 idle 杀 |
| **故障转诊** | Omarchy(systemd-coredump → agent) | 仅 journal |
| **apply_patch(unified-diff)** | Codex | AST + 字符串两套 |
| **ToolSpec compile-time schema** | Codex | runtime validator |

### 5.3 lc 的"独家特性"(8 项目均无)

| 独家特性 | 描述 |
|--------|------|
| **灵元铁律 + 哲学治理** | 异议制 + blast analysis + cognitive assessment,论文"机制胜于内省" |
| **LACP human_context** | trace 含人类意图/turn/confidence 5 维 |
| **LayeredMemory + Ebbinghaus** | 5 层 × 5 维衰减(time/repetition/meaning/association/emotion) |
| **Plug Level L1/L2/L3** | 拔插等级显式声明 + 缺席降级范式 |
| **铁律 8 work claim** | 跨仓并发修改的故障域隔离 |
| **2T3A records 原语** | create/transition/query 三动作封闭 |
| **双签 SignedProvider** | CapabilitySeam 的安全模型 |
| **薄主干铁律** | 主干零概念词汇 |

---

## 六、建议的实施路径(0-6-12-18-24 月路线图)

### Month 0-3(P0 急治)
- T2 沙箱(Linux Landlock + macOS Seatland)
- S3 Execution approvals 三态
- M1 Session SSoT 改造
- M7 写 CORE_SURFACE_CONTRACT.md

### Month 3-6(P1 主线)
- M2 append-only DAG session
- M3 Hook 矩阵扩到 20+
- M4 Profiles + Bundles + Patches
- M6 RPC mode(为 IDE 集成铺路)
- S1 CLI orchestration(task gate + inbox + dispatch)
- T3 Apply Patch 工具
- T4 compile-time tool schema(Pydantic v2)
- Su1 App Server JSON-RPC
- Me1/Me4 跨 session 语义 memory
- Me5 Full trace 透明
- Su3 4 种执行模式

### Month 6-12(P2 锦上添花)
- 17-34 项按依赖顺序展开
- 重点:**M5 4 Runtime Modes**(Standard/Minimal/Code/Creator)— 给 benchmark + creator 场景
- **S6 Cron scheduler**(灵族"自驱动"路径)
- **Su5 系统脚本 API 化**(lingoptimize/lingaudit 等)— 让其他 agent 跨进程调用 lc

### Month 12-24(P3 战略)
- S5 worktree 隔离(灵族子成员并发)
- T7 Code(PTC)模式(模型写代码组合多 tool)
- "Agents build agents"(一句话生成 agent)
- 多通道接入(Telegram/Discord/Slack)

---

## 七、结语:灵元哲学在 8 项目语境下的位置

> **本次对比的一个意外发现:8 项目中没有一个走"record-first + 封闭算子"哲学**。

| 哲学 | 代表项目 | lc 的位置 |
|------|---------|----------|
| **Surface-agnostic core** | Codex | ✅ 已有(多 mixin + wiring manifest) |
| **Everything is a Plugin** | DSH(Cordis) | ✅ 已有(灵元铁律 §一) |
| **Minimal core** | Pi(418 行) | ✅ 已有(query_engine 主干 ~200 行) |
| **Self-improving loop** | Hermes Agent + Penguin | ✅ 已有(self_optimizer + PatternRecognizer) |
| **Multi-agent gateway** | Hermes WebUI + Orca | ✅ 已有(coordination + 12 灵族) |
| **Worktree-first isolation** | Orca | ⚠️ 缺(proj_* 仍在 in-process) |
| **Apply patch / code-edit DSL** | Codex + Pi | ⚠️ 部分(ast_edit + file_edit 两套) |
| **Record-first + 封闭算子** | **无** | ✅✅ **独家**(灵元 1.0 哲学) |

**结论**:
- lc 在 **哲学深度 + 治理机制 + 协议完整性** 上是**第一梯队**;
- 在 **Surface 多样性 + 沙箱强度 + 生态集成** 上是**第二梯队**;
- **最该补的 4 件事**:进程沙箱、Execution approvals、Surface IDE 集成、跨 session 语义 memory。

---

*文档生成日期:2026-09-20*
*生成者:灵克监督会话*
*数据来源:* 8 项目 web search + lc 本地真读 + DSH 本地真读 + lc 自评文档
*未做:每个 agent 的细节 key_files_actually_read 行号 —— 待 8 个精读 agent 回报后补强(详见 docs/peer-borrow/AGENT_REPORT_BACKFILL.md 占位)*