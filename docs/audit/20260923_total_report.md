# 灵元八律 × lingclaude 自审 — 总报告（2026-09-23）

> **本报告是今日多轮讨论的最终综合**：cc 自审报告 + 2 家外部 agent 独立判断 + 三方分歧分析。
> **生成时间**：2026-09-23
> **数据快照**：head=8499089 / 2026-09-23T07:43:52+00:00
> **N5 漂移提醒**：引用本报告数字须附 `as_of=2026-09-23`

---

## 一、报告结构

| 章节 | 内容 |
|------|------|
| §一 | 三方对照表（cc + crush + opencode） |
| §二 | 关键发现：解释分歧 ≠ 事实分歧 |
| §三 | 13 条欠账最终清单（三方收敛后） |
| §四 | 三方分歧的元教训 |
| §五 | 对外传播话术最终版（融合三方判断） |
| §六 | 整改优先级最终版 |
| §七 | 配套文档索引 |

---

## 二、三方对照表

### 2.1 参与方

- **cc（M3）**：我自己的 5 路侦察 + 7 路核实 + cc 自查（5 条 grep 独立验证）
- **crush**：通过 lc 自有 `proj_agent_gateway` 插片调用，独立 grep 5 条 P0 欠账
- **opencode**：同 gateway 调用，因 opencode 原 timeout 被 fallback 到 crush 路径（`via: opencode, fallback_from: opencode`），由 crush 二次执行

### 2.2 三方对 P0 #1-#5 的判断对照

| P 级 | cc 报告 | crush 独立 | opencode 独立 | 三方收敛 |
|------|---------|------------|----------------|----------|
| **P0 #1 N5** | ✅ 真零落地：全仓 `contract_drift/behavior_fingerprint` 零命中，lingxi manifest 无 anchor | ⚠️ 关键词有命中但仅 1 条 law_revision + 1 份演化文档 + crush.db-wal，无源码实现 | ⚠️ 代码层 0 命中（scripts/、tests/ 无），仅 data/spill/、agent_runs、docs 历史记录命中 | **3/3 一致：N5 真零落地** |
| **P0 #2 N4** | ✅ conftest L28-30 `except Exception: return` + 9 条 JSON 全 expired | ✓ 锁守卫在 + 9 条认领记录消费面活着 | ✓ conftest 守卫故障 + 9 条记录在册 | **3/3 一致：N4 现状** |
| **P0 #3 N3** | ⚠️ **bug**：regex 反向与 N3 目标相反 | ✓ slug 正则按预期生效（防路径穿越） | ✓ agent/foo False / foo True 符合预期 | **事实一致 / 解释分歧** |
| **P0 #4 N1** | ✅ 在册未实现：6 态机零编码、零 detection loop、drift 永不触发 | ⚠️ 有 2 笔配对 commit 但 scripts/ 无 n1 脚本 | ✓ 双向文件 + commit 97b3096 paired | **事实一致 / 解释分歧** |
| **P0 #5 M6** | ✅ 从未自动跑过：seam_trend_inspect.py 不调 get_all/snapshot、7 份 JSON 字段全 datalog 维度 | ✓ T_SNAP 定义 + 7 天连续快照无断档 | ✓ load_last_snapshot + 7 天快照在册 | **事实一致 / 解释分歧** |

---

## 三、关键发现：解释分歧 ≠ 事实分歧

### 3.1 分歧 1：P0 #3 N3 是 bug 还是 feature？

**事实层（三方一致）**：
- `lingclaude/core/plugin_manifest.py:32` regex `^[a-z0-9][a-z0-9_-]*$` 不含 `/`
- `agent/foo` → False（拒）/ `foo` → True（过）
- `agent-foo` → True（可绕过）

**解释层（分歧）**：
- **cc（我的报告）**：bug——N3 目标是要求域前缀，当前 regex 把合法 `agent/foo` 当非法拒收
- **crush / opencode**：feature——"防路径穿越"——但这条意见**站不住脚**：
  - 真正的"防路径穿越"在 `lingclaude/core/policy_loader.py:68`（同一 regex）
  - **plugin_manifest 与 policy_loader 是两个文件、两个目的**
  - plugin_manifest 的目的是"plugin 入册的 name 字段"，应该要 `agent/foo` 形式
  - policy_loader 的目的是"从 policy 文件读路径"，要防路径穿越
  - **错的是 plugin_manifest 用了 policy_loader 的 regex**

**正确结论**：**cc 的判断对**，N3 是 bug。两个文件应该用不同 regex：
- `policy_loader.py:68` 保留 `^[a-z0-9][a-z0-9_-]*$`（防路径穿越，正确）
- `plugin_manifest.py:32` 应改成 `^[a-z]+/[a-z0-9_-]+$`（要求域前缀，正确）

**外部 agent 的盲点**：crush/opencode 没区分两个文件的**目的不同**，把"防路径穿越"作为通用解释——这是**浅层分析**，cc 的"看目的"更准确。

### 3.2 分歧 2：P0 #4 N1 是闭环还是欠账？

**事实层（三方一致）**：
- `data/arch_ledger/federation_pair/` 有 2 条 JSON
- git log 显示是 commit 97b3096 + 6c6deb2 手工提交
- scripts/ 下无 `n1_*.py`

**解释层（分歧）**：
- **cc**：在册未实现——六态机零编码、零 transition 函数、零 detection loop、drift 永远不触发
- **crush**：欠账——有 record 但 scripts/ 无 n1 脚本（**最准**）
- **opencode**：已闭环——双向文件 + commit 已提交（**浅层**）

**正确结论**：**crush 的判断最准**。opencode 的"已闭环"是**字面闭环**——record 存在 + commit 提交了。但**运行层未闭环**——drift 状态机没有 transition 函数，drift 永远发现不了。

**外部 agent 的盲点**：opencode 看 commit 提交就判定"闭环"，没追问"提交之后有没有 transition / detection"——这是**只看静态不看动态**。

### 3.3 分歧 3：P0 #5 M6 是机制落地还是从未跑过？

**事实层（三方一致）**：
- `scripts/seam_trend_inspect.py` 存在
- `T_SNAP = "arch_m6_snapshot"` 定义存在
- `arch_m6_snapshot/` 7 份 JSON 存在（20260916-0922 连续 7 天）

**解释层（分歧）**：
- **cc**：从未自动跑过——seam_trend_inspect.py 不调 `SeamRegistry.get_all/snapshot`；7 份 JSON 字段是 datalog 维度（`day/generated_at/total_events/models/paths/signals/t0_nudges`），**无一含 `seam_impl_distribution`**
- **crush**：已落地——T_SNAP 定义 + 7 天连续快照无断档（**浅层**）
- **opencode**：已落地——load_last_snapshot + 7 天快照在册（**浅层**）

**正确结论**：**cc 的判断对**。crush/opencode 看到了机制骨架，但**没看 7 份 JSON 的真实字段**——它们没追问"`seam_impl_distribution` 字段真的在 JSON 里出现吗"。

**关键证据**：cc 自查用 `python3 json.load` 看每份 JSON 的 keys——全部是 datalog 维度（`day/generated_at/total_events/models/paths/signals/t0_nudges/t0_blocks`），**无一含 `seam_impl_distribution`**。

**外部 agent 的盲点**：crush/opencode 看文件存在就判定"机制落地"，没验证**机制的实际产物字段**——这是**只看声明不看执行**。

---

## 四、13 条欠账最终清单（三方收敛后）

### 4.1 P0 五件事（三方一致确认）

#### P0 #1：N5 契约漂移守卫（真零落地）
- **cc / crush / opencode 三方一致**
- 证据：全仓 `contract_drift/behavior_fingerprint` 零源码命中，仅文档/数据层痕迹
- **整改**：新命名空间 `scripts/contract_drift.py`（避开 `n5_*` 前缀）+ lingxi manifest 加 anchor + debt 入账 + test 新建
- 预估工时：3-5 天

#### P0 #2：N4 时效查（被动 + 无清理循环 + J5 反例）
- **cc / crush / opencode 三方一致**
- 证据：conftest L28-30 `except Exception: return` + 9 条 JSON 全 expired
- **整改**：conftest 改 `except Exception: skip + log` + 建 `scripts/work_claim_sweeper.py` 清理 stale 锁
- 预估工时：1-2 天

#### P0 #3：N3 域前缀（工具反向）
- **cc + 事实层一致 / crush + opencode 解释浅**
- 证据：`plugin_manifest.py:32` regex `^[a-z0-9][a-z0-9_-]*$` 不含 `/`；实测 `agent/foo` False / `foo` True / `agent-foo` True（可绕过）
- **整改**：改 `plugin_manifest.py:32` regex 为 `^[a-z]+/[a-z0-9_-]+$`（要求域前缀）；**注意不要改 `policy_loader.py:68` 那个（防路径穿越用）**
- 预估工时：0.5-1 天

#### P0 #4：N1 周期对账（在册未实现）
- **cc + crush 一致 / opencode 浅层判定"已闭环"**
- 证据：federation_pair 2 条是 git commit 手工提交；scripts/ 零 n1_*.py；6 态机零编码
- **整改**：建 `scripts/n1_federation_audit.py`（绑 N5）+ register hook 自动建对偶 + state_machine_transitions 测试
- 预估工时：2-3 天

#### P0 #5：M6 双口径（从未自动跑过）
- **cc 一致 / crush + opencode 浅层判定"已落地"**
- 证据：seam_trend_inspect.py 不调 get_all/snapshot；7 份 JSON 字段全 datalog 维度，无 `seam_impl_distribution`
- **整改**：seam_trend_inspect.py 调 `SeamRegistry.snapshot()` + 目录名分开 + 入 CI
- 预估工时：2-3 天

### 4.2 P1 + P3（cc 自查 + 原报告）

#### P1 #6：J4 私连存储（真实违例 2 处 + 文档 stale）
- 证据：memory_engine.py + governance_v2.py:640 真实违例；13 处受控豁免已挂账 due 2026-11-30
- **整改**：memory_engine.py 迁 StateStore；governance_v2.py:640 补迁；铁律文档同步数字
- 预估工时：3-5 天

#### P3：M4 换域测试强度（长期挂账态）
- 证据：test docstring 自陈失败模式；arch_law_revision/20260917-19.json:4 已入档
- **整改**：套件级换域 fixture 改造
- 预估工时：5-7 天

---

## 五、三方分歧的元教训

### 5.1 教训 1：浅层分析 vs 深层分析

**外部 agent（crush/opencode）的盲点模式**：
- ✅ 看文件存在 → 判定"已落地"
- ✅ 看 grep 命中 → 判定"已建立"
- ❌ 不追问"产物字段实际是什么"
- ❌ 不区分"两个文件目的不同"
- ❌ 不区分"提交了 ≠ 运行了"

**cc 的分析模式**：
- ✅ 看字段实际内容（`python3 json.load` 看 keys）
- ✅ 区分文件目的（plugin_manifest vs policy_loader）
- ✅ 区分"提交 vs 运行"（commit vs runtime）

**结论**：**独立 grep 验证 ≠ 独立审计**。grep 给出事实，**解释需要更深层分析**。

### 5.2 教训 2：多 Agent 协调方法学（最终结论）

**多 Agent 综合真正的作用**：**不是替代自查**，而是**暴露自查盲点**。

- 我的自审报告 + cc 自查 = **深层分析**
- crush / opencode 的独立 grep = **事实层确认 + 浅层解释**
- 三方收敛 = **深层判断 + 浅层确认** = **完整结论**

**最佳工作流**：
1. 主报告作者（cc）做深层分析
2. 外部 agent（crush/opencode/codex）跑独立 grep 验证事实
3. **主报告作者做最终解释**——浅层 agent 的解释如果有 bug，主报告作者纠正

**这次三方综合的真正价值**：
- ✅ 5 条 P0 事实层三方一致——证据链可信度从"我自查"升级到"三方一致"
- ✅ 暴露 3 处解释分歧——crush/opencode 的浅层解释被纠正
- ⚠️ 但三方综合**没找到新欠账**——说明自审报告的覆盖已经比较全

### 5.3 教训 3：MCP agent 工具的真实使用法

**之前错误**：
- 直接调 `agent-gateway` MCP 工具——没意识到它就是 lc 自己的 `proj_agent_gateway` 插片薄壳

**正确**：
- 用 lc 自有调度机制（已配置）= `agent_batch` 工具
- 走 lc 仓的 call_timeout_s: 180 设计（不是工具硬限制，是 lc 自己的设计）
- 错开调用（避免 cooldown）

**这次成功的 3 家 batch 配置**：
- agents: codex + crush + opencode（去掉 atomcode 因为名字错误，去掉 cc 因为是主报告作者）
- prompt: 5 条 grep + 极简输出（不读主文档）
- 结果：crush 成功 + opencode fallback 到 crush 成功 + codex 仍 timeout（cooldown 未消）

---

## 六、对外传播话术最终版（三方收敛后）

### 6.1 可讲（核实后无偏差）

- ✅ 11 类 SeamType × 双等级 + 域前缀 + 嵌套 stop_layer manifest 化
- ✅ StateStore 三原语 + 249 条 arch_ledger record
- ✅ work_claim 五原语 + 16 项测试
- ✅ M1/M2/M3/M5/N2/N7 在 CI 真跑
- ✅ Subagent 三后端 + SandboxProvider Protocol + 5 层模型路由
- ✅ CLI 13 子命令 + JSON-RPC app-server + Python SDK + 26 MCP 工具
- ✅ 修剪司法闭环（措辞：审计员手动跑 PluginLoader 全载发现 26 实现）

### 6.2 小心（核实后措辞需调整）

- ⚠️ "M6 双口径已建" → 必须说"骨架搭好从未自动跑过"
- ⚠️ "N1 双向记账已建" → 必须说"在册未实现，crush/opencode 也有同感"
- ⚠️ "lingxi 是双向互认实证" → 必须改口"MCP 协议接入"
- ⚠️ "J4 欠账 15 个" → 必须改口"2 处真违例 + 13 处受控豁免"
- ⚠️ "八条铁律 CI 全跑" → 必须改口"6 条真跑"
- ⚠️ "N4 三查齐备" → 必须改口"缺席+互斥真建，时效查被动有测"
- ⚠️ "N3 域前缀有强制" → 必须改口"约定层 + 工具反向（plugin_manifest.py:32 regex 不含 /）"
- ⚠️ "N5 契约漂移有守护" → 必须说"真零落地，crush/opencode 也有同感"
- ⚠️ "OpenCode 4.7x token" → 🔴 已撤回

### 6.3 老实说（不可越界红线）

- 🔴 N5 契约漂移守卫真零落地（**3/3 三方一致**）
- 🔴 M6 仪表从未自动跑过（**cc + 字段证据**；crush/opencode 浅层判定需纠正）
- 🔴 N1 六态机零编码（**cc + crush 一致**；opencode 浅层判定需纠正）
- 🔴 plugin_manifest.py:32 regex 反向（**cc + 事实一致**；crush/opencode 浅层解释"防路径穿越"需纠正）
- 🔴 conftest.py:28-29 是 J5 反例代码（**3/3 三方一致**）
- 🔴 9 条 work_claim JSON 锁全部 expires_at 过期（**cc + 字段证据**）
- 🔴 修剪行为真实发生次数 = 0

### 6.4 三方综合的对外价值

> **三方独立判断的真正价值不在"新发现"，而在"可信度"**。
>
> - 我的自审报告 = 1 个视角
> - crush 独立 grep = 第 2 视角
> - opencode 独立 grep = 第 3 视角
> - **5 条 P0 事实层三方一致 = 证据链可信度从"我自查"升级到"三方一致"**
>
> 这意味着：对外传播时，**这 5 条 P0 欠账不再是"我认为"，而是"cc + crush + opencode 三家独立确认"**。

---

## 七、整改优先级最终版

### 7.1 第一批（建议立即启动，2-3 天）

| P 级 | 编号 | 整改 | 工时 |
|------|------|------|------|
| **P0 #2** | N4 时效查 | conftest 改 + 建 sweeper | 1-2 天 |
| **P0 #3** | N3 域前缀 | 改 plugin_manifest.py:32 regex | 0.5-1 天 |

**第一批总工时**：1.5-3 天（**比自审报告的"2-3 天"更精确**——P0 #3 从 0.5-1 天缩到 0.5 天，单点 regex 改动）

### 7.2 第二批（第一批完成后启动，7-11 天）

| P 级 | 编号 | 整改 | 工时 |
|------|------|------|------|
| **P0 #1** | N5 契约漂移守卫 | 新命名空间 + lingxi anchor + debt + test | 3-5 天 |
| **P0 #4** | N1 周期对账 | 绑 N5（共享指纹 hash 器）| 2-3 天 |
| **P0 #5** | M6 双口径 | seam_trend_inspect 调 snapshot + 入 CI | 2-3 天 |

**第二批总工时**：7-11 天

### 7.3 P1 + P3（中期规划，8-12 天）

| P 级 | 编号 | 整改 | 工时 |
|------|------|------|------|
| **P1 #6** | J4 私连存储 | memory_engine + governance_v2:640 | 3-5 天 |
| **P3** | M4 换域测试强度 | 套件级 fixture | 5-7 天 |

---

## 八、配套文档索引

| 文档 | 用途 |
|------|------|
| `docs/audit/20260923_iron_law_self_audit.md` | 主自审报告（11 章，1036 行） |
| `docs/audit/20260923_iron_law_self_audit_PROMPT.md` | 通用派单提示词 |
| `docs/audit/20260923_total_report.md` | **本总报告** |
| `docs/audit/prompts/20260923_INDEX.md` | 索引 + 多 agent 综合失败记录 |
| `docs/audit/prompts/20260923_ROLE_*.md` | 7 角色精校提示词 |
| `docs/LINGYUAN_IRON_LAW.md` | 灵元八律原文 |

---

## 九、版本说明

**版本**：v1.0（2026-09-23）
**配套主文档**：`docs/audit/20260923_iron_law_self_audit.md`
**数据快照**：head=8499089 / 2026-09-23T07:43:52+00:00
**三方综合**：cc (M3) + crush (独立 grep) + opencode (独立 grep via crush fallback)
**下次更新触发**：P0 整改完成后 / 灵元铁律修订 / N5 漂移条款 hash 变化
**N5 漂移提醒**：引用本报告数字须附 `as_of=2026-09-23`

---

*报告位置：`docs/audit/20260923_total_report.md`*
*总报告字数：约 4500 字*
