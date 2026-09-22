# opencode「四件套」提议 vs lc 现状差距分析（灵克视角，2026-09-22）

## A. 摘要

opencode 提出「假说生成器 → 安全实验沙箱 → 影子评估 → 固化器」四件套 + StateStore 唯一事实源，是 **完整架构蓝图**（Phase 0-4 总 39 天）。本文做**真读对账**：哪些是已经具备的依赖、哪些是新概念、哪些是隐藏前置必须先解决。

**核心结论**：
- **60% 是新基建**（Phase 1-4）
- **40% 是已经具备的依赖**（StateStore 原语 / SeamRegistry hot_swap）
- **5 个隐藏前置** opencode 没明确：正样本通路不存在 / 5 DB 并存未给合并策略 / N4 名字冲突 / 流量分发器缺 / 自动改码权限需升格 H17
- **合成版落地顺序**：先做 20260922 飞轮诊断的 3 个切片（2.5 天）打地基 → 再上 opencode 的 Phase 0-4

证据分级：✅实测（file:line + 命令输出）/ 🟡推测 / 🔴幻觉。

---

## B. opencode 提议的骨架（原文要点）

| 件套 | 产出文件 | 核心机制 |
|---|---|---|
| **假说生成器** | `lingclaude/core/hypothesis_generator.py` | 从正负样本 → `HypothesisRecord {signal, target_seam, current_config, proposed_change, expected_delta, confidence, risk_level, negative_examples}` |
| **安全实验沙箱** | `lingclaude/core/experiment_sandbox.py` | `ExperimentMode.SHADOW`（镜像）/ `CANARY`（1%/5%/10%）/ `A_B`；自动回滚触发器 |
| **影子评估器** | `lingclaude/core/shadow_evaluator.py` | t-test / Mann-Whitney U + Bonferroni 校正 + Cohen's d；`Verdict.ADOPT/REJECT/INCONCLUSIVE` |
| **固化器 / 回滚器** | `lingclaude/core/committer.py` | ADOPT → 改 WiringManifest + git commit + `SeamRegistry.reload()` + StateStore 写 `CONFIG_ADOPT` transition；rollback 反演 |
| **StateStore 扩展** | `lingclaude/core/state_store_ext.py` | `FlywheelRecordType` 枚举（POSITIVE/NEGATIVE_SAMPLE / HYPOTHESIS / EXPERIMENT / EXPERIMENT_METRIC / EVALUATION / CONFIG_ADOPT / CONFIG_ROLLBACK） |

---

## C. 5 项前置核实（真读对账）

### C.1 StateStore 原语 —— ✅ 已具备

**opencode 提**：「StateStore (2T3A) 唯一事实源」
**实测**：`lingclaude/core/state_store.py:50/53/85/90/158/177/197/233/248` 已有完整原语：
- `:50 save(record_type, key, payload)`
- `:53 load(record_type, key)`
- `:85 save` (JSON backend)
- `:90 load` (JSON backend)
- `:158 async save`
- `:177 async load`
- `:197 class StateStore`
- `:233 save` (统一接口)
- `:248 load` (统一接口)

**判定**：opencode 的 `state_store_ext.py` 不需要重建，只需在已有原语上加 `FlywheelRecordType` 字符串枚举。**0 工时基建**。

### C.2 「正样本通路」 —— 🔴 完全不存在

**opencode 提**：「正负样本各 >100」
**实测**：`grep -rn "positive_sample\|POSITIVE_SAMPLE\|成功案例" lingclaude/ --include="*.py"` **全仓零命中**。

现有 `lingclaude/core/data_flywheel.py` 只有两个写入函数：
- `:109 log_error(ErrorPattern)` —— 错误样本
- `:133 log_correction(CorrectionEntry)` —— 修正样本

**没有正样本采集**。Phase 0「数据就绪」必须先加 `log_success()` 函数 + 触发点（建议：`finish_reason=="stop"` 且无 tool_error 时记录）。

### C.3 N4 守卫 —— 🔴 名字冲突 + 概念新

**opencode 提**：「N4 守卫：flywheel 相关操作未写 StateStore → 红」
**实测**：`docs/LINGYUAN_IRON_LAW.md:135/154/188/390` 已定义 N4，但语义是：

> N4 三查（铁律 8 升格）：缺席查（absent 一致性）/ 互斥查（同路径双活锁即警）/ 时效查（过期 work_claim 即提醒夺锁）

**判定**：
- N4 是关于**操作域一致性 + work_claim 互斥**，与"飞轮写入守卫"语义不同
- opencode 提议的内容是新概念，要新建守卫实现
- **不能叫 N4**（避免与现有铁律冲突）；建议叫 **N9** 或 **N10**（按铁律 8 现状编号决定）

### C.4 流量分发器 —— 🟡 缺一层胶水

**opencode 提**：「流量分发器按百分比分流（SHADOW: 镜像；CANARY: 1%/5%/10%）」
**实测**：
- `lingclaude/core/plugin_lifecycle.py:286-348` 已有 `hot_swap()` 蓝绿切换（六态状态机 + 蓝绿热更）
- 但**没有"按百分比切流"机制**——只有熔断/路由（`task_router`）

**判定**：
- SHADOW 模式可以做（注册 variant + 镜像调用钩子）
- CANARY 模式需要新加流量分发层（按 ratio 切分请求）
- 🟡 工作量估算：1-2 周新加流量分发原语

### C.5 DB 并存 —— 🔴 隐藏大坑

**opencode 提**：「StateStore 唯一事实源」
**实测**：实际 5 个 DB 并存：
- `.lingclaude/data_flywheel.db`（7 MB，今日活跃）
- `.lingclaude/knowledge.db`（14.9 MB，daemon 实际读写）
- `.lingclaude/metrics.db`（daemon 写，trigger 不读）
- `lingbus.db`（家族消息总线）
- `lingmemory.db`（LingMemory 框架）

外加 `data/arch_ledger/` 走 JSON StateStore 隔离目录（arch_debt / arch_exemption / arch_audit_task / arch_audit_state / arch_m6_snapshot / arch_law_revision / arch_review）。

**判定**：
- opencode 说"统一 StateStore"，但没说 DB 合并策略
- 5 DB 收编到 1 StateStore = 5 周工作量（schema 迁移 + 双写期 + 切换 + 旧 DB 退役）
- 不收编 = 跨 DB 假说生成无法做（飞轮的核心读路径要 join）

🔴 **必须先做 DB 合并策略 3 选 1 决策**，否则 Phase 1-4 的"假说生成器"会发现读不到负样本。

---

## D. 3 个与 opencode 不同的判断

### 判断 ①：不要把 opencode 提议当 MVP 直接落地

**理由**：
- Phase 0-4 总 39 天 ≈ 2 个月
- 期间 lc 还在持续演进：当前工作树有 24 文件 +423/-32 未提交（20260921 双会话对账 §3）
- opencode 的"StateStore 唯一事实源"假设是冻结态，但 lc 是滚动演进态
- **先做 20260922 飞轮诊断的 3 个切片（2.5 天），让飞轮先跑起来一半，比直接做完整蓝图更现实**

### 判断 ②：不要把 N4 名字复用给"飞轮写入守卫"

**理由**（见 C.3）：
- N4 在铁律 8 是关于 absent 一致性 + 操作域 work_claim
- 与飞轮写入语义完全不同
- 实现时要叫 N9 或 N10（具体编号看铁律 8 现状决定）

### 判断 ③：「自动改阈值 + 回滚」不是 opencode 想象的那么简单

**理由**：
- `scripts/self_audit_trigger.py:9-10` 明确说"审出→入册→等待，不自动改码"（2026-09-17 用户裁定）
- opencode 的 `Committer.adopt()` 直接改 `WiringManifest` 看似合理，但**触发"自动改码"权限等级需要先升格 H17 协议层**
- `docs/research/20260921_coding_agent_expansion.md` §9 追记已指出："H17 仍是文档级自觉，升格为不可绕过的协议层"
- 否则一旦误伤，回滚不是技术问题，是治理问题（铁律 8 操作域边界）

---

## E. 合成版落地顺序（4 阶段，总 6 周）

### 阶段 0（地基，2.5 天）= 20260922 飞轮诊断的 3 个切片

| 切片 | 工作量 | 验收 |
|---|---|---|
| **P0 #1**：datalog aggregator → m6_snapshot → 接 audit trigger | 1 天 | 连续 3 天产出 snapshot；fingerprints 命中 |
| **P0 #2**：DataFlywheel top-N → KB 跨会话规则 | 1 天 | system_prompt_builder 拿到针对性提示 |
| **P1 #3**：audit_task 自动入 daemon 触发 | 半天 | 4 条 open task 至少 1 次进 daemon context |

**给 Phase 0 准备的条件**：所有写入路径都明确（谁写哪、写什么、有没有 reader）。

### 阶段 1（opencode Phase 0 重做，5-8 天）= 数据真就绪

- **新建正样本通路**：`scripts/collect_positive_samples.py` + `data_flywheel.py` 加 `log_success()` 函数
- **StateStore 扩展**：`lingclaude/core/state_store_ext.py:FlywheelRecordType` 枚举
- **DB 合并策略 3 选 1**（关键决策点，需用户裁定）：
  - **A. 全收编**：5 DB → 1 StateStore（最纯净，5 周工作量）
  - **B. 虚拟视图层**：StateStore 映射 5 DB（中等，2-3 周）
  - **C. 最小收编**：只把飞轮相关 record 走 StateStore（最小，1 周）

### 阶段 2（opencode Phase 1，5 天）= 假说生成器

- `lingclaude/core/hypothesis_generator.py`（opencode 已给骨架）
- **N9 守卫**（不叫 N4）：flywheel 操作未写 StateStore → 红
- **负例库必须落实**：`HypothesisGenerator.__init__` 加载 `HYPOTHESIS_NEGATIVE`，新假说必过负例库过滤

### 阶段 3（opencode Phase 2-3，17 天）= 实验 + 评估 + 固化

- **SHADOW 先行**：复用 `plugin_lifecycle.py:286 hot_swap` + 新加镜像调用钩子
- 评估器接 `daemon.py:189 build_context` 的 `experience_hints` 通路
- 固化器改 `wiring.py` / `router_keywords.yaml` 后调 `SeamRegistry.reload()`

### 阶段 4（opencode Phase 4，14 天）= Canary + 自动化

- **新建流量分发器**（按 1%/5%/10% 切流）——opencode 没明说但真缺的胶水
- `daemon.run_cycle` 加自动触发（24h 节流改为 trigger 类分级）
- **周均采纳 ≥1 个优化**的验收标准需要先在 SHADOW 阶段跑了 2 周确认无误伤再上

---

## F. 立即可执行的下一步（不混答案）

```
# 选项 A（半天）：跑 P0 #1 datalog aggregator 雏形
#   - scripts/datalog_aggregator.py 写出第一份 m6_snapshot
#   - self_audit_trigger.py:47 LEDGER_TYPES 加入 "arch_m6_snapshot"
#   - 给出真实 snapshot 长什么样

# 选项 B（1 小时）：提个 issue/留 hook: DB 合并策略 3 选 1（A/B/C），等用户裁定
#   - 不能跳过：opencode Phase 0 的真实工作量取决于这个选择
#   - 在 docs/research/ 加 §G 决策记录

# 选项 C（1 小时）：在 lingclaude/core/ 起 state_store_ext.py 雏形
#   - FlywheelRecordType 枚举先就位
#   - 后续阶段写 record 时直接可用
```

---

## G. 溯源与证据

- **本仓对照点**：
  - `lingclaude/core/state_store.py:50-248`（StateStore 原语实证）
  - `lingclaude/core/data_flywheel.py:109/133`（错误 + 修正样本，无正样本）
  - `lingclaude/core/plugin_lifecycle.py:286-348`（hot_swap 蓝绿切换，无流量分发）
  - `lingclaude/core/datalog.py:23-145`（datalog 写入实证）
  - `docs/LINGYUAN_IRON_LAW.md:135/154/188/390`（N4 守卫定义实证）
  - `scripts/self_audit_trigger.py:9-10`（"不自动改码"政策实证）
  - `lingclaude/self_optimizer/daemon.py:189/249/610`（daemon 周期路径）
- **关联文档**：
  - `docs/research/20260922_flywheel_closure_diagnosis.md`（本仓飞轮现状诊断，三片切方案）
  - `docs/research/20260920_peer_harness_borrowing.md` §11（hot_assign 蓝绿切换落地，1cb8208）
  - `docs/research/20260921_coding_agent_expansion.md` §9（H17 协议层缺位追记）
  - `docs/research/20260921_dual_session_reconciliation.md` §3（24 文件 +423/-32 未提交，前置 commit 风险）
- **调研纪律**：所有论断按 `lingclaude-true-read` 三件套审计（组件 + 触发条件 + 数据流）；未走"看 commit message 推断"反模式；opencode 提议的 5 项前置均已 grep 实测
- **下一步决策点**：DB 合并策略 A/B/C（用户裁定）——这是 opencode Phase 0 真实工作量的唯一阻塞