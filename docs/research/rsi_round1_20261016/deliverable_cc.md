# 两篇 RSI 论文对灵克（lingclaude）自进化框架的借鉴与优化方案

> 评审对象：`/home/ai/lingclaude` 自优化模块（`lingclaude/self_optimizer/`，4777 行）+ 学习回路（`core/turn_learner.py`、`core/data_flywheel.py`）。
> 依据：arXiv:2609.11873（RSI 综述）、arXiv:2609.36054（自动化 R&D 风险联署）。
> 本文所有 lc 事实均标注 `文件:行号`，可复核。

---

## Q1 借鉴意义

### 1.1 用「改进循环七要素」解剖 lc

论文第 2 节把一个改进循环拆成七要素，判定 RSI 的关键是**哪些决策由谁控制**。按此解剖 lc：

| 要素 | lc 现状（实证） | 判定 |
|---|---|---|
| **AI system** | Python 单机 agent 运行时（`lingclaude/`，engine/core/gov 三层） | ✅ |
| **System state**（跨轮保留并继承） | `daemon_state.json` 的 `DaemonState`（`daemon.py:70-133`：`best_ever_params`/`stall_count`/`frozen*`/`benchmark_baseline`）+ `knowledge.db` rules + `experiments.db` 台账 + `behavior_policy.yaml`/`config.yaml` | ✅ 状态确实继承，但**只有单点最优快照，没有版本谱系与任意点回滚** |
| **Experience** | `data_flywheel.db` error_log/corrections、`core/turn_learner.py`、`failure_cluster_analyzer.py`（聚类→LearnedRule draft）、`rule_effect_verifier.py`（7 天前后窗口回放判定） | ✅ **lc 最强的一环**，断点①②③④ 已把"经验→行为变更"闭环 |
| **Target**（本轮直接修改对象） | 只有 **7 个阈值标量**。structure：`config.yaml` 的 `max_class_size/max_method_count/max_complexity/max_nesting_depth/coupling_limit`（`daemon.py:864-870` 的 `param_map`）；behavior：`behavior_policy.yaml` 的 `consecutive_fail_limit/tool_repeat_limit` | ⚠️ **lc 从不修改自己的代码、prompt、工具实现、检索策略** |
| **Improver** | `lingminopt.MinimalOptimizer`（`optimizer.py:168-174`），`seed=42` 硬编码、`max_trials` 默认 20；structure 空间 4×4×4×4×5=1280 点 → 20 次随机采样**覆盖率约 1.5%**；behavior 走本地穷举 24 点（`optimizer.py:127-132`） | ⚠️ 采样密度过低且种子固定，跨轮探索无多样性 |
| **Strategy**（决定搜哪里） | `config.get("strategy", "random")`（`optimizer.py:158`）+ `_maybe_rotate_goal`（`daemon.py:690`，`stall_count>=8` 切 goal） | ❌ **策略写死在代码里，不在 Target 集合内**——论文明说 Strategy 可成为后续 Target，lc 恰停在这一步 |
| **Verifier**（评估并执行接受规则） | 三闸：P0 实证门禁（`benchmark.py` 12 题 AST 静态检查）、P1 择优回滚（`daemon.py:617-633`）、F7 frozen zone 死循环熔断；规则层另有 `rule_effect_verifier` | ✅ 闸门齐备（**但见 1.2：闸门本身有洞**） |

### 1.2 最关键的发现：structure 回路在「自调量具」

这是本文档优先级最高的一条，因为它是可检验的代码事实，不是判断：

`_build_search_space("structure")` 搜的三个主参数 `max_class_size / max_method_count / max_complexity`（`optimizer.py:56-61`），**同时就是 `StructureEvaluator._analyze_structure` 的判据入参**（`evaluator.py:50-52`），**同时就是 `trigger._check_structure` 的比较阈值**（`trigger.py:106`、`trigger.py:124`），**同时就是 daemon 写回 config.yaml 的键**（`daemon.py:864-870`）。四者同源。

后果：structure 循环的最优解在数学上必然落在搜索空间上界——violations 下降是**量具放松**，不是代码变好。P0 门禁也拦不住：`benchmark.py` 的内置 12 题判定本身就是结构阈值（`{"id":"b01","check":"max_class_lines<=600"}` 等），`adopt_params(best_params)` 把同一批阈值灌进题集，分数上升是**同义反复**，不是独立验证。

代码里其实已经有这个认知：`optimizer.py:71-72` 的 F1 注释原文——「原 structure 空间调的是 benchmark 题集自身阈值 → Goodhart 空转」——**但只修了 behavior 分支（改搜 `behavior_policy.yaml` 真实阈值），structure 分支原样保留至今**。这是 lc 自进化回路的头号缺口，正中论文第三挑战「可靠验证：反复访问评估器会奖励 exploit」与第二挑战「自主性归因：候选变好≠改进了发现候选的方法」。

### 1.3 自主性定级：**L1（改进执行自主）的低配**

- **已达 L1**：无需人工介入即可执行改进——report-only 写配置（`daemon.py:808-813`）、backlog 白名单四类动作自动执行（`backlog_executor.py:6-7`「未匹配白名单的条目绝不自动改代码」）、四闸自动裁决。L1 定义为「改进执行自主：数据/训练方法/平台/评估/部署优化/应用系统」，lc 覆盖「平台配置 + 评估阈值」两小块。
- **未达 L2**：策略（搜哪里、怎么搜、用什么预算）全由人写死在 `optimizer.py:158` 与 `daemon.py:690`，且 Target 集合里没有 prompt/harness 维度。论文 L2 定义为「改进策略自主：prompt 搜索 / agent-harness 搜索」，lc 完全没有这一层。
- **远未达 L3/L4/L5**：benchmark 题集是固定 12 条静态 AST 题，无自适应任务生成、无 self-play、无环境交互练习、无轨迹蒸馏、无改进评估器本身（L5）。
- **在 L1 内部也是最窄的一档**：Target 只有 7 个标量，且其中 5 个是结构阈值（见 1.2，实际近乎无效）。

**最大缺口（按严重度排序）：**

- **G1｜验证器与被控对象同源（exploit 面）**——见 1.2。论文三挑战的「可靠验证」+「自主性归因」双重命中。当前 lc 无法区分"结构真的改善"与"阈值被放宽"，因此**整套改进回路的收益无法归因**。
- **G2｜Headroom 未度量**——HCI 的核心是回答"还剩多少可改进空间、改进是不是越来越贵"。lc 的全部指标都是代理量（violations / avg_complexity / 回放代价 / 12 题基准分），**没有任何任务级成功度量**（任务完成率、人工返工率、单位任务 token）。而 `trigger._check_time`（`trigger.py:270-288`）在无 headroom 判断的前提下，本质是"距上次优化 ≥1 天就再跑一轮"的**定时烧预算**。这正是三支柱里 Visibility 在 lc 残缺的位置：观测得到的是内部量，不是价值量。
- **G3｜安全继承无版本谱系**——论文挑战一引 Gödel Agent「100 次试验中 14% 低于初始策略」，对应的机制要求是迁移测试/版本历史/回滚。lc 有 `record_change(config_path)` 写前留档（`daemon.py:885-888`），但：① report-only 默认下根本不写；② 只有一个 `best_ever_params` 单点，没有"第 N 轮用了什么"的完整谱系；③ 无一键回滚到任意历史点。一次坏参数的发现周期取决于人工翻 git。

### 1.4 第二篇论文的映射：风险不在 r>1，在"人失去参与机会"

Hinton/Bengio/Pachocki 那篇谈的是 AI R&D 自动化后的社会性爆炸。lc 是单机开源项目，**不存在能力爆炸层风险**，但论文三大风险在单个 agent 系统内部有精确同构物，且**同构的第一条不是能力，是控制权**：

- **「人类监督与控制丧失（人类失去参与机会与专业性）」** → lc 的 report-only 模式：daemon 已经跑到数百圈，人只在 `advisor.generate_report` 落盘的 markdown 里事后看；trigger 阈值、optimizer 策略、goal 轮换规则全是人设一次、agent 长期自跑。**决策权已转移而人不知情**——这是 lc 现阶段最实在的风险，且与 1.3 的 L2 缺口同源。
- **「权力制衡被侵蚀」** → evaluator 与 improver 同源（1.2），自评自证，缺少独立裁判面。
- **「能力增长超过社会驾驭速度」** → lc 的对应物是**改进速率超过可观测速率**：报告是逐轮 md 文件，没有单一可查询的"改了什么、值不值、回滚率多少"视图（`state_store_ext.py:284` 的 `optimization_outcomes()` 已有数据但无出口面）。
- 政策三支柱（Visibility / Steer & Constrain / Adapt）映射见 Q2 治理小节。

---

## Q2 优化方案

原则：**先修尺子，再谈搜索**。在验证器可信之前扩大搜索空间，只会加速 Goodhart。

### P0 — 立即做（不做则整个回路的收益不可归因）

**P0-1｜切断 structure 自调量具回路**（优先级 P0）
- 动作：让 `evaluator` 与 `trigger` 的阈值**不再从 config 读**，固定为模块内常量（`evaluator.py:138-143` 的 `get_current_metrics` 已经是固定 500/25/20，直接复用这组值）；`_build_search_space("structure")` 与 `daemon._apply_params` 的 `param_map` 一并下线——当前没有真实的代码侧干预旋钮可搜，就**直接停用 structure goal**，只保留 behavior goal（其 Target 是真实行为控制旋钮，`optimizer.py:74-76`）。
- 论文依据：第三挑战 Reliable Verification（反复访问评估器会奖励 exploit）；第二挑战 Autonomy Attribution（无法区分自改进与外置）。

**P0-2｜HCI-lite：给 lc 一个 headroom 判据**（P0）
- 动作：新增 `self_optimizer/headroom.py`，用**已有语料**算三项趋势，不新增采集面：① 任务级通过率（`core/turn_learner.py` 的纠正语义门槛已能区分"纠正"与"寒暄灌水"，可直接算无纠正回合占比）；② 单位任务成本（`token_monitor_service.py` + 按 token 记账链路）；③ 现有基准分。输出单一标量趋势 `HCI_lc = w1·返工率 + w2·(1−通过率)`。落点：`trigger` 增加 `_check_headroom`，**headroom 低于阈值时不触发优化**（转省预算分支），取代现有 `_check_time` 的纯定时触发。
- 论文依据：HCI（跨基准归一化的未闭合提升空间、改进越来越贵）；三支柱 Visibility。单机项目不必做跨基准严格归一，做**同基准纵向趋势 + 是否值得再跑一轮的判据**即可。

**P0-3｜评估器冻结与指纹化**（P0）
- 动作：给 `BenchmarkEvaluator` 与 `RuleEffectVerifier` 各加 `evaluator_version` + 源码指纹（git digest），写入 `experiments.db` 的每条实验记录；跨轮比较仅在**同指纹**下进行，指纹变更则本轮结果标 `incomparable` 而非直接比大小。lc 已有 fingerprint 机制先例（`scripts/self_audit_trigger.py` + arch_ledger），直接沿用不新造。
- 论文依据：第三挑战「评估器本身演化会破坏跨轮可比性」。

### P1 — 本季度做（把 L1 补到 L2 入口 + 补治理三支柱）

**P1-1｜Strategy 入 Target（L2 的最小可行步）**（P1）
- 动作：把 `strategy ∈ {random, grid, bayesian, annealing, tpe}`、`seed`、`max_trials`、goal 序列全部落进 `DaemonState`，允许 daemon 自选。lingminopt **已原生支持全部 5 种**（`/home/ai/lingminopt/lingminopt/cli/meta_optimize.py:28` 的 `click.Choice`），是纯配置改动、零新依赖。选型判据用 `experiments.db` 历史（哪条曲线收敛快/回滚率低）。同时把 `seed=42`（`optimizer.py:173`）改为按 cycle_id 派生，恢复跨轮探索多样性。
- 论文依据：七要素「Strategy 本身可成为后续 Target」；自主性 L2（改进策略自主）。

**P1-2｜状态版本谱系 + 一键回滚**（P1）
- 动作：`DaemonState` 增 `history: list[{cycle_id, params, score, evaluator_fp, goal, ts}]`（现有 `cycles` 只有摘要、`best_ever_params` 只有单点）；新增 `python -m lingclaude.self_optimizer.daemon rollback --to <cycle_id>`，写回 config.yaml/behavior_policy.yaml 前照旧走 `PermissionContext.check_action` + `record_change`。**回滚本身也开一条 experiments 台账**（verdict=`manual_rollback`），保证 F0 指标不失真。
- 论文依据：挑战一 Safe Inheritance（版本历史 / 回滚机制，对应 Gödel 14% 退化）。

**P1-3｜变更速率限制（Steer & Constrain 落地）**（P1）
- 动作：三条硬限——① 单日 `accepted` 实验数上限；② 同一 config 键 24h 内不得二次变更（冷却表落 `experiments.db`）；③ 触及 `gov/guard` 白名单的键（如 `max_class_lines`）**只读不写**，永久排除出 Target。超限即置 `frozen=True` 并指数退避——**复用已有 F7 机制，不新增状态机**（`daemon.py:324-355`）。
- 论文依据：三支柱 Steer & Constrain（能力增速限制、可暂停特定工作负载）。

**P1-4｜经验三属性标注**（P1）
- 动作：`failure_cluster_analyzer` 产出的 `LearnedRule` 增三字段：`legality`（白名单外一律不自动注入）、`difficulty`（按规则被注入后的会话跨度度量）、`persistent_gain`（直接取 `rule_effect_verifier` 的 7 天前后窗口结论，`rule_effect_verifier.py:35-38` 的三个常量已就位）。三属性不全 → 规则停在 `draft`，不进热路。
- 论文依据：第 6 章方向二「学习者条件化的经验获取与可靠学习信号（合法性/难度/持久收益）」。

**P1-5｜跨组件诊断**（P1）
- 动作：失败聚类目前只归因到 tool+签名，答不出"该改数据/上下文/工具接口/模型哪个"。给每条签名加 `suspect_component ∈ {data, context, tool_api, model, harness}`，由 `turn_learner` 纠正原文关键词启发式初判，**输出物改为「干预假设 + 受控对比方案」而非直接 append backlog 修复**（backlog 自动执行保持白名单四类不变）。
- 论文依据：第 6 章方向一「把诊断转成可检验的干预假设 + 受控对比」。

### P2 — 有余力做

- **P2-1｜可见性单页**（P2）：把 `state_store_ext.health_counts()` / `optimization_outcomes()`（`state_store_ext.py:246-318`）已有数据接成 CLI 单页：本周期改动数、accepted/rolled_back/rejected 比、frozen 次数、evaluator 指纹历史、headroom 曲线。依据：Visibility。
- **P2-2｜自适应题集**（P2）：`benchmark.py` 的 12 题按 failure_cluster 命中情况替换同分布新题，防固定题集被"记住"。依据：L3（未来学习经验自主）预研。
- **P2-3｜总线侧暂停开关**（P2）：每轮 trigger/params/verdict 发一条 LingBus `channel=system`（现在只有报告落文件 + 失败入档），daemon 每轮读 pause flag。依据：Adapt 支柱（保持人在环内的参与机会，直接对冲 1.4 的"控制权丧失"）。

### 治理视角：三支柱落点对照

| 支柱 | lc 现状 | 建议机制 | 落点 |
|---|---|---|---|
| **Visibility** 可见性 | 逐轮 md 报告、journal 日志、零散 jsonl；无价值量口径 | headroom 曲线 + F0 指标单页 + evaluator 指纹历史 | `headroom.py`、`state_store_ext.py` 出口面 |
| **Steer & Constrain** 导向约束 | report-only + P0/P1/F7 四闸 + 权限闸（已相当扎实） | 变更速率限制 + 白名单键只读 + pause flag；**闸的自变量要与被控对象解耦** | `daemon.py:_apply_params`、`experiments.py` |
| **Adapt** 适应准备 | 单机无外部接受环节；缺"人工接管即接管"的一键通道 | `rollback --to` + 总线广播 + 人工 rollback 入账 | `daemon.py` CLI、`experiments.py` |

### 明确不建议做的

- 不训练/微调模型（单机开源、无算力、论文 1 的 L1-L2 在 harness 层已足够占住性价比）。
- 不追求论文意义的跨基准 HCI 严格归一（单机无跨基准基础，做同基准纵向趋势即可，强行归一会造出第二个 Goodhart 面）。
- 不把 `backlog_executor` 白名单从四类扩到"自动改代码"——那直接踩第一挑战的安全继承，且当前 RuleEffectVerifier 的回放窗只有 7 天，样本量（`MIN_SESSIONS=4`）不足以支撑自动改码的验证强度。

### 一句话路线

**P0-1 + P0-3 把尺子钉死 → P0-2 让"值不值得改"可判定 → P1-1 才有资格扩搜索面（P1-3 限速、P1-2 留退路）→ P1-5/P1-4 把"改哪、拿什么经验改"接上。** 顺序颠倒则每一次提速都在放大当前的不可归因性。

---

**字节约 3600 中文字符（含代码引用）**
