# 各 coding agent 横向比较（10 家 harness 全景 + 扩展 5 家：cc/codex/opencode/crush/atomcode）

- 日期: 2026-09-21
- 作者: 灵克 (lingclaude)
- 源码快照: `/tmp/harness_read/`（claude-code / codex / opencode / crush / 十家 harness）+ `/home/ai/atomcode-src`
- 前置阅读: 同日 `docs/research/20260920_peer_harness_borrowing.md`（十项目精读）

## 0. 十家 harness 全景回顾（承自 20260920 精读）

| # | 项目 | 本质 | 对 lc 最有借鉴价值的机制 |
|---|------|------|--------------------------|
| 1 | **DSH** (deepseek-ai) | Everything-is-a-Plugin（Cordis 运行时） | 服务生命周期强一致（dispose/reload）、三角色能力模式、模型自省工具族 |
| 2 | **Pi** (badlogic/pi-mono) | 极简分层 agent monorepo | 无状态纯函数循环、工具变更 diff 声明制、JSONL 树会话、Pico durable |
| 3 | **Orca** (stablyai) | 并行 agent 舰队 ADE | git worktree 隔离 + SQLite 事实源 + 消息信箱状态机；桌面=事实源，手机=方向盘 |
| 4 | **PenguinHarness** | 自进化 agent 构建器 | GOAL.yaml 两值协议（complete/blocked）、错误分层+重连梯子、自进化三底线 |
| 5 | **oh-my-hermes** | 操作层 | 证据边界纪律（runtime_observation/v1）、executor-readiness 三态+repair card、模型混合路由 |
| 6 | **codex-host** | 多 harness 投影进 Codex Desktop | 保真投影（不走公约数）、CLI shim 字节透明、宿主增强不动官方壳 |
| 7 | **agent-harness** | 多 provider 配置单源化 | .harness/ 单源生成 AGENTS.md/CLAUDE.md + lock provenance + 漂移 CI |
| 8 | **hermes-webui** | 零构建 WebUI | 进程内 agent 直读 HERMES_HOME、SSH 隧道独占、离线 cron |
| 9 | **hermes-studio** | BFF 控制面 | credential_pool 多账号 LRU 轮转、统一事件管线、多后端文件桥 |
| 10 | **omarchy** (basecamp) | Arch 桌面环境 | agent 懒加载存根（mise）、usage 收集器契约化、事件→prompt→skill 的 crash 诊断 |

**十家给 lc 的 P0/P1 建议汇总**（详见 20260920 文档 §11）：凭据池模型（studio）、服务 dispose/reload（DSH）、repair card 三态（OMH）、lc_plugins_inspect 自省（DSH）、worktree 扇出（Orca）、JSONL 树会话（Pi）、配额主动轮询（omarchy）、GOAL 两值协议（Penguin）等 13 项。

## 1. 扩展五家关键机制速览（源码实证，本日新增）

### cc（anthropics/claude-code）
- 引擎本体是闭源二进制，可用材料 = README/CHANGELOG/examples/mods。
- **缓存前缀纪律**：system prompt 内 `__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__` 行，上方全局缓存、下方动态。任何"恢复后内容微变"（memory 年龄注记、SessionStart 输出、附件重发、MCP tool 重渲染）都当 cache-miss bug 修。
- **权限分层**：企业 settings > project > user；工具级 `permissions.ask/deny`；PreToolUse hook exit-2 = 阻断并把 stderr 喂回模型；沙箱只约束 Bash（网络 allowlist）。
- **子代理编排**：YAML frontmatter agent + `context: fork` skill；官方范例是"同型 N 个 agent 并行出方案再 confidence 过滤"。
- **扩展四层**：plugins / skills（progressive disclosure）/ hooks / **mods**（引擎级 TS 中间件，`($,e,next)` 挂事件，`engine.create` fold 注入新 noun，契约=单一 .d.ts）。

### codex（openai/codex，Rust）
- **rollout JSONL 事件日志**：`rollout/src/recorder.rs` 后台 mpsc 异步写 + flush 语义；一行一事件含 timestamp/ordinal。fork/revert 不删原文件，写新文件并记 `forked_from_id`+截断 ordinal——**不可变性把"回退"变成"新开分叉"**。
- **审批 = 沙箱 × 策略正交矩阵**：SandboxMode{ReadOnly, WorkspaceWrite, DangerFull} × AskForApproval{untrusted, on-failure, granular, never}；granular 开关关 = 自动拒绝而非弹窗。
- **exec-policy 规则引擎**：Starlark `prefix_rule(allow|prompt|forbidden)` + 批准后写回 `default.rules` 热更——**一次性人工批准沉淀为可复用资产**。
- **MCP 大结果不落盘**：rollout 只存引用/摘要，客户端按需重建，防持久化膨胀。

### opencode（sst/opencode，TS）
- **Effect 函数式核心**：session/provider/command 全用 Effect 编排，TUI/Web/SDK 共享同一核。
- **server/client 分离**：HTTP API headless 模式，`opencode run --print` 一行命令即出结果——CI/批处理友好。
- **provider 多源适配**：models.dev 清单 + 本地 provider 定义，切换 provider 不改 agent 配置。
- **session 存储**：SQLite 索引 + 文件系统正文；`/share` 一条命令生成可读链接（把会话变可分享资产）。
- **权限**：按 provider/tool 的 permission map，TUI 层弹窗审批。

### crush（charmbracelet/crush，Go）
- **LSP 原生集成**：agent 侧直接查 LSP 做代码导航，不靠 grep 全仓猜——对"小仓库 + 强 LSP 工具链"场景极省 token。
- **多 provider 切换**：运行时 `/model` 切换 provider+model，会话历史保留；权限按 agent 角色配置 allowed tools。
- **bubbletea TUI**：终端 UI 与核心完全解耦（同 charmbracelet 系的"UI 是可替换壳"哲学）。
- **极简扩展面**：MCP + slash command，无 skills/mods 层——单 agent 单场景定位。

### atomcode（/home/ai/atomcode-src，Rust，已精读）
- **cache_epoch 前缀缓存**：compaction 的 stub 提交进历史且单调，每轮最多尾部破缓存一次；Hook 只许 tail-append。
- **纯函数 agent 循环**：核心 ~120 行，状态外置；`declareToolChanges` diff 声明制。
- **turn_start 快照**（本 lc 会话借鉴落地 b99921c）：崩溃可恢复。

## 2. 与 lingclaude 横向比较

> **2026-09-21 修订**（本文档首版 13:30 定稿，以下记录其后 4h 内工作树新增的未提交改动，
> §2/§3 相关断言已按此更新；commit 归属以 `git log` 为准）：
> - **cached_tokens 全链路透传**：`_accumulate_usage` 返回三元组（含累计缓存）、`UsageSummary.cached_tokens` 累计字段、`_finalize_turn` 多参、turn 摘要行 `cache 99%` 上 toolbar——缓存命中从「provider 层可观测」升级为「引擎/CLI 层可观测」。
> - **权限模式上 toolbar**（atomcode 借鉴）：`get_permission_mode()` 实时进状态栏。
> - **上下文真实口径修复**：优先用上一轮真实 input_tokens（含 system+history+tools 实际 prefill），修「一直 1%」。
> - **quota_governance 停层声明**（铁律 2 细则 5）：内核/接缝/实现三停层 + usage_api 扩展位。

### lc 相对十家 harness 的优势（承自 20260920 精读）

| 优势 | 对照 |
|------|------|
| **编队 + 治理**：多成员通信、任务回执账本、异步会议制、审计台账 | 十家中只有 Orca 有编排层，但没有跨进程成员身份与审计；lc 的「族」概念独有 |
| **治理密度**（铁律/守卫/台账三层） | DSH 工程化最深但无组织治理；OMH 有证据边界但无台账体系 |
| **缓存工程学**（本轮补齐，6f34d5e） | 达到 atomcode 同级——lc 此前的短板已补齐 |
| **配额治理**（本轮新增，cbff31f + 1cb8208） | 十家全没有「解析重置时刻 → 自动冷却到点」的机制 |

### lc 相对扩展五家（cc/codex/opencode/crush/atomcode）的优势

| 优势 | 五家对照 |
|------|---------|
| **编队 + 治理** | 五家全是单 agent 单体（codex 有 subagent fork 但无跨进程成员身份）；只有 lc 把"agent 社会"做成了产品 |
| **配额窗口治理** | 五家遇到配额耗尽要么报错要么退避，没有"解析重置时刻 → 自动冷却到点"的机制 |
| **缓存命中可观测**（76-80% 命中实测） | cc 有缓存纪律但无命中观测；codex/opencode/crush 未展示等价能力 |
| **插片 + trust 分级**（25 插片 manifest） | 五家扩展面都更"扁平"：cc mods 要 .d.ts 契约、codex MCP 按 server、opencode 插件按 Effect 模块、crush 只有 MCP/slash——lc 的 trust/plug 分级是独有的治理维度 |
| **薄主干纪律 + 铁律守卫** | 五家源码里最接近的是 codex 的 Starlark 规则沉淀，但 lc 的"声明-验证闭环 + 返审触发器"体系化程度更高 |

### lc 相对扩展五家的弱点（按差距排序）

| # | 弱点 | 领先者 | 差距 |
|---|------|--------|------|
| 1 | **agent 循环重主干**（5 文件 mixin ~3000 行） | atomcode/Pi（~120 行纯函数） | 循环纯度差 25 倍；状态散在 58 wiring 槽位 |
| 2 | **会话不可变 + fork 语义缺失** | codex（rollout JSONL + forked_from_id）、Pi（parentId 树） | lc resume 是整段列表回放，无"回退=新开分叉" |
| 3 | **审批未矩阵化** | codex（沙箱×策略 6×4 矩阵 + granular 关开关=auto-reject）、cc（三层 settings + exit-2 回灌） | lc approvals.json 是 ask/auto 二值，缺"沙箱能力 × 打扰策略"正交与规则沉淀 |
| 4 | **批准后无资产化** | codex（allow 前缀写回 default.rules 热更） | lc 每次审批是一次性的，同命令反复弹窗 |
| 5 | **provider 切换不保留会话** | crush（/model 运行时切 provider 保留历史） | lc 切 provider 走路由+熔断，会话历史语义未验证保留 |
| 6 | **无 headless 批量模式** | opencode（`--print` 一行出结果）、codex（app-server JSON-RPC） | lc 只有 TUI/REPL，CI 批处理要自己包 |
| 7 | **大工具结果未瘦身** | codex（MCP 大结果不写盘） | lc 工具结果全量进历史，长会话 token 膨胀 |
| 8 | **LSP 缺位** | crush（agent 直查 LSP） | lc 靠 grep/读文件猜，小仓库强 LSP 场景费 token |

## 3. 下一步优化方向（全 15 家视角修订版）

> **2026-09-21 晚 修订（参考 deepseek 评估重排）**：原 P0 三项（不可变会话/审批矩阵/缓存边界行）
> 直接照做，会把循环纯化（原 P1#4）压在它们之下——但 2026-09-21 源码核实（§3.1）证明
> **依赖关系被高估了一半**：不可变会话/headless 不改循环体即可落地，真依赖循环纯化的只有
> hot_swap/fork。因此本版把「循环纯化」拆成**第 0 步（前置，小切口）**而非「所有事情的前置」，
> 并在第 1 步补上「resume 已可从快照重建」的实测反证。

### 3.1 源码核实结论（2026-09-21，`_call_model`/`stream_call_model`/`resume_interrupted` 实读）

| 依赖命题 | 实测 | 判定 |
|----------|------|------|
| 「循环持有状态、无法从快照重建」 | 循环体是 for 轮次 + 局部栈变量（`used_tools/total_*/loop_detector`），**本身可重入**；外置状态是循环体引用的 20/19 个 `self._*` 槽位 | 部分成立 |
| 「resume 无法从快照重建」 | `resume_interrupted`（submission.py:345）**已实现**从 checkpoint 恢复 messages/round_idx/total_*/journal 工具签名，且 R5 阶段2 有副作用待确认清单 | **实测不存在此缺陷** |
| 不可变会话依赖循环纯化 | rollout JSONL + fork 只需改 `_save_checkpoint` 的持久化层（submission.py + 新增录制器），**循环体不动** | 不依赖，高估 |
| headless 依赖循环纯化 | `QueryEngine.__init__` 已收敛到 `assemble(WIRING_MANIFEST)`（P2.b），裸构造 + assemble 即可无 TUI 跑循环；headless 障碍是**缺 CLI 入口协议**，非循环纠缠 | 依赖被高估 |
| hot_swap/fork 依赖循环纯化 | `plugin_lifecycle` 蓝绿目前只切插片实例，**引擎循环本身不可热切**；3000 行 mixin 热切实高风险 | **真依赖** |
| 循环体对 `self._*` 的引用是否阻塞纯化 | 引用多为**可插拔治理钩子**（journal fail-soft / track_behavior / 路由 slot）——接口化即可，非架构级阻塞 | 可接口化解除 |

### 3.2 重排后的优先序

**第 0 步（前置，小切口——不是「大重构」，是「把循环体对治理钩子的引用接口化」）**
- 循环体对 `self._*` 的 20 槽位引用中，把治理钩子（journal/track_behavior/路由 slot/hallucination_correction）抽成**可注入的 seam 接口**（默认实现保持现状，行为零变化），纯函数核心 = `for round_idx: provider.complete → tool_call_executor → check_stop`。
- 验收：循环体可被「注入不同 seam 实现」驱动（测试注入 fake 钩子即可全离线回归）；TUI/headless 共享同一循环实例。
- 改动面：`model_call.py` 循环体 + 新增 1 个 seam 接口文件，**不动** `_save_checkpoint`/`resume_interrupted`（它们已能重建，见 §3.1 反证）。

**P0（一周内，第 0 步之后的本体，原三项不变 + 顺序微调）**
1. **codex 式不可变会话**：rollout JSONL（一行一事件 + ordinal）+ forked_from_id，fork/revert 写新文件不删旧——**改持久化层，循环体不动**（§3.1 已证不依赖纯化）。
2. **codex 式审批矩阵**：sandbox_mode × approval_policy 正交 + granular 关=auto-reject + 批准后 allow 前缀写回规则热更（资产化）。
3. **cc 式缓存边界行**：system prompt 内嵌 dynamic-boundary 行，动态内容全压其后；resume/compact 前缀字节不变当 CI 断言——把 `6f34d5e` 的前缀缓存优化锁死不退化。

**P1（两周内，原 P1#4 已升为第 0 步，余下顺延）**
4. **opencode 式 headless 模式**：`lc run --print` 一行出结果 + JSON-RPC app-server 面（§3.1 已证缺的是 CLI 入口协议，非循环纠缠）。
5. **codex 式大结果瘦身**：工具结果 >阈值只存摘要+引用（治长会话 token 膨胀）。
6. **worktree 扇出**（Orca）+ **session-owned Bash**（DSH）。
7. **凭据池模型**（hermes-studio）：credential_pool 多账号 LRU 轮转——GLM 1310 周期限额的根治。
8. **lc_plugins_inspect 自省插片**（DSH）：会话内查 25 插片/熔断 slot/路由健康。

**P2（一个月内，治理加固）**
9. 证据边界协议化（OMH）：H17 升格为协议层。
10. manifest 单源 + lock provenance（agent-harness）。
11. GOAL 两值协议（Penguin）。
12. crush 式 LSP 工具面。
13. TUI 双代热更（Pi chord）——**此项真依赖第 0 步的循环纯化**，故保留在 P2 并显式标注前置。

**一句话（修订）**：循环纯化**是 hot_swap/fork 的真前置**，但**不是不可变会话/headless 的前置**（§3.1 实测反证）。因此本版把循环纯化做成「小切口接口化」（第 0 步，不阻塞 P0 三项），P0 三项照原序推进，TUI 双代热更（唯一真依赖纯化的 P2 项）保留在 P2。战略终局判断（B 路线：Agent 编队操作系统）不变。

### 3.3 战略路线（参考 deepseek 评估，维持原判断）

| 路线 | 核心赌注 | 终局 | 风险 |
| :--- | :--- | :--- | :--- |
| A. 更好的 coding agent | 单 Agent 体验追 cc/codex | 「又一个 harness」 | 红海 |
| B. Agent 编队操作系统 | 把「族」做成平台，让其他 harness 成族员 | 「Agent 社会的 Linux」 | 需开放接口 |
| C. 治理中间件 | 铁律/台账/配额治理做成可插拔层挂任何 harness | 「Agent 治理的 OMH」 | 依赖宿主扩展面 |

**判断维持 B，用 C 的方式落地**：短期把治理层做成可独立挂载（先证价值），中期用 headless + 循环纯化让 lc 引擎可被其他编排器调用，长期让「族」协议成为编队事实标准。生态位 = 「比谁能让多个 Agent 像团队一样工作」。
