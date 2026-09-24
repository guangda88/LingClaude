# Coding Agent Harness 横向比较与 lingclaude 改进方向

- 日期：2026-09-23
- 依据：`docs/research/20260921_coding_agent_expansion.md`；本机核实 `/tmp/harness_read/`、`/home/ai/atomcode-src`、`lingclaude` 仓库。
- 说明：引用对端实现处均已用本机快照抽查关键符号；无法核实的点单独标「未验证」。

## 1. 十家 harness 全景

| # | 项目 | 核心定位 | 最值得借鉴的机制 |
|---|------|----------|------------------|
| 1 | DSH（deepseek） | 插件化运行时 | 服务 dispose/reload 生命周期、三角色能力模式、模型自省工具 |
| 2 | Pi | 极简分层 monorepo | 无状态纯函数循环、工具变更 diff 声明、JSONL 树会话 |
| 3 | Orca | 并行 agent 舰队 | git worktree 隔离、SQLite 事实源、消息信箱状态机 |
| 4 | PenguinHarness | 自进化构建器 | GOAL 协议 only `complete/blocked`、错误分层、自进化底线 |
| 5 | oh-my-hermes | 操作层 | 证据边界协议、executor-readiness 三态 + repair card、模型混合路由 |
| 6 | codex-host | Codex Desktop 多 harness 投影 | 保真投影、CLI shim 字节透明、宿主增强不改官方壳 |
| 7 | agent-harness | 多 provider 配置单源 | `.harness/` 单源生成 AGENTS/CLAUDE 文档、lock provenance、漂移 CI |
| 8 | hermes-webui | 零构建 WebUI | 进程内 agent 直读、SSH 独占、离线 cron |
| 9 | hermes-studio | BFF 控制面 | credential_pool 多账号 LRU、统一事件管线、多后端桥 |
| 10 | omarchy | Arch 桌面环境 | agent 懒加载、usage 契约化、事件→prompt→skill 的 crash 诊断 |

这十家的共同启示是：**单 agent 循环已经不够看**，竞争点在可恢复状态、治理、隔离、扩展面与编排。lc 的「编队 + 治理」仍是最独特的位置，但工程底座需要向 codex/atomcode/Pi 看齐。

## 2. 扩展五家横向比较

| 维度 | Claude Code | OpenAI Codex CLI | OpenCode | Charm Crush | AtomCode | lingclaude 现状 |
|------|-------------|------------------|----------|-------------|----------|----------------|
| 会话持久化 | README/CHANGELOG 可见缓存与 resume 生态，引擎闭源细节未验证 | rollout JSONL append-only，`forked_from_id` + 截断 ordinal，回退=新分叉 | SQLite 索引 + 文件正文 | 常规会话，未验证树/分叉 | compaction stub + `cache_epoch`，崩溃可恢复 | 已有 `core/rollout.py`，fork/revert 记源 id 与 ordinal，原文件不删改 |
| 缓存策略 | `__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__`，上方全局缓存 | 大结果引用化，减少持久化膨胀 | 常规 provider 使用 | 常规 | `cache_epoch` 单调尾部破缓存 | system prompt 分离固定前缀与动态 tail suffix；cached_tokens 可观测 |
| 权限/审批 | 企业>项目>用户 settings，hook exit-2 阻断回灌 | Sandbox × Approval 正交矩阵，granular 关=自动拒绝，批准可沉淀为规则 | tool/provider permission map + TUI 审批 | 按 agent 角色 allowed tools | 权限模式与快照联动 | 已有 ask/auto/strict/plan 模式环与守卫，但「沙箱 × 审批策略」正交矩阵未见完整实现；「未验证」是否有同命令规则资产化 |
| 批处理/headless | SDK/CLI 可用，闭源面未验证 | app-server JSON-RPC | `run --print`、server/client 分离 | TUI 为主 | CLI/kernel 分离 | `--print/--json` 与 JSON-RPC app-server 已落地，TUI/headless 共享循环入口 |
| 扩展机制 | plugins/skills/hooks/mods 四层，mods 靠 `.d.ts` 契约 | MCP + exec policy | Effect 模块插件 | MCP + slash | 工具 diff 声明 + hook tail-append | 插片 manifest、trust 分级、L1/L2 接缝、熔断/路由治理更体系化 |
| 代码理解 | 依赖引擎内工具，闭源不可核 | 常规 grep/exec 工具链 | 常规 | LSP 原生导航 | LSP/语义层有路线但以 kernel 工程为主 | 主要仍 grep/read；LSP 工具面是明确短板 |
| 编排 | skill 可 fork 子代理 | 有子代理/fork 能力 | server 可外部编排 | 单 agent | 单 agent 偏工程内核 | 多成员编队、任务账本、审计台账是独有优势 |

五家对比后的结构性判断：

1. codex 代表「状态与权限资产化」的工程上限：会话不可变、审批可沉淀、大结果不膨胀。
2. Claude Code 代表「缓存纪律 + 分层权限 + 渐进披露扩展」的产品上限；但闭源部分只能按公开材料判断。
3. OpenCode 代表「同一核多入口」：TUI/Web/SDK/headless 不应是不同产品。
4. Crush 的 LSP 是低成本高收益的 token 省减器。
5. AtomCode/Pi 代表「小循环内核」：循环体可重入、状态外置、diff 声明，长期维护性最好。

## 3. lc 的位置

### 已补齐或接近补齐

- **不可变会话**：`lingclaude/core/rollout.py` 已对齐 codex rollout 思路，含 fork/revert 元数据与 best-effort 记录。
- **headless**：`cli/app.py` 与 `cli/repl_turn.py` 已有 `--print`、`--json`、app-server 入口。
- **缓存边界**：`system_prompt_builder.py` 明确固定前缀 + 动态 tail suffix，避免恢复/压缩时前缀漂移。
- **工具输出瘦身与有界压缩**：近期提交已铺开“工具输出精简/有界压缩”，对齐 codex 大结果瘦身方向。
- **可观测性**：cached_tokens、权限模式、上下文真实口径已进入 CLI/摘要层。

这些已经改变了 09-21 文档中的部分弱点排序：headless、不可变会话、缓存边界、大结果瘦身不应再列为“最大缺口”。

### 仍然明确的差距

1. **审批规则资产化不足**：codex 有 `sandbox × approval` 正交与批准后 allow 前缀热更；lc 目前更接近模式环 + 单点审批。此判断基于仓库 grep 与 09-21 文档，未发现反证，但未做端到端运行验证。
2. **循环内核仍偏重**：`model_call.py` 已缩到约 458 行，但治理钩子、路由、journal、熔断仍与主干耦合；对比 AtomCode/Pi 的外置状态 + 纯循环，演进成本仍高。
3. **LSP 工具面缺位**：Crush 可直接查 LSP；lc 主要 grep/read，强类型仓库下 token 成本高。
4. **worktree 扇出尚未成为默认路径**：`proj_agent_gateway/server.py` 已有 WorktreeSession 接线，但注释标注 env 门禁默认关。
5. **配额/凭据池治理未完全产品化**：studio 的 credential_pool LRU 是多账号限额的通用解；lc 有 quota 治理与 inspect 面旧证据，是否已全链路轮转「未验证」。

## 4. lc 优化方向：按优先级

### P0：把已建好的地基变成默认可用

1. **审批矩阵 + 批准资产化**
   - 做法是对齐 codex：`sandbox_mode(read-only / workspace-write / full)` × `approval_policy(untrusted / on-failure / granular / never)`；granular 关闭时自动拒绝而非反复打扰。
   - 一次性人工批准应写入可热更 allow 规则，按命令前缀或工具签名沉淀。
   - 理由：这是当前最能减少用户打扰、又提高安全一致性的短板，且不依赖循环重构。

2. **worktree 扇出默认化与失败回收**
   - 现在能力已在 gateway 中，但默认关。应给任务类型加安全门槛：涉及多文件修改、测试运行、可冲突写入时默认 worktree。
   - 增加“成功合并、失败清理、审计记录三分支”的生命周期。
   - 理由：Orca 已证明隔离是并行 agent 的地基；只有隔离，编队才不会互相污染。

3. **循环 seam 接口化继续收口**
   - 不是为了炫技式纯函数，而是把 journal、track_behavior、路由、熔断、纠错钩子抽成可注入 seam。
   - 验收标准：测试能用 fake seam 离线驱动同一循环；TUI/headless/子代理共享同一实例。
   - 理由：这是后续 hot_swap、fan-out、策略实验复用的前置。model_call 已经变小，现在做成本最低。

### P1：补齐编码体验与多账号可持续性

4. **LSP 工具面**
   - 先做最小四件：definition/references/documentSymbol/diagnostics，缓存 workspace 索引。
   - 理由：Crush 的路线证明这比继续堆 grep 更省 token，也更适合大仓库重构。

5. **credential_pool / 配额轮转**
   - 把 provider 凭据变成资源池，按限额、成本、健康度 LRU/加权轮转。
   - 理由：GLM 有限额类问题被 09-21 文档反复提示；这是编队规模化前的硬约束。

6. **`lc_plugins_inspect` 标准化**
   - 会话内直接查插片清单、trust 级别、熔断状态、路由健康、rollout 状态。
   - 理由：lc 的治理密度是优势，但优势必须能被 agent 和用户低门槛看见。

7. **JSONL/rollout 与任务账本打通**
   - rollout 不只做 replay，还要能定位某次 fork 对应的 task ledger、审计记录和实验分支。
   - 理由：lc 的独特价值是“治理可追溯”，孤立 rollout 无法释放这个价值。

### P2：长期形态

8. **manifest 单源 + lock provenance**
   - 借 agent-harness：一套 manifest 生成 agent 文档、插片契约、权限描述与版本锁定，防漂移。

9. **证据边界协议化**
   - 把 OMH 式 `runtime_observation` 纪律变成协议层，而不是个别守卫规则。

10. **GOAL 两值协议**
   - 目标状态只允许 `complete/blocked`，blocked 必须带可执行障碍描述。适合作为编队任务的统一收口。

11. **TUI 双代热更**
   - 最后做。它真正依赖循环 seam 化，风险也最高；应放在主干稳定之后。

## 5. 总判断

lc 不应试图在“单 agent 手感”上全面追赶 Claude Code 或 Codex；那会把独有价值拖入红海。lc 的正确路线仍是：**用 codex/atomcode/Pi 的工程内核纪律，承载自己独有的编队、治理与审计层**。

短期优先级很清楚：先把审批矩阵、worktree 默认化、seam 化三件事做成默认体验；然后补 LSP 与凭据池；最后把治理协议升格为可对外挂载的平台能力。这样 lc 不是“又一个 CLI harness”，而是“可治理、可追溯、可编队的 agent 操作内核”。
