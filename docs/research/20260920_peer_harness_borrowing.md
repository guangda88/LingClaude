# 同侪 Harness 源码精读与 lc 可借鉴架构报告

- 日期: 2026-09-20
- 作者: 灵克 (lingclaude)
- 性质: 源码级调研（10 个开源 harness/编排项目本地克隆精读）
- 目的: 对照 lingclaude（单主体 + 插件容器架构），识别可借鉴架构，输出优化路线图
- 源码快照: `/tmp/harness_read/`（omarchy 源码在 basecamp/omarchy）

## 0. 调研对象与仓库定位

| 项目 | 仓库 | 定位 |
|------|------|------|
| DSH | deepseek-ai/deepseek-harness | Everything-is-a-Plugin（Cordis 运行时，21万+ star） |
| Pi | badlogic/pi-mono | 极简分层 agent（agent/ai/chord/protocol/durable 12 包 monorepo） |
| Orca | stablyai/orca | 并行 agent 舰队 ADE（worktree 隔离 + 编排状态机 + 移动端） |
| PenguinHarness | Prism-Shadow/penguin-harness | 自进化 agent 构建器（GOAL 协议 + 错误恢复 + 严格单调接受） |
| oh-my-hermes | rlaope/oh-my-hermes | 操作层（证据边界纪律 + 模型混合路由） |
| codex-host | BytePioneer-AI/codex-host | 多 harness 投影进 Codex Desktop（保真投影 + shim 字节透明） |
| agent-harness | madebywild/agent-harness | 多 provider 配置单源化（.harness/ → 生成 AGENTS.md/CLAUDE.md） |
| hermes-webui | nesquena/hermes-webui | 零构建 WebUI（进程内 agent + SSH 隧道独占） |
| hermes-studio | EKKOLearnAI/hermes-studio | BFF 控制面（credential pool + 多后端文件桥） |
| omarchy | basecamp/omarchy | Arch 桌面（agent 懒加载存根 + usage 收集器契约化） |

> DSH 与 Pi 是 lc 插片架构的两个官方同门参照系：DSH 教「单 harness 内部组合层」（服务生命周期/三角色/自省），
> Pi 教「最小核心 + 可嵌入协议层」（无状态循环/JSONL 树会话/纯函数循环）。

## 1. DSH（deepseek-ai/deepseek-harness）— 同构参照系

**特点**：Everything-is-a-Plugin，跑在 Cordis 运行时上。

- **服务生命周期强一致**（`vendor/cordis/src/fiber.ts` L625-639）：required service 消失 → 依赖插件自动 dispose，回归 → 自动重载；`PENDING` 是合法常态（依赖未就绪等待中）。事件驱动而非 lc 的周期探活。
- **三角色能力模式**：Definition（`dsh-shell` 纯契约）/ Provider（`dsh-bash-local` 实现）/ Consumer（`dsh-tool-bash` 工具暴露）三包分离，「seam = 服务名 + 契约类，任何单包都不是 seam」。
- **会话级持久 Bash**（`dsh-terminal` owner-scoped PTY）：cwd/env/shell 函数跨调用保持。
- **模型自省工具族**（`cordis_inspect/_define/_run/_stop/_undefine`）：模型自己可查插件树状态，还能在受限沙箱里定义一次性插件。
- **配置 fail-loud**：重复注册/未匹配 patch 目标启动期即炸。

**lc 可借鉴**：
1. 插片加载从「import 时一次性」升级为**事件驱动 dispose/reload**（消解灵知/灵通问道探活暴露的「依赖已死而消费方还在」）。
   > 2026-09-21 追记：已落地 `plugin_lifecycle.py`（1cb8208，六态状态机 + 依赖指纹 + disposer 逆序 + 蓝绿 hot_swap）。
2. 能力插片（cap_infer/cap_browser）按三角色拆「契约/实现/工具暴露」，换 provider 不动消费方。
3. BashExecutor 引入 session-owned 持久 shell（多轮工具调用的 cwd/export 不丢）。
4. 新增只读 `lc_plugins_inspect` 自省插片（25 插片状态/熔断 slot/路由健康），会话内自查替代 wake 时外部探活。
   > 2026-09-21 追记：repair_card 的 fail-closed 语义（RESOLVED 必须带验证证据）已就位，但 lc_plugins_inspect 自省插片未动工。

## 2. Pi（badlogic/pi-mono）— 最干净的分层范本

**特点**：
- **agent 循环是无状态纯函数**（`packages/agent/src/agent-loop.ts` L162-279，核心仅 ~120 行），状态封装在外部类；可换引擎（本地/远程/durable）不动循环。
- **工具增删/系统提示变更走 system message diff 声明**（`declareToolChanges`），历史不可变，重放任意序列即可还原——插件注册/注销天然可审计可回放。
- **steering（turn 后注入）+ follow-up（停止后注入）双队列**：用户插话与排队任务互不踩线。
- **JSONL 树会话**：每行 `{id, parentId}` 独立可解析，崩溃重放即恢复，`/tree`/`/fork` 回溯一等能力。
- **Pico durable 规范**（`packages/durable/docs/pico-v5.md`）：原子 commit + 任务 checkpoint + orphan 标记，「插件崩溃 ≠ 系统崩溃」。
- **faux provider 放公共包**（`packages/ai/src/providers/faux.ts`）：确定性测试替身，全链路回归不用 mock。
- **协议层**（4 字节长度 + CBOR 帧 + 三段路由）让宿主只实现 ByteTransport 即可嵌入。

**lc 可借鉴**：
1. engine 循环纯化 + 状态外置（治 TUI 崩溃丢上下文）。
2. 插片工具变更改 diff 声明制，进审计账。
3. JSONL 树会话 + checkpoint/orphan 语义，替换现有消息级 resume。
   > 2026-09-21 追记：崩溃恢复半边已落地（b99921c turn_start round=-1 checkpoint + `/resync` 全量重绘原语），
   > 但完整 JSONL 树会话（parentId 分叉 + fork/orphan 语义）未做。
4. faux provider 模式做灵元栈 CI（确定性回归，对照裁判代理 404 假数据事故）。

## 3. Orca（stablyai/orca）— 编队运营层

**特点**：
- **worktree 是一等域对象**（`src/main/git/` 60+ 文件）：线系（parent/child）、基线漂移检测、合并前基准快照一致性（`coordinator.ts:262-301` 同 tick 共享 base）。
- **编排 = SQLite 事实源 + 消息信箱 + 状态机**（`src/main/runtime/orchestration/` 454+ 文件）：phase 分解→派发→监控→合并，消息类型含 decision_gate/escalation/heartbeat，「只在人必须决策时打断你」。
- **桌面是事实源，手机是方向盘**：移动端只读+转向，不建第二事实源。
- 任意 CLI agent 扇出到独立 worktree，结果 diff 择优合并。

**lc 可借鉴**：
1. agent_batch 的 scratch 隔离升级为 **git worktree 隔离**（全族审计每成员一 worktree，产出可 diff 可回滚）。
2. 家族会议/任务派发引入**信箱 + 状态机**（9-19 会议轮次靠人工 collect，Orca 是事件驱动）。
3. 状态修改只发生在 daemon 事实源、TUI/WebUI 只读的架构约束（permissions.py 双进程不一致教训）。

## 4. PenguinHarness（Prism-Shadow）— 目标协议 + 错误恢复

**特点**：
- **GOAL.yaml 协议**：完成必须写协议文件（只许 complete/blocked 两值），沉默不算数；目标每轮重注入 + token 预算内嵌 + 连续 3 轮同阻塞判定 + 100 轮硬兜底 + 崩溃后磁盘目标保持 active 作恢复点。
- **LLM 错误分层**：retryable/fatal/aborted 三分类（未分类刻意归 retryable）；指数重连梯子（~60s 总耐心 + 20 轮绝对上限）+ UI 实时倒计时 + Retry now/Give up；auth 失败是可恢复锁（修 key 即解锁）。
- **自进化安全三底线**：benchmark 冻结后分数权威在文件（server 只校验不重算）、ship 前自动快照（排除秘密）、Candidate 严格高于 Reference 才接受；优化器上下文隔离（禁读金标防污染）。
- 状态文件全走单一 writer 原子替换（防半截 YAML）。

**lc 可借鉴**：
1. 14 连熔断场景做「配额重置倒计时 + 到点半开」（硬配额熔断已解析重置时刻，只差 UI 呈现与自动恢复）。
   > 2026-09-21 追记：止血半边已落地（cbff31f 解析重置时刻 → 冷却到点，1310 撞墙后自动落 volcengine 候选），
   > 「到点半开 + 自动恢复」的主动重试未做。
2. self_optimizer 补齐「冻结基准 + 严格单调接受 + 上下文隔离」三底线。
3. 任务回执制引入 GOAL 协议（开放任务给 complete/blocked 两值契约，替代 48h 盯人）。

## 5. oh-my-hermes（OMH）— 证据边界纪律

**特点**：
- **宣称必须有观测**：没有 `runtime_observation/v1` 事件，UI 禁止说「已通过/已合并」；progress/gap/blocker 三语义分离（gap=没人做过的步骤，blocker=运行时失败，只有后者打扰用户）。
- **executor-readiness 三态**（missing/blocked/stale）+ repair card：说清差什么、附修复命令，但永不谎称已修复。
- **mixture-of-models 路由**：ultrabrain/deep/quick 档 = 模型 + reasoning effort 绑定。
- **置信词表闭集**（plan/running/seen/failed/verified/blocked/cancelled），未识别状态 fail-closed 判 not run。

**lc 可借鉴**：
1. H17（声明-验证闭环）从守卫文档升格为**协议层**：状态只能渲染观测事件，progress/blocker 语义分离。
2. 路由切换失败给 repair card（灵知 DATABASE_URL 缺失即典型 stale 场景）。
   > 2026-09-21 追记：repair_card.py 已落地（1cb8208），OPEN/REPAIRING/RESOLVED/FAILED 状态机 + fail-closed；
   > 尚未接 task_router.check_switch_target_health 的路由切换门禁。
3. 模型路由档位化（coding 决策位旗舰+高 effort，fast_response 用 flash，比 flash 门禁更平滑）。

## 6. codex-host（BytePioneer）— 保真投影

**特点**：
- **保真而非公约数**：每 harness 走原生接口（Pi 官方 RPC / Claude Agent SDK），流式/审批/diff/权限原生语义直接投影，不降级再模拟。
- **CLI shim 字节透明**：16KB 双向转发只拦 app-server 启动点（`crates/shim/src/lib.rs`），管理命令/SSH 通道原样走官方。
- **桌面层受控增强**：CDP 注入 + 原型方法包装（不动安装文件、不碰内部 DOM），「绝不触碰」清单成文档级约束。
- **外部 thread steer = 取消-等待-自动重启**（20s 预算 < 30s 超时，去重 + outcome-unknown 回执）。

**lc 可借鉴**：
1. agent-gateway 深接 codex/cc 时透传各自审批/权限/diff 语义（当前只有 invoke/status 文本粒度）。
2. 「宿主增强不动官方壳」的边界清单做法——lc 给外部 agent 加守卫（lc_mcp_guard）时同理：能透传就不包装。

## 7. agent-harness（madebywild）— 配置单源化

**特点**：`.harness/` 一份事实源 → 生成 AGENTS.md/CLAUDE.md/copilot-instructions；per-provider overrides 合并语义；manifest.lock.json 记 provenance；registry pull 工作流。

**lc 可借鉴**：
1. 25 个插片 manifest 单源生成 + lock 记 provenance——直接治「测试断言 20 插片、org_member 实有 22」的账本漂移事故（b4b2034 实录），漂移由 CI 事前发现而非审计事后。

## 8. hermes-webui（nesquena）— 极简控制面

**特点**：零构建（Python+vanilla JS）、进程内跑 agent 直读 HERMES_HOME、SSH 隧道独占访问（不暴露端口）、离线跑 cron。

**lc 可借鉴**：
1. lc 的 WebUI（13458/13460）可吸收「SSH 隧道是唯一暴露面」的安全模型——健康巡检 13460 对公网开放，应收敛。

## 9. hermes-studio（EKKOLearnAI）— 控制面工程

**特点**：BFF（Koa :8648）→ Socket.IO 单命名空间按 source 分派三种 runtime 到统一事件管线；**auth.json credential_pool 数组**（同 provider 多账号 LRU 轮转 + 独占平台凭据 clone 剥离）；多后端文件桥（local/Docker/SSH/Singularity/Modal/Daytona 统一 FileProvider 接口）；模块命名空间隔离（新 agent 只写 adapter 不动共享面）。

**lc 可借鉴**：
1. **凭据池模型**——GLM 1310 周期限额耗尽的根治：`credential_pool[glm]` 放多账号/多套餐（智谱 coding 套餐 + volcengine 同源 + 开放平台），轮转 + 独占凭据剥离。
   > 2026-09-21 追记：半边已落地——`quota_governance.py`（1cb8208）做 QuotaWindow 窗口记账
   > （defer/allow/stale 三态，「问窗口不猜」），但多账号 LRU 轮转的 credential_pool 未做；
   > 另 cbff31f 已把硬配额熔断做到「解析重置时刻 → 冷却到点」（本文 #1 的止血版）。
2. 统一事件管线（provider 流 → ProtocolAdapter → 多订阅者），llm_proxy 现状是单通道。

## 10. omarchy（basecamp）— 桌面公民化

**特点**：`omarchy-agent-usage-{claude,codex,fireworks}` 每 provider 一个纯函数收集器 → 统一 JSON 契约 → 状态目录 watch（新增 agent = 新增一个文件，零面板改动）；限额走各 provider 官方端点（codex app-server JSON-RPC），缓存故障自动降级直扫；**事件→prompt→skill 指针**的 crash 诊断（coredump 通知直接驱动 agent 读 SKILL.md）。

**lc 可借鉴**：
1. provider 用量/限额收集器化（硬配额熔断只有「失败才记录」，omarchy 是**主动轮询限额端点**——重置时刻可提前知晓而非事后解析错误文本）。
2. 灵族健康巡检（SDT-lc-002）按收集器契约化，新增探测 = 新增一个文件。

---

## 11. lc 优化方向汇总（按 ROI 排序）

> **2026-09-21 追记（落地状态）**：本文 09-20 定稿，09-21 双会话已落地 8 提交
> （32a729b/98ffd72/3d16d8f/b99921c/cbff31f/6f34d5e/1cb8208/9cedbd7）+ 一批未提交工作树改动。
> 下表每项已按实对照更新「状态」列：
>
> - **#1 凭据池** → 部分落地：`quota_governance.py`（方案C v4 P0#1）实现 QuotaWindow 窗口记账
>   （defer/allow/stale 三态，对齐 atomcode rate_limit hook 的「不猜要问」），但 **credential_pool
>   多账号 LRU 轮转未做**——仍是单 provider 配额治理，多账号池化仍待补。
> - **#2 dispose/reload** → 已落地：`plugin_lifecycle.py`（六态状态机 + 依赖指纹 epoch +
>   disposer 逆序 + 蓝绿 hot_swap），24 测试全绿。
> - **#3 repair card 三态** → 已落地：`repair_card.py`（OPEN/REPAIRING/RESOLVED/FAILED 状态机，
>   RESOLVED 必须携带验证证据，fail-closed），未接 `task_router.check_switch_target_health`。
> - **#4 lc_plugins_inspect** → 未落地。
> - **#5 worktree 扇出** → 未落地。
> - **#6 会话树 + checkpoint** → 部分落地：`b99921c` 做了 turn_start round=-1 checkpoint
>   （崩溃恢复），但**完整 JSONL 树会话 + fork/orphan 语义未做**（仍消息级）。
> - **#7 配额主动轮询** → 部分落地：`quota_governance.py` 预留 usage_api 扩展位，主动轮询未做。
> - **#8 GOAL 两值协议** → 未落地（repair_card 是修复卡状态机，非任务回执契约）。
> - **#9 证据边界协议化** → 部分落地：repair_card RESOLVED 必须带证据（fail-closed）已覆盖
>   「修复证据」半边；progress/gap/blocker 三语义分离 + H17 升格协议层未做。
> - **#10 manifest 单源 + lock provenance** → 未落地（但 9cedbd7 已把 test_agent_family
>   快照 20→22 对齐，账实漂移有局部治理）。
> - **#11 agent-gateway 保真投影** → 未落地。
> - **#12 自进化三底线** → 未落地（self_optimizer 无 frozen/reference/monoton 机制）。
> - **#13 TUI 双代热更** → 部分落地：plugin_lifecycle hot_swap 的蓝绿切换（candidate 验证 +
>   异常回滚旧代）已就位，但 **TUI 渲染层双代 cutover 未做**（b99921c 做的是输出侧，不是双代）。
>
> **已落地之外的新增收益**（本文未收录，见 20260921 扩展文档）：缓存命中可观测
> （cached_tokens 全链路，76-80% 实测省 54-57%）、硬配额熔断至重置时刻（cbff31f）。

### P0（一周内可做，直接消解已暴露的问题）

| # | 方向 | 来源 | 治什么 | 改动面 |
|---|------|------|--------|--------|
| 1 | 凭据池模型：`credential_pool[provider]` 多账号/多套餐 LRU 轮转 + 独占凭据剥离 | hermes-studio | GLM 1310 周期限额撞墙（熔断只到重置时刻，池化后可跨账号续跑） | task_router + config schema |
| 2 | 服务 dispose/reload：插片依赖消失自动摘除消费方，恢复自动重载 | DSH fiber.ts | 灵知启动即断/契约失配类「账实不符」，比周期探活更彻底 | plugin_loader |
| 3 | repair card 探活三态：missing/blocked/stale + 差什么+修复命令 | OMH | 路由切换失败只有 allowed/denied 二值，灵知 DATABASE_URL 缺场景无指引 | check_switch_target_health |
| 4 | lc_plugins_inspect 只读自省插片：模型可查 25 插片/熔断 slot/路由健康 | DSH cordis_inspect | 探活只能外部 wake 做，会话内盲区 | 新增 1 个插片 |

### P1（两周内，能力升级）

| # | 方向 | 来源 | 治什么 |
|---|------|------|--------|
| 5 | worktree 扇出：agent_batch 隔离从 scratch 目录升级为独立 git worktree，结果可 diff 择优可回滚 | Orca | 全族审计/多 agent 编排污染工作区，产出无法对比择优 |
| 6 | 会话树 + checkpoint 持久化：JSONL 树（parentId 分叉）+ 原子 commit + orphan 标记 | Pi coding-agent/durable | TUI 崩溃丢上下文、resume 只到消息级 |
| 7 | 配额主动轮询：收集器化各 provider 限额端点，重置时刻提前知晓 | omarchy usage | 硬配额熔断是事后解析，主动轮询可提前降级避峰 |
| 8 | GOAL 两值协议：开放任务回执改为 complete/blocked 契约 + 每轮重注入 + 硬兜底轮数 | Penguin | 48h 盯人式督办（灵研课题目4 塌方风险），任务闭环自动化 |

### P2（一个月内，治理加固）

| # | 方向 | 来源 | 治什么 |
|---|------|------|--------|
| 9 | 证据边界协议化：宣称只许渲染观测事件，progress/gap/blocker 三语义分离，置信词表闭集 | OMH | H17 仍是文档级自觉，升格为不可绕过的协议层 |
| 10 | manifest 单源 + lock provenance：25 插片由一份事实源生成，漂移 CI 事前报 | agent-harness | org_member 账本扩容后测试断言未跟上的漂移事故 |
| 11 | agent-gateway 保真投影：深接 codex/cc 时透传原生审批/权限/diff 语义，shim 最小拦截面 | codex-host | 外部 agent 能力被削平成文本进出 |
| 12 | 自进化三底线：冻结基准 + ship 前快照 + Candidate 严格单调接受 + 优化器上下文隔离 | Penguin | self_optimizer 只有触发预算约束，缺防污染与单调性 |
| 13 | TUI 双代热更：candidate 先激活验证、成功才 cutover、失败 dispose candidate | Pi chord | 插片热更半坏卡宿主 |

### 不做 / 慎做

- **omarchy 多 CLI 并存无协作**：lc 的价值在编队，学它等于自废。
- **hermes-studio 功能大爆炸**（微信登录/Kanban/群聊房间）：与 lc 薄主干纪律冲突，不学其膨胀路径。
- **Pi 协议层全量照搬**：lc 是 Python 单体，按需取 JSONL 树 + 纯函数循环即可，不必上 CBOR 帧协议。

## 12. 一句话路线图

**（09-20 原文，已过时——09-21 1cb8208 已落地 #2/#3/#9 半边，#1 部分落地，见 §11 追记）**

P0 四项全部围绕**已暴露的三类事故**：配额撞墙（#1）、插片账实不符（#2/#3）、会话内盲区（#4）。
建议先入 4 张 arch_audit_task 再动手；P1/P2 按自驱节奏推进。

**（09-21 修订版）剩余未落地优先序**：#4 lc_plugins_inspect（治会话内探活盲区）→ #1 补齐
credential_pool 多账号轮转 → #5 worktree 扇出 → #8 GOAL 两值协议（治灵研课题目4 类开放任务督办）
→ #12 自进化三底线。P0 原文「先入 4 张 arch_audit_task」的执行结果：#2/#3 已入 1cb8208 落地，
#1 半落地，#4 未动工。

## 附：溯源与证据

- 各仓库源码快照在 `/tmp/harness_read/`（omarchy 在 `omarchy-src/`），行号引用以该快照为准。
- DSH 行号来自其 `docs/cordis-api/*` 自动生成的源码链接（`vendor/cordis/src/*.ts#L行号`）。
- lc 侧对照点：`lingclaude/model/task_router.py`（F12b/F12j 熔断，硬配额熔断 2026-09-20 已修）、
  `lingclaude/plugins/agents/`（25 插片）、`lingclaude/core/permissions.py`（双进程内存不一致教训）、
  9-19 会议 `data/family_meetings/task_receipts_20260919.json`（探活复盘）。
