# Coding Agent Harness 横向比较与改进方向建议

- 日期: 2026-09-23（作者: AtomCode / agnes-3.0-flash）
- 基线参考: `lingclaude/docs/research/20260921_coding_agent_expansion.md`（10 家全景 + 扩展 5 家）
- 核实来源: `/tmp/harness_read/` 快照、`/home/ai/atomcode-src`、`/home/ai/lingclaude` 本体（均为源码实证，标注位置见各节）
- 说明: 与 09-21 相比，本次对多处断言做了源码复核，**发现 2 处事实已变化、1 处需修正**（见 §3），后续方向按最新事实重排。

## 1. 全景速览（15 家）

**十家 harness（承自 09-20 精读，核心机制不变）**：DSH（服务 dispose/reload + 模型自省）、Pi（无状态纯函数循环 + JSONL 树会话）、Orca（worktree 扇出 + 信箱状态机）、PenguinHarness（GOAL 两值协议 + 重连梯子）、oh-my-hermes（证据边界 + repair card 三态）、codex-host（保真投影 + CLI 字节透明）、agent-harness（配置单源 + lock provenance）、hermes-webui（零构建 WebUI + SSH 隧道）、hermes-studio（凭据池 LRU 轮转）、omarchy（agent 懒加载存根 + usage 契约化）。十家细节见 09-20/09-21 文档，本文不再展开。

**扩展五家关键机制（本次源码复核）**：

| 机制 | 实证位置 | 判定 |
|------|---------|------|
| cc 缓存前缀纪律（system prompt 内 dynamic-boundary 行） | 引擎闭源，仅有 CHANGELOG/示例材料 | 未验证（源码不可读） |
| cc PreToolUse hook exit-2 阻断回灌 | 同上 | 未验证 |
| codex rollout JSONL + mpsc 异步 + forked_from_id | `codex-rs/rollout/src/recorder.rs:85-141`（tokio mpsc 后台写者；Create 参数含 forked_from_id） | ✅ 已验证 |
| codex 审批 = SandboxMode × AskForApproval 正交 | `codex-rs/protocol/src/config_types.rs:104`（ReadOnly/WorkspaceWrite/DangerFullAccess）+ `protocol.rs:986`（UnlessTrusted/OnRequest/Granular/Never） | ✅ 已验证 |
| codex exec-policy Starlark 规则热更 | `codex-rs/execpolicy/src/parser.rs:66` + `amend.rs:65`（`blocking_append_allow_prefix_rule` 写回） | ✅ 已验证 |
| opencode Effect 编排 + headless `run --print` | `packages/cli/src/index.ts`（@effect import）+ `packages/opencode/src/cli/cmd/run.ts:126`（`run [message..]` 支持 `--format json` 非交互流式） | ✅ 已验证 |
| opencode 会话存储「SQLite 索引 + 文件正文」 | 实查仅 `storage.ts` 文件 JSON 存储，/share 走 `share-next.ts` 远程 API | ⚠️ 修正：SQLite 索引说法**未验证**（可能版本差异） |
| crush LSP 原生导航 | `internal/lsp/manager.go`（多 LSP 客户端懒加载）+ `agent/tools/lsp_definition.go` | ✅ 已验证 |
| crush 运行时 /model 切换保留会话 | `agent/coordinator.go:1387` `UpdateAgentModel` 重建模型配置、保留历史 | ✅ 已验证 |
| crush 扩展面 = MCP + slash command（无 skills/mods） | 内置命令在 `shell/builtins_registry.go`，skills 仅内置 crush-config | ✅ 已验证 |
| atomcode cache_epoch 前缀缓存 | `crates/atomcode-kernel/src/message.rs:900`（SessionSnapshot.cache_epoch，compaction 成功 +1） | ✅ 已验证 |
| atomcode 纯函数循环 ~120 行 | `crates/atomcode-kernel/src/agent.rs` `run_turn`（循环体含 10+ 计数器，**非**极简 120 行） | ⚠️ 修正：「~120 行纯函数」说法**未验证**，实读 run_turn 含大量重试/熔断预算逻辑，核心循环体 >120 行（agent.rs 共 4710 行） |
| atomcode turn_start hook + checkpoint | `hook.rs:203` turn_start 可改 conversation；`config.rs:112` round_cap_checkpoint | ✅ 已验证 |

## 2. lc（lingclaude）现状盘点（2026-09-23 源码实证）

已落地的能力（相对 09-21 文档的增量）：

- **headless 已存在**：`cli/app.py:216-223` 支持 `--print`/`--json`（`_headless_turn`，repl_turn.py:186），无装饰 stdout；`--continue`/`--resume <id>` 按项目过滤；`app-server` 子命令（app.py:407）已存在。
- **cache_epoch 语义对齐 atomcode**：`l5_audit.py:342,378`、`query_engine.py:300`、`wiring.py:348`（L1/L2 裁剪、reset 单调 +1，「对齐 atomcode compaction 的 cache_epoch 语义」原注释）。
- **cached_tokens 可观测**：`model_call.py`/`usage`/`repl.py` 全链路累计 + datalog 埋点（`d3e51d1` commit）。
- **审批资产化（部分）**：`permissions.py:205,348,372` 已有 `always_allow` 写回 approvals.json + 跨进程 mtime 热加载——比 09-21 文档的「每次审批一次性」现状**更靠前**，但仍是工具名粒度，未做 codex 式**命令前缀**沉淀。
- **provider 切换保留会话**：`cli/app.py:148-170` `--model`/`--provider` 切换默认模型不改会话历史——09-21 弱点 #5（未验证保留）现已**验证保留**。
- **会话恢复**：`submission.py:365` `resume_interrupted` 从 checkpoint 恢复 + journal 工具签名幂等 + R5 副作用待确认清单（承 09-21 §3.1 反证）。
- **WIRING_MANIFEST 单源装配**：`query_engine.py:154`「55 项装配收敛至 manifest，新增协作者 = manifest 加一行，本文件 diff 为 0」。
- **LSP 已有雏形**：`engine/lsp_registry.py` + `tool_handlers/lsp_tools.py`（LspToolsMixin）——09-21 弱点 #8「LSP 缺位」**已部分落地**（registry + 注入工具存在，与 crush 的深度对比未验证：lc 侧 LSP 工具是否真跑起 gopls/tsserver 未实测）。
- **飞轮/自优化**：`self_optimizer/`（daemon、ExperimentLedger、learner）09-21 后大量新增 commit（`da08bfc`/`cdec8d9`/`9b5ed34` 等），十家/五家均无等价机制。
- **Shift+Tab 四态权限环 + 热加载**（`513c505`）。

## 3. 事实修正（相对 09-21 文档，影响排期）

| 09-21 断言 | 本次复核 | 影响 |
|-----------|---------|------|
| 弱点 #6「无 headless 批量模式，CI 要自己包」 | `--print/--json/_headless_turn/app-server` 均已存在 | 该弱点**已部分消除**，剩余差距在「一行命令出结果的极简协议 + JSON-RPC 面成熟度」，非 0 到 1 |
| 弱点 #5「切 provider 会话历史语义未验证」 | `switch_model` 不动 `_conversation`，**验证保留** | 弱点消除 |
| 弱点 #4「批准后无资产化，同命令反复弹窗」 | `always_allow` 工具级沉淀 + 热加载已存在，但**前缀级规则沉淀缺失** | 差距缩小但仍在（codex `prefix_rule` 写回是命令粒度资产） |
| atomcode「~120 行纯函数核心循环」 | 实读 `run_turn` 计数器密集，非 120 行极简 | 「循环纯度差 25 倍」的量级表述需按此修正（lc 循环体 + mixin 58 槽位 vs atomcode 单文件 4710 行但核心 run_turn 仍偏重） |

## 4. lc 优化方向（按优先级，基于最新事实重排）

### P0（一周内，直接可开工）

1. **codex 式命令前缀审批资产化**（承接现有 always_allow，补最后一环）：把 `prefix_rule`/`network_rule` 等价物做进 `permissions.py`——批准后写回命令前缀规则 + 热更，治「同前缀命令反复弹窗」。理由：现有工具级沉淀已就位，边际成本最小、收益直接；codex 实证在 `amend.rs`，模式成熟。
2. **rollout JSONL 不可变会话 + fork 语义**（09-21 P0#1，维持）：`_save_checkpoint`（submission.py:318）之上加事件级 JSONL + ordinal + forked_from_id，fork/revert 写新文件不删旧。理由：09-21 §3.1 已证不改循环体；崩溃可恢复已有基础，缺的是「回退 = 新开分叉」语义。
3. **cc 式缓存边界行锁死**（09-21 P0#3，维持）：system prompt 内嵌 dynamic-boundary，前缀字节不变做 CI 断言，防止后续 cache_epoch 收益被新插片破坏。理由：cache_epoch 已对齐 atomcode，边界行是把收益「锁死不退化」的最后一环。

### P1（两周内）

4. **headless/JSON-RPC 面收口**（降自 P0——已非 0 到 1）：`--print` 已可用，补齐 opencode 式「一行命令出结果」的极简协议 + `app-server` JSON-RPC 面成熟度（错误语义、事件流、CI 友好 exit code）。理由：CI/批处理是 B 路线（Agent 编队 OS）让「其他 harness 成族员」的接入面。
5. **大工具结果瘦身**（codex 式，维持 09-21 P1）：工具结果 > 阈值只存摘要 + 引用，rollout JSONL 只写引用防膨胀。与 P0#2 同层落地更顺。
6. **worktree 扇出 + session-owned Bash**（Orca/DSH，维持）。

### P2（一个月内，治理加固）

7. 凭据池模型（hermes-studio）——GLM 1310 周期限额根治（维持）。
8. lc_plugins_inspect 自省插片（DSH，维持）。
9. 证据边界协议化（OMH H17 升格）、manifest lock provenance（agent-harness）、GOAL 两值协议（Penguin）（维持）。
10. **LSP 深化**（crush 式）：registry/tools 雏形已在，差距在「agent 主动调 LSP 做符号导航」的默认策略（而非默认走 grep）。降自 09-21 P2#12 的「LSP 工具面」为「LSP 默认化 + 实测收益」。

### 战略（维持 09-21 判断）

B 路线（Agent 编队操作系统）不变：lc 独有的编队 + 治理 + 飞轮自优化（`self_optimizer`，十家/五家全无）是护城河；C 方式落地（治理层可插拔先证价值）。短期关键动作 = P0 三项 + P1#4 接入面，让「其他 harness 能挂载 lc 治理层」变得具体可行。

## 5. 风险提示

- cc 引擎闭源，「缓存前缀纪律」「exit-2 回灌」等机制仅有外部材料，落地时建议先做自己的边界行实验，不要照抄未知契约。
- crush LSP 深度（是否真在 agent 决策里高优先级调用）与 lc LSP 雏形的实测差距**未验证**，P2#10 启动前需一次 A/B（同仓库同任务，grep-only vs LSP 引导的 token 消耗对比）。
- atomcode「纯函数 ~120 行」口径修正后，「循环纯度」不再是 25 倍差距，但 lc 的 58 wiring 槽位 + 5 mixin 结构仍是十家/五家中最重的主干，接口化（09-21 第 0 步）仍建议先于 TUI 双代热更（P2 热更唯一真依赖）。
