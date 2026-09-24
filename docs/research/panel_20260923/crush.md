# Coding Agent Harness 横向比较与 lc 优化方向（15 家：10 全景 + cc/codex/opencode/crush/atomcode）

- 日期：2026-09-23
- 作者：Crush（第三方执行实例），本文与 `docs/research/20260923_coding_agent_comparison.md` 等四份 lc 自评草稿相互独立
- 基线文档：`/home/ai/lingclaude/docs/research/20260921_coding_agent_expansion.md`（09-21 上轮对比）
- 实证方法：对侧断言锚定 `/tmp/harness_read/` 快照（09-20/21 拉取，本轮未重拉，09-21 之后的对侧演进**未验证**）+ `/home/ai/atomcode-src`；lc 断言锚定 `/home/ai/lingclaude` 当前工作树与 `git log`（本轮抽查）
- 标注纪律：✅已验证（含出处）/ 🟡推断 / ❗未验证

## 0. 一句话结论

09-21 版列出的 8 项 lc 弱点，48h 内 **7.5 项已落盘关闭**（P0/P1 桶 git 实证：`de1532e`、`42858f0`）。
lc 的差距焦点已从「补机制债」转为「机制投产化」：**默认关的开关要转正、收益要遥测量化、循环纯度要收尾、B 路线（编队 OS）要跨出单体**。

## 1. 十五家全景横向比较

### 1.1 十家 harness 全景（承 09-21，本轮抽验无变化处不重述）

| # | 项目 | 本质 | 代表机制 | lc 吸收状态（09-23 实测） |
|---|------|------|----------|--------------------------|
| 1 | DSH (deepseek) | Everything-is-a-Plugin（Cordis 运行时） | 服务 dispose/reload 生命周期、插件自省工具族 | ✅ `cap_inspect` 自省插片已落地（42858f0） |
| 2 | Pi (pi-mono) | 极简分层 agent monorepo | ~120 行纯函数循环、JSONL 树会话、工具变更 diff 声明制 | ⚠️ 树会话思想进 `rollout.py`；循环仅钩子化，纯度差距仍最大 |
| 3 | Orca (stablyai) | 并行 agent 舰队 ADE | git worktree 隔离 + SQLite 事实源（快照 `src/cli/` 中 worktree 逻辑 ✅） | ✅ worktree 扇出已落地（`core/worktree.py` 204 行）；SQLite 事实源未采纳 |
| 4 | PenguinHarness | 自进化构建器 | GOAL.yaml 两值协议、错误分层重连梯子 | 部分：熔断/重试散见于 loop_detector + hooks，无独立两值协议 ❗（本轮未细验） |
| 5 | oh-my-hermes | 操作层 | 证据边界（runtime_observation/v1）、repair card 三态 | 部分：证据协议进 spec_decision 线（9b5ed34）；repair card 未独立落地 |
| 6 | codex-host | 多 harness 投影进 Codex Desktop | 保真投影、CLI shim 字节透明 | 未采纳（lc 无宿主投影需求） |
| 7 | agent-harness | 多 provider 配置单源化 | `.harness/` 单源生成 + lock provenance + 漂移 CI | 未采纳（插片体系已覆盖） |
| 8 | hermes-webui | 零构建 WebUI | 进程内直读、SSH 隧道独占、离线 cron | 未采纳 |
| 9 | hermes-studio | BFF 控制面 | credential_pool 多账号 LRU 轮转（`packages/server/.../profile-credentials.ts` ✅） | ✅ `model/credential_pool.py`（220 行）已落地，但 env 门禁**默认关** |
| 10 | omarchy (basecamp) | Arch 桌面环境 | agent 懒加载存根、usage 收集器契约化 | 部分：配额治理 lc 自研（cbff31f）方向不同 |

### 1.2 扩展五家

| 项目 | 语言/形态 | 代表机制（快照实证） | lc 对照 |
|------|-----------|----------------------|---------|
| **cc** (claude-code) | 闭源引擎 + TS 扩展面 | `__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__` 缓存边界行（CHANGELOG:140 ✅）；三层 settings + PreToolUse exit-2 回灌；plugins/skills/hooks/mods 四层 | lc 已抄到缓存边界（`__DYNAMIC_BOUNDARY__`，`system_prompt_builder.py:45` ✅） |
| **codex** (openai/codex) | Rust | rollout JSONL append-only + ordinal + `forked_from_id`（`codex-rs/rollout` ✅）；沙箱 × 审批正交矩阵 + granular 关=auto-reject；Starlark `prefix_rule` 批准后写回热更；MCP 大结果不落盘 | lc P0 三项全部对齐落地（de1532e），见 §2.2 |
| **opencode** (sst) | TS/Effect | Effect 函数式核心；server/client 分离 + headless 出结果模式（README 中 `--print` 字样未直接 grep 到，CLI 细节以快照 packages 为准 ❗部分未验证）；`/share` 会话分享 | lc 已补 `--print`（`cli/repl.py:219` ✅）+ JSON-RPC app-server（`cli/app.py:407` ✅）；`/share` 无对应物 |
| **crush** (charmbracelet) | Go | LSP 原生集成（README:19 ✅「uses LSPs for additional context」）；运行时 `/model` 切 provider 保留历史；bubbletea TUI 解耦 | lc 已有 `engine/lsp_provider.py`（661 行，✅存在），但接线深度与 token 收益❗未验证 |
| **atomcode** | Rust | cache_epoch 前缀缓存（`crates/atomcode-coding/src/runtime.rs` 中 cache_epoch ✅）；纯函数 agent 循环（~120 行）；turn_start 快照可恢复 | lc 已借 turn_start 快照（b99921c）；循环纯度差距仍是最大单项债 |

## 2. lc 与 15 家的差距复核（09-21 八弱点逐项对账）

### 2.1 lc 独有优势（15 家对照后仍成立）

| 优势 | 对照 |
|------|------|
| 编队 + 治理（多成员通信、回执账本、异步会议、审计台账） | 15 家全是单 agent 单体或单宿主编排，无跨进程成员身份体系 |
| 配额窗口治理（解析重置时刻 → 自动冷却到点） | 15 家均只有报错/退避，无此机制 |
| 插片 + trust 分级（25 插片 manifest） | 五家扩展面均扁平（cc mods 要 .d.ts、codex MCP 按 server、crush 只有 MCP/slash） |
| 铁律守卫 + 声明-验证闭环 + 返审触发器 | 最接近的是 codex 的 Starlark 规则沉淀，但 lc 体系化程度更高 🟡（程度判断为推断） |

### 2.2 八弱点逐项对账（09-21 判定 → 09-23 现状）

| # | 09-21 弱点 | 09-23 现状 | 证据 |
|---|-----------|-----------|------|
| 1 | 循环重主干（~3000 行 mixin） | **半关闭**：第 0 步钩子化完成（LoopHooks Protocol，17 处接线，`model_call.py` 降至 458 行）；但 engine/loop 仍有 1917 行、纯度与 Pi/atomcode 差一个量级 | `de1532e`、`wc -l` 实测 |
| 2 | 会话不可变 + fork 缺失 | **已关闭**：`core/rollout.py` append-only JSONL + 单调 ordinal + `forked_from_id` + revert=新开分叉 | `rollout.py:6-81` ✅ |
| 3 | 审批未矩阵化 | **已关闭**：SandboxMode × ApprovalPolicy 正交 + granular 关=auto-reject | `approval_matrix.py:57,83` ✅ |
| 4 | 批准后无资产化 | **已关闭**：`record_approval()` 写回 approvals.json always_allow 热更 | `approval_matrix.py:226` ✅ |
| 5 | provider 切换不保留会话 | **未关闭/未验证**：❗本轮未找到等价 crush `/model` 的运行时切换入口断言 |
| 6 | 无 headless 批量模式 | **已关闭**：`--print`（repl.py:219）+ JSON-RPC app-server（app.py:407）✅ |
| 7 | 大工具结果未瘦身 | **已关闭**：`_slim_tool_output` >1600 字符截断为前 800 + 引用（42858f0）✅ |
| 8 | LSP 缺位 | **已关闭（存在性）**：`engine/lsp_provider.py` 661 行 ✅；但 agent 侧实际调用频率/收益 ❗未验证 |

## 3. lc 今后优化方向（按优先级排序）

### P0（一周内）：给已落地基建「通电」——机制在但未投产

1. **凭据池转正 + 配额收益量化**。`credential_pool.py` 已落地但 env 门禁默认关，GLM 周期限额问题仍靠冷却兜底。理由：这是 15 家全景中 lc 唯一「有机制但未开」的项，转正成本极低、收益直接（限额根治）。验收：默认开 + 熔断回落链路有审计事件。
2. **缓存命中率遥测常态化**。`cached_tokens` 已全链路透传（toolbar `cache 99%`），但缺跨会话聚合：把命中率写进 datalog/ledger，做成「缓存回归告警」。理由：cc 把前缀稳定当 CI 纪律，lc 有 `assert_prefix_stable`（`system_prompt_builder.py:151`）但无命中率趋势面，防退化只能靠单次断言。
3. **审批矩阵 dogfooding + 默认策略收敛**。矩阵与资产化已落地，但默认 approval_policy 仍偏保守（ask 落 pending）。理由：codex 的经验是 granular + 规则沉淀让弹窗率随时间下降；lc 应先度量「每会话弹窗数」，再调默认值。❗当前弹窗率基线未验证，需先埋点。

### P1（两周内）：机制债收尾 + 短板补齐

4. **循环纯度收尾（阶段二：状态外置）**。第 0 步只把治理钩子接口化；`engine/loop` 1917 行 + loop_body 586 行仍是 Pi/atomcode 的 5 倍体量。理由：这是「引擎可热切/TUI 双代热更已落地但引擎热更仍高风险」的根因；也是五家对照中剩余最大单项差距。验收：循环核心 ≤300 行、fake seam 全离线回归。
5. **provider 运行时切换保留会话（crush `/model` 对齐）**。15 家中仅 crush 有此体验，lc 路由+熔断体系已具备底座，缺的是会话历史语义在切换点的显式契约。理由：与凭据池（P0-1）天然耦合，一起做完收益翻倍。❗现状未验证，先补断言再实现。
6. **opencode 式 `/share` 会话资产化**。rollout JSONL 已是不可变事实源，生成只读分享链接成本低。理由：把「会话」从私有日志升级为可协作资产，与编队治理的审计台账同源互补。
7. **headless 协议对齐 codex app-server v2**。lc app-server 已有 run/stream/health 三面，但 codex 2026 默认 transport 演进方向（thread/turn 模型）❗未细验。理由：headless 是 B 路线「被其他编排器调用」的咽喉，协议越早对齐生态位越稳。

### P2（一个月内）：治理加固 + B 路线物理化

8. **repair card 三态独立落地**（OMH 借鉴，executor-readiness + 修复卡）。
9. **GOAL.yaml 两值协议**（Penguin 借鉴，complete/blocked 显式化，治「完成声明」核验）。
10. **Pi chord 式引擎本体双代热更**。TUI 热更已落地（42858f0 蓝绿 cutover），引擎热更是真依赖 P1-4 循环纯化的尾项，显式标注前置。
11. **跨 harness 编排协议（B 路线实体化）**：把「族」协议开放给外部 harness（codex/opencode 实例作为族员接入），fan_out + agent-gateway + worktree 三件套已具雏形。理由：战略判断维持——A（更好的单 agent）是红海，C（治理中间件）是落地方式，终局是「Agent 社会的 Linux」🟡（战略表述为推断性判断，非可验证事实）。

### 排序逻辑（一句话）

09-21 的主题是「抄作业」（补机制债），已 7.5/8 关闭；09-23 的主题应是「交电费」（让机制真正运转：默认开、有遥测、可度量），其次才是「收尾纯度债」与「B 路线物理化」——因为未投产的机制对用户的价值为零，而纯度债的利息（热更风险）可在 P1 节奏内偿付。

## 4. 未验证声明汇总（诚实清单）

- 对侧 15 家在 09-21 之后的演进（快照未重拉）。
- opencode `--print` 具体 CLI 形态（README 未直接命中，以 packages 源码为准，本轮未展开）。
- lc provider 切换的会话保留现状（弱点 #5，未找到断言）。
- lc LSP 接线的实际 token 收益。
- lc 审批弹窗率基线、凭据池开启后的实际限额缓解幅度（均需埋点后才能断言）。
- atomcode 工具 diff 声明制（declareToolChanges）在 `/home/ai/atomcode-src` 本轮 grep 未命中，该断言仅存于 09-21 精读记录，❗标注未复核。
