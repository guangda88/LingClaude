# Coding Agent Harness 横向比较与 lc 改进方向（2026-09-23）

> 承自 `docs/research/20260921_coding_agent_expansion.md`（10+5 全景、差距清单、优先级 P0/P1/P2），
> 并按本机资源交叉核实：本轮 lc 工作树已新增 `feat(spec_decision)` 系列 commit（6f56726…513c505），
> 主干文件 `loop_body.py`（586 行）/`model_call.py`（458 行）/若干 wiring/fan_out/flywheel 落账
> 模块；与 atomcode（Rust 多 crate，agent.rs 单文件）对照已读取源码。

---

## 1. 全景（10 家 + 扩展 5 家，共 15 家）

| # | 项目 | 本质 | 对 lc 最有借鉴价值的机制（已读源码/快照） |
|---|------|------|-------------------------------------------|
| 1 | DSH (deepseek) | Everything-is-a-Plugin（Cordis） | dispose/reload、lc_plugins_inspect 自省、三角色能力 |
| 2 | Pi (badlogic) | 极简分层 monorepo | ~120 行纯函数循环、JSONL 树会话、Pico durable |
| 3 | Orca (stablyai) | 并行舰队 ADE | git worktree 隔离 + SQLite 事实源 |
| 4 | PenguinHarness | 自进化构建器 | GOAL.yaml 两值协议、错误分层重连梯 |
| 5 | oh-my-hermes | 操作层 | runtime_observation/v1 证据边界、repair card 三态 |
| 6 | codex-host | 投影层 | 保真投影、CLI shim 字节透明、宿主增强 |
| 7 | agent-harness | 多 provider 单源化 | `.harness/` 锁文件 + 漂移 CI |
| 8 | hermes-webui | 零构建 WebUI | SSH 隧道独占、离线 cron |
| 9 | hermes-studio | BFF 控制面 | credential_pool 多账号 LRU 轮转 |
| 10 | omarchy | Arch 桌面 | usage 收集器契约化、事件→prompt→skill 诊断 |
| 11 | **cc** (anthropics) | 闭源引擎 + 公开协议 | `__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__` 行、四层扩展（plugins/skills/hooks/mods，mods 用 `(e,d,e,next)` TS 中间件 + `.d.ts` 契约） |
| 12 | **codex** (openai, Rust) | rollout 不可变会话 | `rollout/src/recorder.rs` 后台 mpsc JSONL；fork/revert 写新文件 + `forked_from_id`；沙箱 × 策略正交 3×4 矩阵；Starlark `prefix_rule` 审批沉淀为规则 |
| 13 | **opencode** (sst, TS) | Effect 函数式核心 | provider/models.dev 多源适配；`opencode run --print` 一行 headless；`/share` 会话变可读链接 |
| 14 | **crush** (charm, Go) | LSP 优先单 agent | LSP 直查导航、`/model` 运行时切 provider 保留历史、bubbletea 壳解耦 |
| 15 | **atomcode** (本机 Rust) | 纯函数循环 + cache_epoch | 循环体 ~120 行，cache_epoch 单调，turn_start 快照崩溃可恢复 |

---

## 2. 横向比较（按维度）

| 维度 | cc | codex | opencode | crush | atomcode | **lc（当前）** |
|------|----|----|----|-----|----|----|
| 循环纯度 | 闭源（未验证） | Rust 状态机（未验证） | Effect 编排 | Go channel | ✅ ~120 行 | ⚠ loop_body 586 行 + 治理钩子耦合 |
| 会话不可变 + fork | 无（未验证） | ✅ rollout JSONL | 部分（未验证） | 无（未验证） | 部分（未验证） | ❌ resume 是整段回放 |
| 审批矩阵化 | ✅ settings 三层 + exit-2 | ✅ 3×4 正交 + granular 关=auto-reject | permission map | allowed tools | ❌（未验证） | ⚠ approvals.json ask/auto 二值 |
| 审批沉淀 | 无（未验证） | ✅ Starlark `prefix_rule` 写回 default.rules | 无（未验证） | 无（未验证） | 无（未验证） | ❌ 一次性，不资产化 |
| provider 切换保会话 | ❌（未验证） | 部分（未验证） | ✅ | ✅ `/model` 运行时切 | ❌（未验证） | ⚠ 走路由+熔断，会话语义未验证保留 |
| headless 批量 | partial（未验证） | ✅ app-server JSON-RPC | ✅ `--print` | 无（未验证） | 部分（未验证） | ❌ 仅 TUI/REPL |
| 大结果瘦身 | 无（未验证） | ✅ MCP 结果只存引用 | 无（未验证） | 无（未验证） | ❌（未验证） | ⚠ 工具结果全量进历史（spec_decision ③④已铺开部分精简） |
| LSP 工具面 | ❌ | ❌ | ❌ | ✅ 原生 | ❌ | ❌ 靠 grep/读文件 |
| 缓存命中率可观测 | ❌ 缓存纪律有，命中观测无 | 无（未验证） | 无（未验证） | 无（未验证） | ✅ cache_epoch | ✅ `cached_tokens` 三元组 + toolbar（`feat(spec_decision)`/`feat(datalog)` commit） |
| 编队 + 治理 + 配额窗口 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ 独有（族、铁律/守卫/台账、配额主动轮询） |

**lc 相对 15 家的独有资产**（不可被替代）：编队+治理、配额窗口治理、缓存命中可观测、trust/plug 分级、声明-验证闭环。
**lc 相对 15 家的差距**（按影响排序）：①循环纯度 ②会话不可变 ③审批矩阵化 ④审批沉淀 ⑤headless ⑥LSP ⑦大结果瘦身。

---

## 3. lc 优化方向（按优先级）

> 依据 2026-09-21 文档 §3.1 源码核实结论：**循环纯化只是 hot_swap/fork 的真前置**，不是不可变会话/headless 的前置。
> 因此循环纯化做成「小切口接口化」（第 0 步），不阻塞 P0 三项。

| 序 | 动作 | 对标 | 理由（未验证项已标） |
|----|------|------|---------------------|
| **0** | 循环体对治理钩子（journal/track_behavior/路由 slot/hallucination_correction）抽 seam 接口，默认实现零行为变化 | Pi/atomcode | 不动 `_save_checkpoint`/`resume_interrupted`（§3.1 已实测反证依赖被高估） |
| **P0-1** | codex 式不可变会话：rollout JSONL（一行一事件 + ordinal）+ `forked_from_id`；fork/revert 写新文件 | codex | 改持久化层，循环体不动；治"回退=抹除历史"风险 |
| **P0-2** | codex 式审批矩阵：sandbox_mode × approval_policy 正交 + granular 关=auto-reject + allow 前缀写回规则热更（资产化） | codex | 同命令反复弹窗的根治；与当前 `Shift+Tab` 四态（auto/ask/strict/plan）正交叠加 |
| **P0-3** | cc 式缓存边界行：system prompt 嵌入 dynamic-boundary 行，动态内容全压其后；resume/compact 前缀字节不变当 CI 断言 | cc | 把 `6f34d5e`/513c505/d3e51d1 的前缀缓存命中优化锁死不退化 |
| **P1-4** | opencode 式 headless：`lc run --print` + JSON-RPC app-server | opencode/codex | §3.1 已证缺的是 CLI 入口协议，非循环纠缠；CI/批处理受益 |
| **P1-5** | codex 式大结果瘦身：工具结果 >阈值只存摘要+引用（与已铺开的 `spec_decision` ③④精简叠层） | codex | 长会话 token 膨胀根治 |
| **P1-6** | worktree 扇出（Orca）+ session-owned Bash（DSH） | Orca/DSH | 编队执行隔离 + 重启不丢所有权 |
| **P1-7** | 凭据池模型（hermes-studio）：credential_pool 多账号 LRU 轮转 | hermes-studio | GLM 1310 周限额的根治 |
| **P1-8** | lc_plugins_inspect 自省插片（DSH） | DSH | 会话内查 25 插片/熔断 slot/路由健康 |
| **P2-9** | 证据边界协议化（OMH）：H17 升格为协议层 | OMH | 与现有 H17 闭环申报叠加 |
| **P2-10** | manifest 单源 + lock provenance | agent-harness | 治理可审计 |
| **P2-11** | GOAL 两值协议 | Penguin | 阻塞信号标准化 |
| **P2-12** | crush 式 LSP 工具面 | crush | 小仓库强 LSP 场景省 token（替代 grep 全仓猜） |
| **P2-13** | TUI 双代热更（Pi chord）——**真依赖第 0 步** | Pi | 唯一真依赖纯化的 P2 项，故保留 |

---

## 4. 战略终局（维持 2026-09-21 修订判断）

**B 路线 = Agent 编队操作系统**，用 C 的方式落地：
- 短期：治理层做成可独立挂载（先证价值）
- 中期：headless + 循环纯化让 lc 引擎可被其他编排器调用
- 长期：「族」协议成为编队事实标准

**生态位 = "比谁能让多个 Agent 像团队一样工作"**——15 家中只有 lc 把"agent 社会"做成产品，
但若不补齐 P0 三项（不可变会话/审批矩阵/缓存边界行），单 agent 体验会被 cc/codex 甩开，
编队优势也难以被外部编排器复用。

---

## 5. 待办与未验证项

- **未验证**：第 9-14 行 cc/codex/opencode/crush 的"未验证"标注字段（需拉对应源码继续核实，
  `/tmp/harness_read/` 快照未在本轮 Read 全量，仅确认目录存在）。
- **未验证**：loop_body.py 当前 586 行 vs. 09-21 文档所述"5 文件 mixin ~3000 行"——可能
  是 §3.1 抽 seam 后已收敛，或文档口径是含 hooks/sub_agent/thread/l5_conversation_loop 的总和
  （未做加总核验）。
- **未验证**：25 插片 manifest 的精确数字与 trust 分级当前态。
- 下次落地建议：先用 `git diff lingclaude/engine/loop/loop_body.py` 锁定 seam 接口抽取面，
  再逐项 commit 对应 P0 序号。