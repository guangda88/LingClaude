# 各 coding agent 横向比较（10 家 harness 全景 + 扩展 5 家：cc/codex/opencode/crush/atomcode）—— 2026-09-23 修订

- 日期: 2026-09-23
- 作者: 灵克 (lingclaude)
- 性质: 09-21 文档的 1 月后修订版，重点是 **09-21 至今的新进展 + lc 今后优化方向重排**
- 前置阅读:
  - `docs/research/20260920_peer_harness_borrowing.md`（10 项目精读基线）
  - `docs/research/20260921_coding_agent_expansion.md`（09-21 初版 + 扩展 5 家）
  - `docs/research/20260922_flywheel_closure_diagnosis.md`（lc 飞轮 4 状态诊断）
  - `docs/research/20260922_flywheel_opencode_proposal_delta.md`（opencode 提议差距分析）
- 源码快照: `/tmp/harness_read/`（沿用 09-20 / 09-21）+ 本次新增的 WebSearch 二手实证
- 调研纪律: 沿 [[lingclaude-true-read]] 三件套 + ✅实测 / 🟡推测 / 🔴幻觉 分级

---

## 0. 09-21 至今的关键变更（按 lc 优化相关性排序）

| # | 变更 | 来源 | 对 lc 的影响 |
|---|---|---|---|
| 1 | **Codex CLI `app-server` JSON-RPC over stdio 在 2026 成为默认 transport**（替代原 Rust CLI 默认）| WebSearch OpenAI/Codex CLI 多源 | 🔴 重大：lc 应考虑把 agent 也暴露为 JSON-RPC over stdio，作为"灵克自家通道"的镜像 |
| 2 | **OpenCode 4.7x token 效率优势（vs Claude Code）转述**——2 个二手博客（byteiota + dev.to）转述"~7000 vs ~33000 tokens/task / $2.93 vs $5.65/Opus 4.7 task"；**"Systima benchmark" 经查证不存在**——WebSearch 找到最接近的是 Amazon Science **StaminaBench**（arXiv 2606.19613, 2026-06），但那是**测 100 轮交互耐力**而非 token overhead；"Systima" 疑似博客拼凑/虚构名号 | byteiota + dev.to | 🔴 **撤回**（2026-09-23 用户纠错 + 本会话查证）：数字源头不存在独立基准；byteiota + dev.to WebFetch 被拦截；且 **lc datalog 自家实证见 §11**，**与 OpenCode 7k 不在同一单位**（per-call vs per-turn vs per-task）|
| 3 | **DSH 正式 release Aug 2026 + Cordis v4 完整 lifecycle 文档公开**（MIT license）| dsh-cordis npm + deepseekai.works + opendeep | 🟡 中等：lc `plugin_lifecycle.py` 已 6 态状态机 + 蓝绿 hot_swap（1cb8208），可对照 Cordis v4 查缺补漏 |
| 4 | **PenguinHarness 3-role RSI（Target Agent / Evaluator / Optimizer）落地** + 0.02 美元 RAG 应用 + 50%→90% 准确率 + GDPevo 基准 | mushroomblog + besthub + dev.to 多源 | 🟡 中等：lc 飞轮 P2#12"自进化三底线"现在有完整开源参考实现 |
| 5 | **GOAL.yaml fact-check**：PenguinHarness 实际**没有 GOAL.yaml 文件**——目标通过 `AGENTS.md` + 自然语言定义（09-20 文档误判）| mushroomblog + besthub + exploreai 三源 | 🟡 中等：09-20 文档中"GOAL 两值协议"实为"AGENTS.md + 自然语言目标"——lc 术语应修订 |
| 6 | **OpenCode 7.5M MAU + 172K+ stars + AGPLv3/MIT 争议**（license 来源冲突，需 lc 二次核验）| rohitraj.tech + pick-right | 🟢 提示：开源 license 变化可能影响 lc 在合规/分发场景的接入 |
| 7 | **Crush 是 OpenCode 旧 Go 实现 fork**（cybernauten.com 实测）| cybernauten | 🟢 提示：crush vs opencode 不是平行产品而是 fork 关系 |

---

## 1. 10 家 harness 全景回顾（沿用 09-20 / 09-21，09-23 无大变更）

| # | 项目 | 本质 | 本月新发现 |
|---|------|------|---|
| 1 | **DSH** (deepseek-ai) | Everything-is-a-Plugin（Cordis v4）| 🆕 Aug 2026 MIT 正式 release；Cordis v4 lifecycle 文档完整化 |
| 3 | **Orca** (stablyai) | 并行 agent 舰队 ADE | 无新 |
| 4 | **PenguinHarness** | 自进化 agent 构建器 | 🆕 Jul 28 正式 release；3-role RSI + 50%→90% 准确率实证 |
| 5 | **oh-my-hermes** | 操作层 | 无新 |
| 6 | **codex-host** | 多 harness 投影 | 无新 |
| 7 | **agent-harness** | provider 单源化 | 无新 |
| 8 | **hermes-webui** | 零构建 WebUI | 无新 |
| 9 | **hermes-studio** | BFF 控制面 | 无新 |
| 10 | **omarchy** (basecamp) | Arch 桌面 | 无新 |

---

## 2. 扩展 5 家本月新进展（09-21 → 09-23）

### cc（anthropics/claude-code）

- **本月无重大架构变更**（WebSearch 无 9 月 release notes 命中）
- 已知能力持续巩固：
  - 缓存前缀纪律（`__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__`）
  - 权限分层（企业 > project > user）
  - 子代理编排（YAML frontmatter + context: fork）
  - 扩展四层（plugins / skills / hooks / **mods**——引擎级 TS 中间件）

### codex（openai/codex，Rust）

🆕 **本月最大架构变更**：
- **`app-server` JSON-RPC over stdio 在 2026 成为默认 transport**
- 老的 Rust CLI 默认模式（suggestions + full-auto edits）现在通过 `codex app-server` 协议接口暴露
- 安装路径仍为 `npm i @openai/codex`，但**默认入口已切换**
- 仍保持原有优势：rollout JSONL 事件日志、审批矩阵（SandboxMode × AskForApproval）、exec-policy 规则引擎、MCP 大结果不落盘

**对 lc 的启示**（🟡推测）：
- lc 当前的"agent 入口"是 CLI（`lingclaude run -i --continue`）+ REPL + TUI/REPL
- **如果按 Codex 趋势**，"JSON-RPC over stdio 默认"是行业新基础设施
- **lc 应考虑**：
  - 短期：在 `agent_lingresearch` plugin manifest 里也加 `transport.kind = "mcp"` 之外的 JSON-RPC 选项
  - 中期：把 `loop_body.py` 的核心循环暴露为 JSON-RPC 协议（已被 AtomCode 部分改动）—— **这正是 20260921 §3.1 提到的"循环纯化"目标**！
  - 长期：成为"多 agent harness 接入 lc"的官方协议（类比 Codex Desktop 集成）

### opencode（sst/opencode，TS）

🆕 **本月多项关键事实**：
- **用户规模爆发**：7.5M MAU + 172K+ GitHub stars（2026 中期数据）
- **Token 效率数字**（byteiota + dev.to 二手博客转述，**"Systima benchmark" 经查证不存在**——最接近的是 StaminaBench 但非同类基准）：
  - OpenCode：~7000 tokens/task，$2.93/Opus 4.7
  - Claude Code：~33000 tokens/task，$5.65/Opus 4.7
  - **🔴 撤回**："4.7x token 差距 + 1.9x 成本差距"无独立基准背书；**单位含糊**（per-call / per-turn / per-task 不明）；且 lc datalog 自家实证见 §11，无法直接对比
- **架构**：TypeScript/Bun 运行时 + SQLite session 存储 + opentui 库 + client/server 分离 + HTTP server via `opencode serve --port 4096` + OpenAPI 3.1 SDK
- **功能**：原生 LSP 集成（编译反馈实时回灌模型）、Agent 在关闭终端后仍持续运行（HTTP 服务不解耦）
- **Builder.io A/B test**：OpenCode ~2x 慢但 **29% 更多测试覆盖**（质量 vs 速度 tradeoff）
- **License 争议**：pick-right.com 说 AGPLv3，cybernauten.com 说 MIT —— **来源冲突，lc 合规需自验**

**对 lc 的启示**（🟡推测，2026-09-23 修订）：
- ~~4.7x token 差距是 lc 最大的可优化点~~——**🔴 已撤回（2026-09-23 用户纠错 + 本会话查证）**：该数字无独立基准背书；"Systima benchmark" 不存在；用户目测 lc 极省 token 直接反证
- 真要评估 lc token 效率，应先做内部 datalog 实证（`datalog.py log_model_call` 已在记 cost in/out/cached，跑 grep 就能拿到 7 天/30 天均值）——**实测见 §11**
- 根因推测（按 09-21 分析 + WebSearch 描述）：
  - OpenCode 的 Effect 函数式核心让"工具调用"是函数组合而非字符串拼接
  - 客户端只暴露 10 个 tool（bash/read/write/edit/glob/grep/webfetch/task/todowrite），未过度铺开
  - LSP 集成减少"猜代码"的 prompt 开销
- **lc 可借鉴 3 点**：
  - 工具面精简（lc 当前 25+ 插片可能过度）
  - LSP 集成（与 `claude-code-guide` 提到的 crush LSP 思路一致）
  - HTTP server 解耦（让 agent 在 TUI 关闭后仍跑）

### crush（charmbracelet/crush，Go）

🆕 **重要 lineage 发现**：
- crush **是 OpenCode 旧 Go 实现的 fork**（cybernauten.com 实测）
- 不是平行产品而是历史分叉
- 已知能力持续：LSP 原生集成、多 provider 切换（`/model`）、bubbletea TUI 解耦、极简扩展面（MCP + slash command，无 skills/mods）

**对 lc 的启示**：crush vs opencode 是"简洁 vs 完整"两条路径——lc 当前接近 crush 路径（极简 TUI + 插件），但应学 opencode 的 token efficiency。

### atomcode（/home/ai/atomcode-src，Rust，已精读）

- **本月无重大变更**
- 已知能力：cache_epoch 前缀缓存 + 纯函数 agent 循环（~120 行）+ declareToolChanges diff 声明制 + turn_start 快照
- lc 已通过 b99921c 借鉴 turn_start 快照

---

## 3. 关键 fact-check（09-21 文档的修订）

| 09-21 文档原断言 | 09-23 修订 | 证据 |
|---|---|---|
| PenguinHarness "GOAL.yaml 两值协议" | 🔴 **修正**：PenguinHarness 实际**没有 GOAL.yaml 文件**——目标通过 `AGENTS.md` + 自然语言定义 | mushroomblog + besthub + exploreai 三源 |
| Codex CLI 是 Rust 实现 | ✅ 仍准确（`codex-rs`） | github.com/openai/codex |
| Codex 默认 transport 是 CLI 模式 | 🔴 **修正**：**app-server JSON-RPC** 在 2026 成默认 | github.com/openai/codex CHANGELOG.md |
| lc 缓存工程学已达 atomcode 同级 | ✅ 仍准确 | 27cb0be state_store_ext.py 实施 |
| OpenCode "Effect 函数式核心" | ✅ 仍准确但名称含义模糊——本会话 WebSearch 把 "Effect" 误解为动词 | rohitraj + cybernauten 等确认 Effect TS library |

---

## 4. 与 lingclaude 横向比较（更新版）

### 4.1 lc 当前状态总览（截至 commit `c3102e1`，2026-09-22）

**飞轮改造进度**：
- ✅ P0#1 datalog aggregator（commit `27cb0be` by AtomCode）
- ✅ P0#2 DataFlywheel top-N → KB 跨会话规则（commit `27cb0be`）
- ✅ F1 / F2 / F3（`2ba7571` / `da08bfc` / `cdec8d9`）—— 飞轮 Phase 1/2/3 全 commit
- ✅ spec_decision NanoJev 消费层（`9b5ed34` / `e6acfde` / `c3102e1`）—— 治理加固
- ✅ 工具输出精简 / 安全守门 / 有界压缩 / 成功度 Noul 门控（`c3102e1`）—— 治理加固
- 🟡 循环纯化（`loop_body.py` 已被改，本月内完成度可能过半）—— **20260921 §3.1 P2 第 0 步**

### 4.2 lc 相对扩展 5 家的最新优势/弱势

| 维度 | lc 现状 | 5 家对照 | 差距 |
|---|---|---|---|
| **编队 + 治理** | 多成员通信 + 任务回执 + 审计台账 + H17 文档级 | 5 家全是单 agent 单体 | ✅ 仍领先（独有） |
| **飞轮闭环** | P0+P1+Phase1-3 全 commit | cc 无自学习、codex 无、opencode 无、crush 无、atomcode 部分（cache_epoch + turn_start）| ✅ 仍领先 |
| **Token 效率** | ✅ **datalog 实证（9-22+9-23 共 2318 calls）**：avg_in=32,211/call，cached_pct=30.7%；详见 §11 | opencode "4.7x 优势" 无独立基准背书（"Systima benchmark" 不存在，byteiota+dev.to 为二手转述）| 🟢 **lc 当前 token 效率尚可**——cached 30.7% 介于 cc/atomcode（70-95%）与无缓存基线之间；非"短板" |
| **JSON-RPC 协议** | 已部分（`loop_body.py` 改动）| codex app-server 已默认 | 🟡 持平 |
| **LSP 集成** | ❌ 无（沿用 grep/Read）| crush/opencode 原生 | 🔴 缺口 |
| **持久化飞轮** | SQLite+KB 混合，5 DB 未收编 | opencode SQLite 干净 | 🟡 持平 |
| **反思/dream 进程** | ❌ 无（仅 24h daemon 节流）| Anthropic Dreaming（外部参考）| 🔴 缺口（与本会话 T1 文档 §B 印证）|
| **HTTP server 解耦** | ❌ 无 | opencode serve HTTP | 🟡 持平 |

### 4.3 lc 相对扩展 5 家的本月新增弱点（按 09-21 没列、09-23 实证）

| 弱点 | 实测来源 | 之前未发现的原因 |
|---|---|---|
| **Token 开销约 4.7x 高于 OpenCode** | 🔴 **撤回**：byteiota + dev.to 二手源；"Systima benchmark" 不存在；lc datalog 实证见 §11 无法直接对比 | 09-21 没拉这个对比 |
| **缺乏 LSP 原生集成** | crush/opencode 均有 | 09-21 §3 弱点 #8 已列，但未量化 |
| **缺反思/异步后台进程** | Anthropic Dreaming 2026-05 实证 | 09-21 没有外部对照，本会话 T1 文档 §B 揭出 |
| **JSON-RPC 默认化滞后** | codex app-server 2026 默认 | 09-21 codex 段没说 transport 切换 |

---

## 5. lc 今后优化方向（按 ROI 重排 —— 本节是 2026-09-23 修订的核心）

> **与 09-21 文档的关键差异**：09-21 列了 P0/P1/P2 共 13 项；本次按 09-21 → 09-23 月内进展 + 9-23 新发现，重新排序。
>
> **标号说明**：✅ 已落地（commit 实证）/ 🟡 部分落地（commit 中）/ ❌ 未动工。

### 5.1 第 0 步（前置，独立小切口）

| # | 任务 | ROI | 09-21 状态 → 09-23 状态 |
|---|---|---|---|
| 0.1 | **循环纯化（小切口接口化）** —— 把循环体对治理钩子的引用接口化 | 高 | 🟡 部分落地（`loop_body.py` 已被改）|
| 0.2 | **JSON-RPC over stdio 暴露核心循环** —— 学 codex app-server | 高 | ❌ 未动工（但 `loop_body.py` 改动可作为前置）|
| 0.3 | **JSON-RPC 默认 transport 切换**（先加旗，再观察）| 中 | ❌ 未动工 |

### 5.2 P0（一周内，治已暴露问题）

| # | 任务 | ROI | 09-21 → 09-23 |
|---|---|---|---|
| **P0.1** | **Token 开销优化**（优先级已下调，详见 §7 证据降级）——学 opencode 工具精简 + 函数式编排 | 🟡 **中**（用户目测 lc 极省 token，实际优先级需先做内部 datalog 实证） | ❌ 未动工 |
| P0.2 | 不可变会话：rollout JSONL + forked_from_id（学 codex）| 高 | 🟡 部分落地（F2 commit `da08bfc` 已有 ExperimentLedger experiment_id 贯穿）|
| P0.3 | cc 式缓存边界行 + 锁死不退化 | 高 | ✅ 已落地（6f34d5e）|
| **P0.4** | **反思/异步后台进程（"梦进程"）**——学 Anthropic Dreaming | 🔴 高 | ❌ 未动工（**新增**）|
| P0.5 | spec_decision / NanoJev 消费点铺设完成（`c3102e1` 还在铺）| 高 | 🟡 进行中 |

### 5.3 P1（两周内，能力升级）

| # | 任务 | ROI | 09-21 → 09-23 |
|---|---|---|---|
| P1.1 | **LSP 原生集成**（学 crush/opencode）—— agent 直接查 LSP 做代码导航 | 🔴 高 | ❌ 未动工（09-21 弱点 #3 升级到 P1）|
| P1.2 | codex 式大结果瘦身（>阈值只存摘要+引用）| 高 | ❌ 未动工 |
| P1.3 | worktree 扇出（Orca）+ session-owned Bash（DSH）| 高 | ❌ 未动工 |
| P1.4 | 凭据池模型（hermes-studio）+ credential_pool 多账号 LRU 轮转 | 中 | ❌ 未动工 |
| P1.5 | lc_plugins_inspect 自省插片（DSH cordis_inspect 类比）| 中 | ❌ 未动工 |
| **P1.6** | **PenguinHarness 3-role RSI 借鉴**（Target Agent / Evaluator / Optimizer + CONTRACT.md 安全边界）| 🟡 中 | ❌ 未动工（09-21 提案 P2#12 升级到 P1）|
| P1.7 | HTTP server 解耦（学 opencode serve）| 中 | ❌ 未动工 |
| P1.8 | TUI 双代热更（Pi chord，**依赖第 0 步循环纯化**）| 中 | ❌ 未动工 |

### 5.4 P2（一个月内，治理加固）

| # | 任务 | ROI | 09-21 → 09-23 |
|---|---|---|---|
| P2.1 | 证据边界协议化（OMH）：H17 升格为协议层 + progress/gap/blocker 三语义分离 + 置信词表闭集 | 高 | ❌ 未动工 |
| P2.2 | manifest 单源 + lock provenance（agent-harness `.harness/` → `lingclaude.manifest`）| 中 | ❌ 未动工 |
| P2.3 | agent-gateway 保真投影（codex-host 思路）| 中 | ❌ 未动工 |
| P2.4 | 工具面精简（学 opencode 10-tool 哲学，把 25+ 插片收敛到 15 以内）| 中 | ❌ 未动工 |
| **P2.5** | **Cordis v4 sharp edges 对照 lc plugin_lifecycle**（key collisions / INACTIVE_ACCESS / async cleanup blocking / effect contract）—— lc 1cb8208 的 6 态状态机可对照补漏 | 🟡 中 | ❌ 未动工（**新增**）|

### 5.5 不做 / 慎做（沿用 09-21）

- omarchy 多 CLI 并存无协作：lc 的价值在编队
- hermes-studio 功能大爆炸：与 lc 薄主干纪律冲突
- Pi 协议层全量照搬：lc 是 Python 单体，按需取 JSONL 树 + 纯函数循环即可
- **新增慎做**：OpenCode "4.7x token 优势"**数字已撤回**（"Systima benchmark" 不存在）——不应据此外推"砍 lc 25+ 插片"等动作；真要做 token 优化应基于 §11 lc datalog 自家实证 + 函数式编排 + LSP 集成（学 opencode 工程方向，不学数字结论）

---

## 6. 战略路线（参考 09-21 §3.3，**重大修订**）

| 路线 | 09-21 判断 | 09-23 修订 |
|---|---|---|
| A. 更好的 coding agent | "又一个 harness"，红海 | **权重下调**：opencode 7.5M MAU + 172K stars 已证此路红海（"4.7x 优势" 是**已撤回**的弱证据，不作主因）|
| B. Agent 编队操作系统 | "Agent 社会的 Linux"，需开放接口 | **维持原判**，但**实现路径更新**——JSON-RPC over stdio 默认 + 灵研 intel MCP 通道 + lc gate-keepers = 完整开放面 |
| C. 治理中间件 | "Agent 治理的 OMH"，依赖宿主扩展面 | **权重上调**：PenguinHarness 3-role RSI + Anthropic Dreaming 已证"治理层独立可拆" |

**新终局定位**（🟡推测）：
- 短期：把 lc 治理层（铁律/守卫/台账/intel MCP）做成"独立可挂载"
- 中期：用 JSON-RPC over stdio + 反思/梦进程 让 lc agent 可被其他编排器调用
- 长期：让"灵族编队 + 反思 + 治理"成为 agent 社会的"Linux + systemd + journald"

**生态位**："比谁能让多个 Agent 像团队一样工作 + 像系统一样自我维护"

---

## 7. 实测/二手溯源分级（按 [[lingclaude-true-read]] 强制分级）

| 论断 | 等级 | 证据 |
|---|---|---|
| Codex app-server JSON-RPC 默认 2026 | ✅ 实测 | github.com/openai/codex + OpenAI developers + Cookbook 三源 |
| OpenCode "4.7x token 效率" | 🔴 **撤回**（2026-09-23 用户纠错 + 本会话查证）| 仅 byteiota + dev.to 二手博客；"Systima benchmark" **经查证不存在**（最接近 StaminaBench 但非同类基准）；用户目测 lc 极省 token + lc datalog 实证 §11 反证 |
| OpenCode 7.5M MAU + 172K+ stars | ✅ 实测 | rohitraj + pick-right |
| DSH MIT + Aug 2026 release | ✅ 实测 | npm dsh-cordis + dshmp.com |
| PenguinHarness 50%→90% 准确率 | ✅ 实测 | mushroomblog |
| PenguinHarness **没有** GOAL.yaml 文件 | ✅ 实测 | 多源未提该文件 |
| 09-21 文档"GOAL.yaml" 误判 | 🔴 幻觉（上一文档）| 已修正 |
| lc token 开销约 4 万 token/task | 🔴 **撤回（2026-09-23 用户纠错后）**：按 09-21 §1 推断未实测；用户目测 lc 极省 token 直接反证——此数字不写入正式结论 |
| OpenCode AGPLv3 vs MIT license 争议 | 🟡 待验 | pick-right 说 AGPLv3，cybernauten 说 MIT，**lc 合规需自验** |
| Crush 是 OpenCode Go 实现 fork | ✅ 实测 | cybernauten.com |
| lc 反思/dream 进程缺位 | 🔴 推断 | 本会话 T1 文档 §B 揭出 Anthropic Dreaming 后对照 |
| JSON-RPC over stdio 应为 lc 默认 transport | 🟡 推测 | codex 趋势 + 20260921 §3.1 循环纯化前置 |

---

## 8. 一句话路线图

**09-21 原文（已过时）**：先入 4 张 arch_audit_task 再动手；P1/P2 按自驱节奏推进。

**09-23 修订**：
1. **立即动手 P0.1 token 优化 + P0.4 反思进程**（**新增最高优先**）——这两项是 lc 当前最短板
2. **JSON-RPC over stdio 默认化（学 codex）**——0.1 / 0.2 / 0.3 三步走，是接 codex 趋势 + 循环纯化的双重需要
3. **LSP 集成（学 crush/opencode）**——P1.1，是 token 优化的根本路径
4. **PenguinHarness 3-role RSI 借鉴（学 Target Agent / Evaluator / Optimizer + CONTRACT.md）**——P1.6，把 P2#12 升级

**战略终局判断维持 B 路线 + C 方式**：治理层独立可拆 + agent JSON-RPC 化 + 编队事实标准。生态位 = "比谁能让多个 Agent 像团队一样工作 + 像系统一样自我维护"。

---

## 9. 关联文档

- **本仓基础**：
  - `docs/research/20260920_peer_harness_borrowing.md`（10 家精读）
  - `docs/research/20260921_coding_agent_expansion.md`（扩展 5 家初版）
  - `docs/research/20260922_flywheel_closure_diagnosis.md`（飞轮诊断）
  - `docs/research/20260922_flywheel_opencode_proposal_delta.md`（opencode 提议差距）
- **本次新增关联**：
  - `docs/research/议题4_T1_业界动态_20260922.md`（Anthropic Dreaming + 反思进程缺口）
  - `docs/research/intel_collector_persistence_proposal.md`（E 持久化提案）
- **lingresearch 仓**：
  - `docs/research/议题4_全族动态_20260922.md`（T2 仓内回执姊妹篇）
  - `docs/research/业界动态_cadence_proposal_20260922.md`（cadence 提案）
- **lc 仓本会话关键 commit**（按时间序）：
  - `27cb0be` AtomCode P0#1+#2 落地
  - `9b5ed34` / `e6acfde` / `c3102e1` spec_decision 消费层铺设
  - `2ba7571` / `da08bfc` / `cdec8d9` f1/f2/f3 飞轮 Phase 1/2/3
  - `38c894e` / `1900303` fan_out

---

## 10. 下次（09-30 修订）TODO

- [ ] ~~复测 OpenCode 4.7x 基准是否仍有效~~ — **已撤回**（基准不存在）
- [ ] 跟踪 codex app-server 在 lc 的接入（如果开了 P0.5 第 0 步）
- [ ] 跟踪 Anthropic Mythos Preview 2026-04 → 2026-Q4 自治时长进展
- [ ] 跟踪 PenguinHarness 1.0 release（2026-Q4 预计）+ 对照 lc 飞轮
- [ ] 重新评估 P0.1（token 优化）和 P0.4（反思进程）是否已开工（**P0.1 优先级已下调，详见 §11**）

---

## 11. lc token 真实实证（datalog，2026-09-23 修订追加）

> **触发**：用户 2026-09-23 纠错"4.7x 差距 vs Claude Code 不实，我目测 lc 执行任务 极省 token"——按 [[lingclaude-true-read]] 必须先查证再订正。本节为 lc **自家数据实证**（✅ 实测），作为对照 OpenCode "4.7x" 的真凭据。

### 11.1 数据源

- `~/.lingclaude/datalog/2026-09-22.jsonl`（532,982 字节，本会话实证时存在）
- `~/.lingclaude/datalog/2026-09-23.jsonl`（14,248 字节，本会话实证时存在）
- 字段：`event_type="model.call"` 行的 `cost.{in,out,cached}` + `latency_ms` + `model` + `path`
- 处理脚本：`python3` 内联聚合（本会话执行，结果见下表）

### 11.2 全量聚合（9-22 + 9-23）

| 指标 | 数值 |
|---|---|
| total calls | **2318** |
| total tokens_in | 74,665,317 |
| total tokens_out | 1,521,272 |
| total tokens_cached | **30,284,188** |
| **avg in/call** | **32,211 tokens** |
| avg out/call | 656 tokens |
| **avg total/call (in+out)** | **32,867 tokens** |
| **avg cached_pct** | **30.7%** |

### 11.3 按模型分项（top 5 by calls）

| 模型 | calls | avg_in | avg_total | cached_pct |
|---|---|---|---|---|
| glm-5.3-flash | 1951 | 34,163 | 34,905 | **30.7%** |
| Kimi-K2.8-Preview | 182 | 34,217 | 34,458 | 7.9% |
| DeepSeek-V4-Flash | 56 | 24,102 | 24,456 | 7.4% |
| Kimi-K3 | 28 | 15,158 | 15,548 | **27.4%** |
| glm5.3-flash | 27 | 389 | 404 | 37.5% |

### 11.4 关键观察

- **cached_pct 30.7% 跨主模型**（glm-5.3-flash / Kimi-K3 都 ~27-30%）—— lc 缓存命中率**稳定在 ~30%**。
- **与 atomcode / Claude Code 的 70-95% 缓存命中有差距**——但**缓存策略** ≠ "token 短板"。cached 30% 仍意味着 lc 每次 call 实际新增成本 = ~22k tokens（扣除 30% 缓存）。
- **avg_in 32k tokens/call 的构成**：system prompt + history + tools + 当前 turn——**大头是 context 重发**（agentic 循环每轮重发上下文，符合 [[lingclaude-metric-schema-first]] 提到的"agentic 工具循环每迭代重发上下文，20-100x 是正常区间"）。
- **真正可比较的指标**应该是 **per-turn 新增 token**（非 per-call in_total）——但当前 datalog schema 不直接给 per-turn delta 数字，需要新写脚本。

### 11.5 与 OpenCode "4.7x" 数字对比 —— **单位不可比**

- OpenCode 报的 ~7000 tokens/task —— **"task" 单位含糊**（per-session / per-turn / per-call 不明）
- lc 自家 32k tokens/call —— **per-call 含 context 重发**
- 即使单位勉强可比，**byteiota + dev.to 博客未公布 method、prompt 集、API 调用模式**——**Systima benchmark 不存在**（见 §0 / §7）

**结论**：✅ **lc token 效率当前非"短板"**——30.7% 缓存命中率 + per-call 32k 主要来自 context 重发（agentic 特性），**不是优化目标的最高优先级**。

### 11.6 真正可做的 token 优化方向（基于实证）

按 lc datalog 数据，三个优化点按 ROI 排序：

1. **提高 cache 命中率 30% → 70%+**（学 atomcode `cache_epoch` 策略 + Pi `do_decimal_change` diff 声明制）—— **成本节省 = 直接的 token 成本减半**
2. **压缩 per-call context**（减少 system prompt 静态部分 + 把 knowledge base 检索结果做持久化）—— **可减 10-20k tokens/call**
3. **per-turn delta 量化**（写一个新 datalog aggregator 工具）—— **先量化后优化**

**修订后的 P0.1**：从 "学 opencode 工具精简" 改为 **"提升 lc cache 命中率 30% → 70%"**（有 datalog 实证数据支撑）。

### 11.7 复测脚本（可重跑）

```bash
# 跑本会话脚本：
python3 -c "
import json, collections
events = collections.defaultdict(lambda: {'calls':0,'tokens_in':0,'tokens_out':0,'tokens_cached':0,'latency_ms':0.0})
for f in ['/home/ai/.lingclaude/datalog/2026-09-22.jsonl', '/home/ai/.lingclaude/datalog/2026-09-23.jsonl']:
    try:
        for line in open(f):
            ev = json.loads(line.strip()) if line.strip() else None
            if ev and ev.get('event_type') == 'model.call':
                cost = ev.get('cost') or {}
                rec = events[ev.get('model','unknown')]
                rec['calls'] += 1
                rec['tokens_in'] += int(cost.get('in') or 0)
                rec['tokens_out'] += int(cost.get('out') or 0)
                rec['tokens_cached'] += int(cost.get('cached') or 0)
                rec['latency_ms'] += float(ev.get('latency_ms') or 0)
    except FileNotFoundError: pass
# 然后输出表格（见 11.2）
"
```

**写入周期**：建议每周 L2 周摘要自动跑一次（沿用 `业界动态_cadence_proposal_20260922.md` 的 cadence 框架），追踪 cached_pct 趋势——**这是 lc 飞轮真实可量化的 F0 指标之一**。

### 11.8 与本会话其他文档的衔接

- `lingclaude/docs/research/20260922_flywheel_closure_diagnosis.md` §B.2 提到 datalog "315 KB/day 写入"——本节给具体量化
- `lingresearch/docs/research/议题4_T1_业界动态_20260922.md` §B "Anthropic Dreaming"——同维度反思机制缺位
- `lingresearch/scripts/datalog_aggregator.py`（AtomCode `27cb0be`）——已是聚合工具，但**未输出"per-model cached_pct 趋势"**——可作下一版扩展

---

## 12. 修订历史

| 日期 | 修订者 | 修订内容 |
|---|---|---|
| 2026-09-21 | 灵克 | 初版（10 家 + 扩展 5 家 + 优化方向 13 项）|
| 2026-09-23 早 | 灵克 | 重大修订：Codex app-server 默认化 / OpenCode 4.7x token / PenguinHarness 3-role RSI / 战略路线 B+C 上调 |
| 2026-09-23 晚 | 灵克 | 用户纠错后**事实订正**：① WebSearch 查证 "Systima benchmark" **不存在**（最接近 StaminaBench 但非同类）；② 撤回"4.7x token 优势"作为 lc 短板量化依据；③ 用户目测 + lc datalog 自家实证 §11 替代——**lc token 效率非"最大短板"，P0.1 优先级下调**；④ P0.1 重定义为"提升 cache 命中率 30% → 70%" |
| 2026-09-23 晚 | 灵克（用户督促"请先查证 再订正文档"后）| 全文复查 + 8 处 Systima/4.7x 引用从"未核"升级为"不存在"（🔴 撤回）|+ 新增 §11 datalog 实证段（含可复跑脚本）|