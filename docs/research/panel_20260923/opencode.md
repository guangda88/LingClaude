# 各 coding agent 横向比较·复核版（10 家 harness + 扩展 5 家：cc/codex/opencode/crush/atomcode）

- 日期: 2026-09-23
- 作者: 灵克 (lingclaude)
- 前置阅读: `docs/research/20260921_coding_agent_expansion.md`（首版比较 + 15 家机制速览）、
  `docs/research/20260920_peer_harness_borrowing.md`（十家精读）
- 方法: 对侧 15 家沿用 09-21 源码快照（`/tmp/harness_read/` + `/home/ai/atomcode-src`，48h 内无重拉，
  对侧断言标记 ✅已验证(历史,锚点=09-21 快照)）；lc 侧全部断言以本仓库当前工作树 + `git log` 复核（✅已验证(本轮)）。

## 0. 一句话结论

09-21 版列出的 **8 项弱点在 48h 内关闭了 7.5 项**（证据见 §2），P0 三项 + P1 五项 + P2 五项全部落盘。
lc 的差距焦点已从「补机制债」转为「纯度收尾 + 门禁转正 + 飞轮收益审计」；战略判断维持
**B 路线（Agent 编队操作系统），用 C 的方式落地**，且 B 的物理基础（跨 harness 调度）本轮已出现。

## 1. 15 家机制全景（压缩速览，承 09-21 版，对侧无变化不重述）

| 阵营 | 项目 | 对 lc 最有价值的机制 |
|------|------|----------------------|
| 十家 | DSH | 服务 dispose/reload 生命周期、插件自省 |
| 十家 | Pi | 纯函数循环、JSONL 树会话、双代热更 chord |
| 十家 | Orca | worktree 扇出 + SQLite 事实源 |
| 十家 | PenguinHarness | GOAL.yaml 两值协议（complete/blocked） |
| 十家 | oh-my-hermes | 证据边界协议 |
| 十家 | codex-host | 保真投影、宿主增强不动官方壳 |
| 十家 | agent-harness | manifest 单源 + lock provenance |
| 十家 | hermes-webui | 零构建 WebUI |
| 十家 | hermes-studio | credential_pool 多账号 LRU 轮转 |
| 十家 | omarchy | usage 收集器契约化、懒加载存根 |
| 扩展 | cc | 缓存前缀边界行、三层权限、exit-2 回灌、mods 引擎中间件 |
| 扩展 | codex | rollout JSONL + forked_from_id、审批矩阵、Starlark 规则资产化、大结果不落盘 |
| 扩展 | opencode | Effect 核、headless `--print`、provider 多源、`/share` |
| 扩展 | crush | LSP 原生工具面、`/model` 运行时切 provider 保历史 |
| 扩展 | atomcode | cache_epoch 前缀缓存、~120 行纯函数循环、turn_start 快照 |

## 2. lc 弱点复核（09-21 版 §2 八项，逐项对账）

| # | 弱点（09-21） | 状态 | 证据（✅已验证本轮） |
|---|---------------|------|----------------------|
| 1 | 循环重主干（5 文件 mixin ~3000 行） | **大部分关闭** | 双路径循环体迁 `lingclaude/engine/loop/loop_body.py`（586 行）+ `hooks.py`（227 行 seam 接口，测试/headless 可注入 fake 钩子）+ `thread.py` LingClaudeThread 薄壳 facade（对标 CodexThread）。commits `3a5deee`/`b026246`。⚠ 残余：586 行 vs atomcode ~120 行，纯度差仍 ~5x；引擎循环本体不可热切 |
| 2 | 会话不可变 + fork 语义缺失 | **关闭（后端）** | `lingclaude/core/rollout.py`（244 行）：append-only JSONL + ordinal + `fork()`/`revert()` 写新文件记 `forked_from_id`，源文件永不删；`core/submission.py:342` checkpoint 旁路录制。⚠ 残余：无 `/fork` `/revert` CLI 面、无 `/share` |
| 3 | 审批未矩阵化 | **关闭** | `lingclaude/core/approval_matrix.py`：SandboxMode × ApprovalPolicy 正交 + `granular` 关=auto-reject（实测注释行 129-131）；CLI Shift+Tab 四态模式环 auto/ask/strict/plan + 跨进程 mtime 热加载（commit `513c505`） |
| 4 | 批准后无资产化 | **关闭** | 同 `approval_matrix.py:224-259`：`record_approval` → allow 前缀写回 `approvals.json` 热更（codex default.rules 对位） |
| 5 | provider 切换不保留会话 | **基本关闭** | `/model <name>@<provider>` 钉住 + `--ttl`（`cli/commands.py:312`），同引擎内切换历史保留。⚠ 残余：跨 provider 的 system prompt/工具集差异适配未实测 |
| 6 | 无 headless 批量模式 | **关闭** | `cli/app.py:885` `run --print` / `--json` + JSON-RPC app-server 常驻面（`:408`）；`repl_turn.py:_headless_turn` 与交互模式共享同一循环实例（第 0 步 seam 接口化的直接收益） |
| 7 | 大工具结果未瘦身 | **关闭** | `engine/loop/loop_body.py:47` 工具结果进历史前瘦身阈值（codex 语义：存摘要+引用） |
| 8 | LSP 缺位 | **关闭** | `engine/lsp_provider.py`（stdio JSON-RPC，definition/references/hover/implementation/documentSymbol/diagnostics）+ `lsp_session.py` 常驻会话池 + `lsp_registry.py` `/lsp add`（crush 风格）+ 工具面注册（`tool_registration.py:334`） |

**P2 治理加固项同步落盘**：`core/evidence_protocol.py`（P2-9 证据边界协议化）、`core/manifest_lock.py`（P2-10 manifest 单源+lock）、`core/goal_receipt.py`（P2-11 GOAL 两值 complete/blocked）、`cli/full_tui.py:394-469`（P2-13 Pi chord 双代热更，蓝绿 cutover）、`core/worktree.py`（P1-6 Orca 扇出 + proj_agent_gateway 接线）、`model/credential_pool.py`（P1-7 账号 LRU 轮转+熔断窗）、`plugins/agents/cap_inspect`（P1-8 自省插片：SeamRegistry/TaskRouter/CredentialPool 三路聚合）。

## 3. lc 新增独有优势（09-21 版没有，本轮新增）

| 优势 | 证据 | 15 家对照 |
|------|------|-----------|
| **投机扇出调度**（Speculative Fan Out） | `engine/loop/fan_out_scheduler.py`：difficulty 分桶门控 + confidence 门控 + 分支模板策略文件 + 重复度终止（commits `3082d7f`/`9064ce9`/`81e99ee`/`1900303`） | 15 家全无——cc 的 N-agent 并行是人工编排，无引擎内投机调度 |
| **自进化飞轮闭环** | f1 换目标函数（behavior_policy 搜索空间 + error_log 离线回放）+ f2 归因链（ExperimentLedger 贯穿 daemon 三出口 + accept/rollback 幂等结算）+ f3 学习固化（add_rule 幂等 upsert + corrections 流 + KB 跨会话规则桥接），commits `2ba7571`/`da08bfc`/`cdec8d9`/`27cb0be` | PenguinHarness 有 GOAL 协议但无离线回放评分；15 家无 ExperimentLedger 级归因 |
| **NanoJev 契约消费层** | spec_decision 三原语内核 + 完成声明核验 + 测试失败分诊（0 LLM token）+ 工具输出精简/安全守门/有界压缩/Noul 门控（commits `9b5ed34`/`e6acfde`/`c3102e1`） | 无对位 |
| **权限模式跨进程热加载** | Shift+Tab 四态环 + mtime 热更（`513c505`） | cc 三层 settings 需重启会话粒度更粗 |

## 4. 与 15 家横向再定位（2026-09-23）

**lc 已对齐或超越的机制**（相对 09-21 新增）：不可变会话（codex）、审批矩阵+资产化（codex）、headless（opencode/codex）、LSP（crush/atomcode）、大结果瘦身（codex）、缓存边界行+CI 断言（cc，`tests/test_prefix_cache_boundary.py`）、worktree 扇出（Orca）、凭据池（hermes-studio）、证据边界/manifest 单源/GOAL 两值（OMH/agent-harness/Penguin）、双代热更（Pi）、插件自省（DSH）。

**仍落后的点**（全部实证，按差距排序）：

| # | 差距 | 领先者 | 实况 |
|---|------|--------|------|
| 1 | 循环纯度 | atomcode/Pi（~120 行） | loop_body.py 仍 586 行；seam 已接口化但治理逻辑仍在循环体内联 |
| 2 | 引擎级热更 | Pi（循环双代） | plugin 层（`plugin_lifecycle.hot_swap` 蓝绿）与 TUI 层（P2-13）已双代，**引擎循环本体不可热切**——09-21 §3.1 裁定的唯一真依赖项仍未做 |
| 3 | fork/revert 无用户面 | codex | `RolloutRecorder.fork/revert` 后端在，无 `/fork` `/revert` 命令；无 opencode 式 `/share` 会话分享 |
| 4 | 沙箱网络维 | cc（Bash 网络 allowlist） | `engine/coding.py` 沙箱只有模式 env，无网络 allowlist |
| 5 | 门禁堆积（新出现的治理债） | — | credential_pool / fast lane / worktree / 投机扇出 均 env 门禁**默认关**（`factory.py:62`、`9d5cdc9`、`server.py:423`）——机制落了但生产路径未吃 |
| 6 | 跨 provider 切换适配 | crush | `/model` pin 保历史已通，但跨 provider system prompt/工具集差异未实测（弱点#5 残余） |
| 7 | 引擎本体规模 | 全部 | core/ + engine/ 仍是 15 家中最大单体；薄主干纪律靠守卫维持而非结构保证 |

## 5. lc 今后优化方向（重排优先序）

**P0（一周）——把已落机制「转正吃透」，不再开新机制**
1. **门禁转正评审**：credential_pool / fast lane / worktree / 投机扇出四项逐一做实机收益审计，达标转正（默认开）或挂账拆除——「默认关堆积」是本轮新产生的最大治理债，机制落了不吃等于没落。
2. **飞轮收益审计回路**：f1/f2/f3 刚落地，下一步不是新机制而是**验证自进化真的赚钱**——ExperimentLedger 归因链数据回流，出第一份「自优化收益周报」（哪些行为阈值改动带来可测收益/回滚）。
3. **fork/revert + /share 用户面**：后端全在，补 `/fork <tag>` `/revert <ordinal>` `/share` 三条命令即可关闭差距#3（半天工作量，收益直接可见）。

**P1（两周）——纯度收尾**
4. **loop_body 586→~200 行**：把治理逻辑（journal/路由/校正）从内联改为 hooks seam 调用，循环体只留 `provider.complete → tool_exec → check_stop` 骨架；验收=注入 fake hooks 全离线回归覆盖现有行为。
5. **引擎循环双代热更**：依赖 4 完成；plugin/TUI 两层蓝绿语义已成熟，直接复用到 loop 代际切换——关闭 15 家对比中最后一项「真落后」。
6. **cc 式沙箱网络 allowlist**：补 Bash 网络维约束，与审批矩阵 sandbox_mode 正交接线。
7. **跨 provider 切换适配实测**：system prompt/工具集差量在 pin 切换时的前缀缓存影响测试。

**P2（一个月）——B 路线物理化**
8. **族协议标准化**：proj_agent_gateway 已能 headless 调度 opencode/crush/atomcode（`server.py` agent_invoke + 失败改派链 opencode→crush）——把跨 harness 的「消息/回执（GOAL 两值）/审计（rollout 事件）」三格式抽成公开协议草案，让「族员」不只有自家 harness。
9. **SQLite 事实源**（Orca 桌面=事实源模型）：会话/任务/账本三库合一查询面，为双端（TUI=方向盘）铺路。
10. **多 harness 投影**（codex-host 保真投影）：lc 治理层（审批矩阵/配额/台账）作为可挂载中间件输出给族员 harness——C 路线的具体形态，反哺 B。

**一句话**：09-21 的「补债阶段」已结束（8 弱点关 7.5）；下一阶段主题切换为 **「转正（门禁吃透）→ 纯化（循环 586→200 + 引擎热更）→ 开放（族协议标准化）」**，战略终局判断不变：B 路线 Agent 编队操作系统，且本轮 fan_out + gateway + worktree 三件套已让其具备物理雏形。
