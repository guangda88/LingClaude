# 自优化/自学习飞轮闭合成都诊断（灵克视角，2026-09-22）

> **codex 修订（2026-09-22）**：应用户要求，以 codex 会话的独立真读结论修订本文。
> 修订原则：**原文档 §A–§H 一字不动**，全部增补集中在 §I（根因增补）/ §J（F0–F4 方案）/ §K（修订记录表），
> 逐条标注与原文的印证 / 补充 / 分歧关系。codex 修订部分沿用原文证据分级：✅实测 / 🟡推测 / 🔴幻觉。

## A. 摘要

lc 仓里**不是没有飞轮**，而是**有四条独立的小飞轮**，其中一条真在转（`KnowledgeBase + DataFlywheel + BehaviorMetrics → system_prompt_builder → next-turn prompt`），其余三条**不同程度的断头**。真正缺的不是新系统，而是把已经存在的 4 条 `arch_audit_task` open 记录、315 KB/天的 `datalog` 写入、7 MB 的 `data_flywheel.db` 接到已经在跑的 `OptimizationDaemon` 上。

**结论先行**（按 ROI 排序）：
1. **datalog 每日 ~300 KB 持续写入却无人读** —— 最大的隐藏 sink，P0
2. **DataFlywheel 7 MB 错误日志只被读一个标量（`recurrence_rate`）** —— pattern 信息沉没，P0
3. **arch_audit_task 4 条 open 未自动消费** —— 大多已走手工闭环，但缺标准化链路，P1
4. **turn_learner 规则一锤子买卖**（session-bound id 永久不变）—— P1 改造点

证据分级按 `lingclaude-true-read` 纪律：✅实测（file:line + 命令输出）/ 🟡推测 / 🔴幻觉。

---

## B. 四条飞轮真实状态（file:line 实证）

| # | 名称 | 写入侧 | 读取侧 | 闭合度 |
|---|---|---|---|---|
| **A** | in-turn self-nudge | `lingclaude/core/tool_call_executor.py:58/110/133 → engine._log_to_flywheel → session_runtime.log_to_flywheel → DataFlywheel.log_error` | `lingclaude/core/system_prompt_builder.py:268` 读 `DataFlywheel.get_stats()` | ✅ 写入活跃（7 MB DB），但**仅 in-session**且只读 1 个标量 |
| **B** | turn-level learning | `lingclaude/core/query_engine_lifecycle_mixin.py:75 → turn_learner.record_turn_learnings → KnowledgeBase.add_rule` | `lingclaude/core/system_prompt_builder.py:288` 读 `KnowledgeBase.search_rules` | ✅ 真读真写，但 **rule_id 嵌 `session_id[:8]`**（`lingclaude/core/turn_learner.py:46`），session-bound |
| **C** | optimization cycle | `lingclaude/self_optimizer/daemon.py:610 _write_cycle_to_knowledge` 写 `opt_cycle_NNNN` 规则，按本轮是否改善 ±0.05（`daemon.py:629-654`） | `lingclaude/self_optimizer/daemon.py:189 build_context` 读 KB 高置信规则作 `experience_hints` | ✅ 真闭合，但 **`lingclaude/cli/repl_turn.py:60 should_run_cycle(min_interval_hours=min_hours)` 24h 节流**，每会话 REPL 结束跑 1 次 |
| **D** | audit-trigger | `scripts/self_audit_trigger.py:176/184/191 file_task()` 写 `arch_audit_task/*.json`（severity/state） | **❌ 无消费者**。"供 SDT 巡检消费"是空头支票（`self_audit_trigger.py:9`），`daemon.trigger.check_all_conditions` 完全不读 audit_task | 🟡 部分闭环：多数 task 被人工 resolve（19 条中 14 resolved / 1 closed / **4 open**），但 daemon 自动消费链路缺位 |

**实测硬数据**（2026-09-22 16:58 时点）：
- `data/arch_ledger/arch_audit_state/default.json` last check `2026-09-22T08:58:43+00:00`
- `arch_audit_task/` 19 条记录状态分布：**14 resolved / 1 closed / 4 open**（`audit-j2-doc-stale-path×2 / ci-unit-job-empty / list-keys-no-direct-test`）
- `arch_debt/` 22 条 open debt（其中 `regression-preexisting-failures-20260921` 等到期未改）
- `.lingclaude/data_flywheel.db` 7 MB（最后改 17:29，活跃写入）
- `.lingclaude/knowledge.db` 14.9 MB（被 daemon 实际读写）
- `~/.lingclaude/datalog/2026-09-22.jsonl` 315 KB（**今天 17:33 还在写入**）

### B.1 静默 sink（无读取侧）

- **E. telemetry datalog**（`lingclaude/core/datalog.py:23-145`） — `log_l5_audit` / `log_t0_behavior` / `log_degradation_alert` / `log_model_call` 4 个函数；全仓 `grep from lingclaude.core.datalog` 只有 `lingclaude/model/openai_provider.py:16` 一处写，**零读**。每日 ~300 KB 数据沉没。
- **F. arch_debt / arch_exemption**（`scripts/arch_ledger.py:60/86`） — 唯一被消费的是 `self_audit_trigger.py:181 expired_debts()`，只查过期；22 条 open debt 没有自动清偿路径。
- **G. arch_m6_snapshot**（`arch_ledger.py:42 T_SNAP`） — record type 已定义但**全仓零写入**——快照机制只是空壳。

---

## C. 三个让飞轮"不转"的结构断点

### 断点 ①：arch_audit_task 消费链路半断

- `self_audit_trigger.py:9-10` 明确说 "审出→入册→等待，不自动改码"（2026-09-17 用户裁定）
- `lingclaude/cli/repl_turn.py:24-94` 的 `OptimizationDaemon` 周期**完全不看 `arch_audit_task`**
- `lingclaude/self_optimizer/trigger.py:20-288` 的 `check_all_conditions` 只走 metrics + behavior snapshot 的 7 个条件，**信号正交于 audit_task**
- 后果：4 条 open task 静静 open，没有 ad-hoc 入口让 daemon 接管

🟡 推测：这是"审计权限 vs 自动执行权限"的有意切分，但缺少"audit → daemon 触发器"的桥接组件。SDT 巡检是文档里的占位符，没有落地为 scheduler。

### 断点 ②：DataFlywheel → 自优化器没有桥

- `system_prompt_builder.py:268-277` 读 `DataFlywheel.get_stats()` 仅看 `recurrence_rate` 一个标量
- `data_flywheel.py:155 get_recurring_errors()` 可返回 top-20 模式（pattern_type/file/error），但**全仓零调用**
- `daemon.py:189 build_context` 完全不查 DataFlywheel，只查 KB
- 后果：7 MB 错误日志里的 pattern 信息（tool_name 维度、file_path 维度）**没有任何回路使用**——只有"错误率"这 1 个信号被消费

### 断点 ③：datalog 写不止、读不归

- `datalog.py:110-145 log_model_call()` 写 `cost.in/out/cached` 到 JSONL
- `datalog.py:42-89 log_l5_audit` / `log_t0_behavior` / `log_degradation_alert` 同样只写
- 全仓 `grep from lingclaude.core.datalog` 零 reader
- 后果：H17 闭环（"账目 ✅ 必须来自仪表盘"）的"仪表盘"半边实为空头支票——`scripts/update_audit_ledger.py` 只生成总账 AUTO-VERIFY 区，并不消费 datalog

### 加分断点 ④（后台 agent 揭出，比我之前理解更严重）

- `turn_learner.py:46` 规则 ID = `f"hallucination_turn_{turn_num}_{session_id[:8]}"`，session-bound
- `grep -rn "update_rule_status"` 全仓只有 3 个调用点，全在 `daemon.py:633` 的 opt_cycle 强化回路
- turn_learner 的 `hallucination_turn_*` / `correction_turn_*` 规则**写后无人改频率/置信度**——永久死规则 id
- 与 `daemon._write_cycle_to_knowledge` 写的 `opt_cycle_*` 不交叉：两条规则流各自为政

---

## D. 三条最小可执行的改造路径（按 ROI 重排）

> **修订说明**：初版优先级把 audit_task 消费列为 P0，但实测发现 14/19 已 resolved，所以 audit_task 降到 P1。**datalog aggregator 升为新 P0 #1**，因为它是最大的隐藏 sink（每天 300 KB 数据沉没）。

### 新 P0 #1：datalog aggregator → m6_snapshot → 接 audit trigger（1 天）

**改动面**：
1. 新建 `scripts/datalog_aggregator.py`：每天 0:00（cron 触发）扫 `~/.lingclaude/datalog/*.jsonl`，按 model/path/cached_pct 聚合，输出 `data/arch_ledger/arch_m6_snapshot/<yyyymmdd>.json`
2. 把 `arch_m6_snapshot` 加入 `self_audit_trigger.py:47 LEDGER_TYPES` 列表——让"datalog 写出新 snapshot"也算 fingerprint 变化 → 触发返审
3. 7 天滚动删除（仿 `daemon.py:605 len(self.state.cycles) > 100` 的滚动策略）

**关键挂接点**：
- 读取：`arch_ledger.py:42 T_SNAP` 已定义 record type，只缺写入
- 触发：`self_audit_trigger.py:47 LEDGER_TYPES = ["arch_debt", "arch_exemption"]` 加 `"arch_m6_snapshot"`
- 监控：cache_pct 跨日趋势回落 → 自动 trigger 返审

**验收标准**：
- 连续 3 天，每天 0:00 都有一份 `<yyyymmdd>.json` snapshot 被产出
- 下一次 self_audit 触发时能在 fingerprints 字典里看到 `arch_m6_snapshot:<date>` 条目
- snapshot 文件总大小受 7 天滚动约束（不无限膨胀）

### 新 P0 #2：DataFlywheel top-N patterns → KB 跨会话规则（1 天）

**改动面**：
1. 新建 `scripts/flywheel_aggregator.py`：扫 `data_flywheel.db` 按 `pattern_type` group，输出 top-N 模式到 KB
2. rule_id 用 `flywheel_pattern_<type>_<yyyymmdd>` 前缀；仿 `daemon.py:656-673` 的 LearnedRule 构造
3. 修改 `daemon.py:200` 的 `r.id.startswith("opt_cycle_")` 为 `r.id.startswith(("opt_cycle_", "flywheel_pattern_"))`

**关键挂接点**：
- 写入：`lingclaude/self_optimizer/learner/knowledge.py:add_rule`（已存在）
- 读取：`daemon.py:189-216 build_context` 的 experience_hints 段

**验收标准**：
- 跑一次 aggregator 后，`system_prompt_builder.py:266-276` 区域能拿到"top-1 模式 X (N 次)"的针对性提示，而不是"复发率 50%"的总览
- 下一轮 daemon 的 `experience_hints` 包含 `flywheel_pattern_*` 类规则

### 新 P1 #3：audit_task 自动入 daemon 触发（半天）

**改动面**：
1. 在 `daemon.py:249 run_cycle` 或 `daemon.py:189 build_context` 加一步：读 `arch_audit_task/*.json` 中 `state=="open"` 的条目，把 severity==P0 的塞进 `trigger_info` 或 context dict
2. 在 `arch_audit_task/<slug>.json` 加 `last_consumed_at` 字段避免每轮重读全表
3. `repl_turn.py:60` 的 24h 节流改为"trigger 类分级节流"（P0 任务不节流，P3 行为指标 24h 节流）

**伪代码骨架**（不写实代码，先 review）：
```
# build_context 末尾，daemon.py:217 之前
open_tasks = [t for slug, t in iter_records("arch_audit_task") if t.get("state") == "open"]
ctx["audit_tasks"] = [{"slug": slug, "severity": t["severity"], "finding": t["finding"]}
                       for slug, t in open_tasks]
```

**验收标准**：
- 4 条 open task 至少 1 次进 daemon context
- 至少 1 条 P0 task 在 daemon 跑完后 state→resolved（要么真修，要么入 debt）

---

## E. 三个 anti-pattern 警告

🔴 **不要引入 ML/LLM-as-judge 来"做学习"**。当前 `daemon.py:629-654` 的 ±0.05 置信度强化是 AgentEvolver Self-Navigating 的简化版，**整个因果链路清晰可追溯**：rule_id → cycle_id → trigger_type → before/after violations。任何引入 LLM 评估或 fine-tuning 的方案都会让结果不可追溯，等于把飞轮糊掉。

🟡 **不要试图把 arch_audit_task 喂给"AI 自动改码"**。`self_audit_trigger.py:9-10` 的"审出→入册→等待"是 2026-09-17 用户裁定的，是制度不是 bug。20260921 harness 文档 §9 也明确指出 "H17 仍是文档级自觉，升格为不可绕过的协议层"——先做协议层再说自动修复，否则就是让飞轮失去"自觉性"基础。

✅ **保留 session 边界语义，但加跨会话规则去重**。`turn_learner.py:46` 的 session-bound rule ID 不必改写——`daemon.py:629-654` 的"同类规则频次+1，置信度±0.05"已经是正确的跨会话形态，**只需扩大它的读源**（加 DataFlywheel + datalog 聚合），不需要重新设计 KB schema。

---

## F. 一个必须前置的真依赖（来自 20260921 harness 文档 §3.1）

`lingclaude/engine/loop/hooks.py:7-19` 显式说 flywheel / 路由 slot / 幻觉闭环让循环与引擎状态纠缠。所以 P0 #1 / P0 #2 / P1 #3 三条改造**单独可执行**，但都隐含一个前置：20260921 文档 §3.2 第 0 步「循环纯化（小切口）」，否则 hot_swap/fork 被锁，飞轮的"快速演进能力"无法上线。

好消息：**P0 #1 / P0 #2 / P1 #3 都不依赖循环纯化**（都是 aggregator + KB read/write 纯数据流），所以这三条改造可以**今天就开工**，第 0 步是另一条独立 P2 任务。

---

## G. 三个未解决问题的诚实清单

1. **MetricsStore 自产自销**：`daemon.py:740 _record_quality_to_metrics` 写 `metrics.db`，但 `trigger.py:24-64` 只读 context dict（来自 `build_context`），**不查 metrics.db**——`grep "MetricsStore\|metrics\.db" lingclaude/self_optimizer/` 仅 `daemon.py:742/744`（自循环写）。🟡 推测是设计选择（context 解耦 metrics），但确认缺位。
2. **optimizer.py 是真优化还是 placeholder**：120 行是真的，比"3.8 KB placeholder"稍大。但 `SynchronousOptimizer.optimize` 是同步走完整个解空间的硬循环，**不是学习/搜索算法**——所以"优化"是真的在跑，只是没有真正的搜索智能。证据：`wc -l optimizer.py` = 120。🟡 推测"够用但非学习型"。
3. **H17 协议层缺位**：20260921 §9 指出 "H17 仍是文档级自觉"。任何把 audit_task 接入自动执行的方案都需要先升级 H17 到协议层。🟡 推测：H17 协议层可放在 `lingclaude/core/evidence_protocol.py`（已存在同名文件，但需读全文确认是否实现）。

---

## H. 溯源与证据

- **数据文件**：`data/arch_ledger/arch_audit_state/default.json`（2026-09-22 时点）；`data/arch_ledger/arch_audit_task/*.json`（19 条）
- **代码引用**：所有 file:line 均按 lc 仓 master HEAD `f8a338f` 当时的实测路径
- **后台三方调研**：Explore agent 揭出"turn_learner 规则永久死 id"、"MetricsStore 自产自销"、"datalog 每日 300 KB 写入但零读"——均已本机复核
- **关联文档**：
  - `docs/research/20260920_peer_harness_borrowing.md` §11（自进化三底线未落地）
  - `docs/research/20260921_coding_agent_expansion.md` §3.2（循环纯化第 0 步）
  - `docs/research/20260921_dual_session_reconciliation.md` §3（24 文件 +423/-32 未提交，前置 commit 风险）
- **本次调研纪律**：所有论断按 `lingclaude-true-read` 三件套审计（组件 + 触发条件 + 数据流），未走"看 commit message 推断"反模式。

---

## I. codex 修订：根因层增补——原文漏掉的那个断点

> 原文档 §A 按 ROI 列的 4 条（datalog sink / flywheel 只读标量 / audit_task 无消费 / session-bound 规则）
> 经 codex 复核**全部成立**。但这四条修的都是"信号接入"。codex 真读 `self_optimizer/` 全量
> （2995 行，8 文件）后发现一个原文未列的**更深层断点：回路 C 的目标函数错位**。
> 它不是"半断"，是"空转"。

### I.1 回路 C 空转：Goodhart 梯度内建于基准题集（✅实测证据链）

1. ✅ `optimizer.py:38-54` `_build_search_space`：三个 goal（structure/performance/simplicity）的
   搜索空间**全部是审计阈值**——max_class_size / max_method_count / max_complexity / cache_size /
   timeout / complexity_threshold……**没有一项是 agent 行为参数**（路由策略、重试策略、工具选择、
   prompt 模板均不在空间内）。
2. ✅ `benchmark.py:29-30` 注释自承："params 调得越宽松分数越高，调得越严苛分数越低"——
   P0 实证门禁的梯度方向**内建 Goodhart**：优化器把尺子调松即可涨分，无需改变任何代码。
3. ✅ `daemon.py:249-373`：P0 门禁只挡 `bench_after.score < bench_before.score`（回退），不挡虚增；
   P1 回滚锚定的 `best_score`（violations）由 `StructureEvaluator.evaluate(params)` 用**同一套
   被调参数**算出（`evaluator.py:47-49`）——自指：尺子证明自己变准了。
4. ✅ `daemon.py:418-433` `_apply_params`：默认 report-only（`LINGCLAUDE_DAEMON_APPLY != "1"` 时
   只记日志不动配置），叠加 PermissionContext 闸门 + 单参数限幅 + file_edit_lock **四重护栏**——
   护栏本身正确（建议-执行强制分离 + 2026-09-05 事故教训），但**缺批准出口**，回路在物理上是断的。

**对原文 §G.2 的结案**（原文列为"未解决问题"）：✅ optimizer 是真优化——`SynchronousOptimizer`
真遍历解空间（经 lingminopt MinimalOptimizer），120 行非 placeholder。但它优化的对象是
"什么时候再触发优化"的**灵敏度**，不是代码质量、也不是 agent 行为。飞轮 C 确实在转，
转的是空气：阈值变化 → 触发频率变化 → 再优化阈值，全程不接触被控对象。
钱学森增益纪律说"纠错执行器必须有外部许可才能作用到被控对象"——现在的首要问题不是许可，
是执行器的作用域里**根本没有被控对象**。

### I.2 回路 B 补充：提炼层缺失（原文断点④的延伸）

原文加分断点④已查明：`hallucination_turn_*` 规则写后无人改频率/置信度，`update_rule_status`
仅 `opt_cycle_*` 回路调用。codex 复核成立，并补一条原文未列的：

- ✅ `RuleExtractor`（`learner/rule_extractor.py:12-`，min_frequency=3、min_confidence=0.7，
  职责恰是把 per-turn 反馈聚合成通用规则）**全仓无运行期调用方**：
  `grep -rn RuleExtractor lingclaude/ --include="*.py" | grep -v learner/` = 空集。
  后果：per-turn 原始规则永远不被提炼，KB 只进不出地膨胀（14.9 MB 实证，原文 §B 已测）。
  **提炼层存在但从未接线**——这是"学习"停留在原始信号层的直接原因。

### I.3 回路 D 补录：两条原文未列的断头

- ✅ `daemon.py:720 behavior_trend`（窗口趋势计算）**无消费方**（grep 空集）——行为趋势算出来
  没人看，不接入触发器、不接入路由、不进报告。
- ✅ `reports/cycle_NNNN.md` **无队列出口**：不入 `arch_audit_task`、不发 LingBus、不产生待办。
  与原文断点①同源但方向相反：①是 task 进不来，这里是 report 出不去。两个方向都断，
  daemon 与灵族治理体系之间等于没有接口。

### I.4 与原文档三条改造的依赖关系（codex 判断）

原文 新 P0 #1（datalog aggregator）/ 新 P0 #2（flywheel→KB 桥）/ 新 P1 #3（audit_task→trigger）
codex **全部认可**，且同意"均为纯数据流、不依赖循环纯化、可立即开工"（原文 §F 判断成立）。
但三条都是**信号接入层**修复——信号接得再多，只要目标函数仍是审计阈值（§I.1），
优化器仍只在调尺子。因此 codex 将"换目标函数"（§J-F1）列为**与原文新 P0 并行、
且逻辑上更优先的根因修复**：先让优化指向被控对象，再接入更多信号才有意义。

---

## J. codex 修订：F0–F4 闭合方案（让飞轮真正转起来）

> 原则：全部新组件走 `SeamType.SELF_OPT` 插片，主干零 diff（铁律 1/3）；
> `_apply_params` 的 report-only 默认与四重护栏**保持不变**（原文 §E 反自动改码纪律成立），
> codex 方案只补"断头出口"，不拆护栏。

### J-F0 先让飞轮可观测（本周，纯加法，零风险）

飞轮不转的第一证据是"没人知道它没转"。新建 `self_optimizer/flywheel_health.py`
（SELF_OPT 插片），每周输出四个指标，落 LingBus channel=system（复用 H6 通道）：

| 指标 | 定义 | 数据源 |
|------|------|--------|
| 规则召回命中率 | 注入"已学经验"的轮数 / `search_rules` 调用轮数 | system_prompt_builder 埋点 |
| 规则有效率 | 注入后当轮 hallucination_risk 下降的占比 | behavior 回喂对比 |
| 参数应用率 | 实际应用数 / optimizer 建议数 | DaemonState |
| rollback 率 | P1 回滚次数 / 应用次数 | DaemonState |

**验收**：四个数连续两周产出 → 飞轮真实转速有基线；后续所有改动以此为 H17 闭环申报的载体。

### J-F1 换目标函数：搜索空间从"审计阈值"换成"策略 YAML"（核心根因修复）

1. `optimizer.py::_build_search_space` 加 `goal == "behavior"`：搜索空间 =
   `core/policies/*.yaml` 中的数值型策略字段（路由置信阈值、降级链权重、重试次数、
   cache TTL）。策略是 data（铁律），改 YAML 不碰主干。
2. benchmark 题集分两层：
   - **结构题**（现有 12 题）降级为参考，**不再当门禁**——它防不了"调松尺子得分"（§I.1）。
   - **行为题**（新建 `bench/behavior/`）：从 `data_flywheel.db` 错误记录与 lingmemory 失败会话
     沉淀回归用例——"上次这类任务失败了，用候选参数重放，成功了吗"。**门禁只看行为题分数**。
3. 应用纪律抄 Penguin Harness（§LC_PEER_BORROWABLE 1.4）：**应用前原子快照**
   （tar.gz 到 `.lingclaude/snapshots/`）、**拒绝版本不复用**、scoreboard 单源禁止重算。
4. 与 JEV/Laya 文档的衔接：`benchmark.py` 的 `eval_calibration`（P1-2，今日已落地）
   作为行为题的门禁维度之一——ECE 不回退才允许应用。

**验收**：跑一轮 `goal="behavior"` 优化，best_params 里出现至少一个 policies YAML 字段；
行为题门禁挡住一次"结构题涨分但行为题回退"的参数集（反 Goodhart 实证）。

### J-F2 接归因链：experiment_id 贯穿（F1 之后）

1. `_apply_params` 每次应用生成 `experiment_id`，写 DaemonState + LingBus；
2. 后续 N 个 turn 的 `record_turn_learnings` 与长任务指标打同一 id；
3. daemon 下轮启动先做**归因结算**：该实验窗口内行为题重放分 + 规则有效率变化 →
   accept（转 best_ever）或 rollback（回 F1 快照）。
4. 这是 Penguin 8 步循环（evidence→hypothesis→candidate→evaluate→accept/rollback）
   在 lc 的最小形态；也是 ECE 校准的真正数据源——没有 experiment 窗口，校准没有样本。

**验收**：任意 experiment_id 能从 LingBus 消息追溯到 accept/rollback 结局（因果链可查）。

### J-F3 学习回路固化（可与 F1 并行）

1. **提炼接线**：`RuleExtractor` 接进 daemon 周期——per-turn 规则按 context_keywords 聚类，
   ≥3 次跨会话复现的**去掉 session 前缀升格为全局规则**，置信度按复现率重算。
   （修 §I.2：提炼层从未接线。）
2. **召回改造**：`system_prompt_builder.py:287` 的 prompt 前缀关键词匹配改为
   **意图匹配**——`core/behavior.py` 的 Intent 分类已存在，零新依赖；
   按当前轮意图类型取规则，而非撞 prompt 前 50 字符。
3. **淘汰通道**：规则注入后当轮风险不降反升 → confidence 衰减；< 0.3 进冷宫。
   Ebbinghaus 衰减机制 `layered_memory` 已有，复用不新造。

**验收**：同一错误模式在两个不同会话出现后，第三个会话的 prompt 中出现**提炼后的通用规则**
（非 session-bound 原始规则）；一条连续 5 次无效的规则被降权至不再注入。

### J-F4 daemon 值守与批准队列（纪律动作）

1. AGENTS.md SDT 表加一行：`SDT-lc-007 | 自优化飞轮周期（F0 健康度 + F2 归因结算 + 报告）| 每6小时 | P1`，
   触发走 `scripts/wake_with_task.py` 现有通道。
2. **报告强制出口**（修 §I.3）：每份 cycle 报告二选一——入 `arch_audit_task`（待办）或发
   LingBus（知会）；不再允许只进 `reports/` 目录。
3. **批准队列**（修 §I.1 断点 4）：report-only 的建议参数写台账（仿 `arch_debt` 格式），
   用户在 CLI 里 `/optimize approve <id>` 一键应用——四重护栏一层不拆，
   断头路变成有审批流的活路，符合"建议-执行强制分离"（AGENTS.md 核心规则）。

**验收**：SDT-lc-007 出现在 AGENTS.md；一份 cycle 报告能在 arch_audit_task 或 LingBus 找到对应记录；
`/optimize approve` 应用一条建议后 config.yaml 出现对应变更且 experiment_id 已登记。

### J-× 与原文档方案的对照总表

| codex 方案 | 与原文档的关系 | 依赖 |
|-----------|---------------|------|
| F0 可观测 | 原文未列，纯新增 | 无，可立即开工 |
| F1 换目标函数 | **根因修复，原文未列**；原文新 P0 #1/#2 为其提供信号源 | 无（信号接入可并行） |
| F2 归因链 | 原文未列 | 依赖 F1 |
| F3 学习固化 | 覆盖并扩展原文 P1（session-bound 规则改造） | 与 F1 并行 |
| F4 值守+批准队列 | 原文新 P1 #3 的超集（含节流分级 + 报告出口 + 批准队列） | 无，可立即开工 |
| 原文新 P0 #1 datalog aggregator | codex 认可，照原方案执行 | 无 |
| 原文新 P0 #2 flywheel→KB 桥 | codex 认可，照原方案执行；F3 的意图召回是其召回侧补充 | 无 |
| 原文 §F 循环纯化前置 | codex 认可：F0–F4 均不依赖它，但 JEV 文档 P0-0 判定不变 | 独立轨道 |

---

## K. codex 修订记录表

| # | 类型 | 内容 | 位置 |
|---|------|------|------|
| 1 | 增补根因 | 回路 C 目标函数错位（Goodhart 内建），原文未列 | §I.1 |
| 2 | 结案 | 原文 §G.2（optimizer 是否 placeholder）→ 真优化但优化对象错位 | §I.1 |
| 3 | 补充 | RuleExtractor 提炼层无运行期调用方（原文断点④的延伸） | §I.2 |
| 4 | 补录 | behavior_trend 无消费方；cycle 报告无队列出口 | §I.3 |
| 5 | 判断 | 原文三条改造属信号接入层，F1 为逻辑上更优先的根因修复；二者并行不冲突 | §I.4 |
| 6 | 新增方案 | F0 可观测 / F1 换目标函数 / F2 归因链 / F3 学习固化 / F4 值守+批准队列 | §J |
| 7 | 印证 | 原文 §A 四条结论、§C 断点①②③④、§E 三条 anti-pattern、§F 前置判断——codex 复核全部成立，无分歧 | — |
| 8 | 边界声明 | codex 修订仅覆盖 `self_optimizer/` + `core/` 相关链路；`lingmemory` 侧蒸馏回路（distill_daemon 等）不在本次真读范围内，🟡待后续补读 | — |

---

## L. 灵克评价与归并（2026-09-22，独立复核）

> 复核人：灵克（AtomCode）。对本文档 6 项核心论断本机抽查（datalog 零读 /
> RuleExtractor 零运行期调用 / get_recurring_errors 零调用 / behavior_trend
> 零消费 / audit_task 19条4open / optimizer 搜索空间全阈值）——**全部成立**。

### L.1 两处采纳（比灵克审计报告 docs/audit/20260922_self_optimize_loop_audit.md 更深）

1. **§I.1 回路 C 空转判定采纳**：灵克原报告将优化路径定性为"悬空不回写"（断点），
   本文档追进 `_build_search_space` 证实搜索空间无被控对象（Goodhart 内建）——
   修回写治不了，必须换目标函数（F1）。灵克 M2 定性修正为 F1 的子问题。
2. **§J-F0 可观测先行采纳**：灵克方案从止血直接跳反馈边，缺基线指标层；
   无 F0 四指标则后续改动无法 H17 闭环申报。F0 提为第一优先。

### L.2 一处分歧（F3 召回源）

§J-F3 建议召回改造挂 `core/behavior.py` Intent 分类——当轮信号有时滞；
建议改挂 **Laya fast_route verdict**（P1-1 已接、conf≥0.3 门控、~150ms 前置），
意图源更早且已带质量闸，不另建判定层。

### L.3 归并结论（两报告合一，以本文档为主干）

- 灵克 M0（存量 26546 条清洗+注入限额）→ 归入 F3 前置步骤
- 灵克 M1（confidence 演化反馈边）→ 并入 F3 淘汰通道
- 灵克 M2（daemon 回写）→ 并入 F1/F2
- 灵克 M3（verify_ledger 证据挂钩 conf 上限 0.9/0.6）→ **codex 未覆盖的增量，
  保留为 F5 候选**（与灵克验证台账纪律同构）
- 执行序：F0 → 原 P0#1/#2（并行）→ F1 → F3 → F2 → F4（→F5 候选）
