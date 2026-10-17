# 两篇 RSI 论文对灵克自进化的借鉴意义与优化方案

> 依据题述论文要点，并只读核查了 `self_optimizer` 的触发、搜索、回放、实验台账、规则验证与 daemon 应用链路。

## Q1：借鉴意义与当前定位

### 1. 当前是“受限 L1”，默认配置的实际闭环仅为 B0+

lc 已能跨轮保留 `DaemonState`、知识库与实验记录，自动触发、生成候选参数、评分并在许可后写入配置，因而具备 **L1（改进执行自主）雏形**；但默认 `report-only`，若严格按默认运行形态，人工仍是执行闭环的一部分，宜称 B0+。它不是 L2：搜索空间、穷举/随机策略、目标轮换规则和评估器均为人先验设定，Strategy 尚未成为可验证的 Target。不应把“自动生成建议”误记为高自主性。

七要素在 lc 中已有对应物：`error_log/corrections` 是 Experience；`behavior_policy`、配置阈值和学习规则是 Target；`SynchronousOptimizer` 与失败聚类器是 Improver；固定网格/随机搜索及停滞轮换是 Strategy；`StructureEvaluator`、`BenchmarkEvaluator`、`ReplayObjective`、`RuleEffectVerifier` 是 Verifier；`experiments.db`、KB、`best_ever` 是 System state。**最大缺口不是候选生成能力，而是可靠验证、效果归因和安全继承。** 当前内置 benchmark 主要是静态 AST 结构检查，behavior 回放又反复使用同一批历史轨迹，难以证明真实编程任务能力提升，更难证明“发现候选的方法”本身进步了。

### 2. 论文一的可操作启示

- **先修 Verifier，再扩大 Target。** 把“某参数在历史语料上更优”与“harness 在新任务上更好”分开；把“候选有效”与“搜索策略比旧策略更会找候选”分开，分别实验，回应可靠验证与自主性归因。
- **HCI 应用于预算分配，而非制造一个总能力分数。** 现有 `stall_count>=8` 后轮换 goal 可升级为“单位成本的边际收益 + 各能力套件剩余空间”判据：结构指标接近饱和就停止空调参，把预算转向工具调用、上下文、修复复发等仍有 headroom 的环节。token 节省是约束和成本项，不应替代任务成功率。
- **持久状态必须携带证据与适用边界。** 规则、参数、prompt 等 artifact 不只保存“值”和历史最优分，还要保存来源任务、依赖版本、适用条件、验证集版本及后续效果；跨版本继承前重新做准入测试，否则持久化只会持久化错误。

### 3. 论文二的可操作启示

单机 lc 不会因自动调参就出现“智能爆炸”，但其治理结构同样适用：**Visibility** 要求每次触发、候选、审批、真实写盘、观察、回滚都可重建；**Steer & Constrain** 要求变更范围、速率、成本和环境可控，且人能暂停；**Adapt** 要求用回滚演练、留出集和小流量试运行吸收新风险。R9 三闸和独立 security_gate 是良好基础，但“有审批”不等于“效果已知”，也不等于“变更确已按审批内容生效”。

## Q2：自进化优化方案

总目标应是：**先把 lc 做成可证、可退、可停的受限 L1，再探索受治理的 L2；不以自主等级或变更数量作为 KPI。**

### P0：先封住验证与继承风险

1. **[P0｜可靠验证、Visibility] 重建评估契约。** 在 `benchmark.py` 增加由真实历史任务固化的小型编程回归集（测试通过、工具轨迹、token/轮次、人工纠正多维记录），静态结构题只作辅助。`replay_objective.py` 按 session/时间切分开发集与只读留出集，搜索不得访问留出集；最终候选与现行策略做同任务、同输入的配对比较。当前“无 traces 返回 0 分”在最小化语义下可能显得最优，应改为 `insufficient` 并禁止接受。`rule_effect_verifier.py` 的前后窗比较增加前窗样本门槛及匹配对照，避免把同期流量变化当成规则效果。评估器和数据集均记录版本/hash，同轮比较中禁止自改。

2. **[P0｜Safe inheritance、Steer/Adapt] 建立 artifact 注册与可恢复状态。** 保持 `state_store_ext.py` 只读 facade，新增独立 `artifact_registry`（可落在 SQLite）：记录 `artifact_id/type/parent_id`、证据链接、适用条件、依赖、代码/配置/evaluator 版本、预期指标和观察窗。`experiments.py` 从立即 `accepted` 改为 `proposed→approved→applied→observing→accepted/rejected/rolled_back`。daemon 应用前原子快照目标文件及 hash，保留 last-known-good（不只是分数最高者）；观察期出现测试回退、纠正/复发上升即恢复快照。这样也避免 report-only 建议与实际生效变更混用一个“applied/accepted”口径。

3. **[P0｜Visibility] 形成端到端审计事件流。** 以 `experiment_id` 串联 TriggerInfo、候选、策略、评估、审批、写盘回执、运行观察和回滚；`audit_watch.py` 消费事件并报告断链、无证据接受、版本漂移。观测采集可 fail-soft，但开启真实应用时，审计库或快照不可用必须 **fail-closed**。`startup_watch.py` 增加当前 artifact/hash 与最近已知良好版本的启动核验。

4. **[P0｜Steer & Constrain、Safe inheritance] 给自动化变更加“速度闸”和爆炸半径。** 保留现有单周期单参数限幅，新增可配置的每日/每周真实应用上限、冷却窗和全局暂停开关；回滚熔断从“同参数+同因”扩展到目标/组件级连续失败。Target 采用白名单，初期只允许声明式配置、prompt 模板和规则，不允许优化器修改 evaluator、R9、security_gate、审计与回滚代码。所有试验在隔离工作区运行，成本总闸同时计入 token、测试时长和后台 job 占用，并定期实测“一键暂停”和恢复演练。

### P1：从调参转向可诊断的 harness 改进

5. **[P1｜七要素、跨组件诊断] 让 trigger 产出可检验假设。** 在 `failure_cluster_analyzer.py` 之后增加轻量 `diagnosis` 插片，把失败簇分成上下文缺失、工具接口、工作流策略、模型能力等候选原因，输出“干预对象—预期指标—反证条件”。`trigger.py` 传递该假设，`optimizer.py` 只在对应 Target 白名单内搜索；每次只改一个因素并做受控对比，避免看到一个失败就同时改 prompt、阈值和工具。

6. **[P1｜HCI、成本治理] 用边际 headroom 替代固定停滞轮换。** 在 `benchmark.py` 按任务成功、修复复发、工具可靠性、上下文效率分别维护归一化套件分与可解释 headroom 向量，不强行压成一个“智能指数”。daemon 的 `_maybe_rotate_goal` 根据“连续无显著留出集收益”及“新增 token/测试成本每单位收益”降频、停止或换目标；token 三板斧继续保留，但必须同时满足成功率和安全不回退。

7. **[P1｜经验三属性、持久复用] 提升 Experience 的可学习性。** 在 turn_learner/data_flywheel 记录中补充来源合法性、任务难度、去重后的 task/session 标识、适用版本和延迟结果；同日去重可保留用于运营统计，但训练/验证切分须防止同任务近重复泄漏。规则晋升除注入前后密度外，还看 7/30 天持续收益和跨会话复现；环境或依赖失效时自动降权、重验，而非永久继承。

### P2：有边界的 L2，不碰模型训练

8. **[P2｜L2、自主性归因、Adapt] 只搜索声明式 Strategy。** 在 P0/P1 稳定后，把少量 prompt 模板、工具排序、重试/压缩策略表示为版本化配置，由 optimizer 搜索；用相同任务集比较“旧策略产生候选”与“新策略产生候选”的留出集收益，才能声称 Strategy 改进。评估器演化、研究目标改写及治理规则仍由人负责，不做 L3-L5 式开放自改。

## 落地顺序与验收

第一阶段只交付 P0，并保持默认 report-only；通过“空语料不能接受、任何真实变更全链可追溯、快照能恢复原 hash、暂停立即可用”后，才扩大自动应用。第二阶段用留出集真实任务收益、跨会话复发率和单位成本收益评估 P1。最后才开放 P2。这样，三支柱不是额外文书：可见性落在证据链，导向约束落在白名单/速率/独立闸门，适应落在观察窗、回滚与复验；lc 的进化也从“更频繁地改自己”转为“更少但更可信地继承改进”。
