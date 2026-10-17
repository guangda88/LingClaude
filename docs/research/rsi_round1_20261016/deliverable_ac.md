# 两篇 RSI 论文对灵克 (lingclaude) 自进化的借鉴与优化方案（评审 AC）

## 0. 事实底座核对

读源码确认（`lingclaude/self_optimizer/`，约 4800 行）：daemon.py 已有 F2 归因链（experiment_id 贯穿 + accept/rollback 结算，`ExperimentLedger`）、P1 择优回滚（best_ever_score 基线）、F7 frozen zone（同参数+同因回滚连击熔断 + 指数退避）、R10 停滞轮换；state_store_ext.py 产出 F0 指标（apply_rate / rollback_rate）；trigger.py / evaluator.py / rule_effect_verifier.py 构成七要素中的 Experience→Target→Verifier 主链。加上治理侧 R9 三闸 + 6 层 security_gate，lc 的Verifier 层在同类开源项目里已相当扎实。

## 1. Q1：借鉴意义与定位

### 1.1 五级自主性定位：L1 后期，正在向 L2 攀爬

- **已达 L1**：lc 能自主优化执行侧对象——参数（optimizer/experiments）、规则（learner/rule_extractor → rule_effect_verifier 闭环验证）、触发器输入（trigger 语义门槛）。部署侧有 systemd job 常驻，评估侧有 benchmark + replay_objective。
- **未达 L2**：改进的**策略层**（搜哪里、按什么顺序搜、什么问题值得搜）全部硬编码：goal 切换由 R10 停滞计数驱动而非策略选择；advisor 输出的 steps 仍需人工消费；七要素中 **Strategy 不是 Target**——lc 从不修改自己的搜索程序。这是当前最大缺口，也是 HCI 指数意义上最值钱的一层：执行层参数的 headroom 正在闭合（token 三板斧边际收益递减），策略层的 headroom 才刚开始。

### 1.2 论文 1 的三点可操作启示

1. **归因缺口（挑战②）是最先要补的账**。lc 的实验台账已经做到"哪次实验导致的接受/回滚"可查，但**没有区分"规则变好了"和"发现规则的方法变好了"**。learner 的 rule_extractor 从不自我审查；rule_effect_verifier 验证的是规则效果而非抽取器的效果。按论文要求，每个入库 artifact 都应记录"它是否属于自改进产物、外置与否"——目前 AGENTS.md 里的规则（外置文档）与 optimizer 产出的参数（自改进）在证据结构上没有统一账目。
2. **状态继承要带"证据包"（挑战①+第6章持久状态管理）**。lc 的 best_ever_params 跨轮继承时只带 score 与 goal 标签，不带"当时在什么任务分布上赢的、适用条件是什么"。论文的 HDSO 配对评估思想（新 vs 旧在同一批任务上配对）lc 已有雏形（replay_objective），但继承侧没有反向要求：旧参数被回滚进来时同样要过一次配对，否则 best_ever 基线可能"赢在历史、输在现在"——goal 卫兵（F5）只防了尺子错配，没防任务分布漂移。
3. **跨组件诊断（第6章）是 lc 天然强项但未兑现**。论文说单点失败应转成"可检验的干预假设 + 受控对比"。lc 有 failure_cluster_analyzer 与全族消息总线，具备把"一次失败归到数据/上下文/工具接口/模型哪一层"的多源证据；experiments.db 已是受控对比的现成载体。差的是把 failure_cluster 输出直接生成 intervention hypothesis 并入实验队列的通道。

### 1.3 论文 2 的三点可操作启示

1. **可见性（支柱一）**：lc 有 F0 指标但缺**改进活动本身的实时可观测**——experiments.db 是结账视角，没有"本周自主变更了多少参数/规则、变更速率曲线、变更集中在哪些文件"的一页视图。智能化爆炸风险在单机形态的对应物是"优化循环预算失控"（frozen zone 只防了死循环，没防温和漂移）。
2. **导向与约束（支柱二）**：lc 已有 6 层 security_gate（准入）与 frozen zone（急刹），但缺**速率约束**——每周期自主变更量无上限、无人工暂停点（pause/resume 工作负载）。三闸是质量闸，不是数量闸。
3. **适应（支柱三）**：论文的"部署前提条件"映射到 lc = 每个 accepted 变更附带明确的部署条件（适用 goal、任务分布、回滚触发条件），而不是全量永久生效。

## 2. Q2：优化方案（按优先级）

### P0 — 三条，补最大缺口、成本低

**P0-1 变更速率总闸（依据：三支柱-导向约束）**
在 daemon.py 的 run_cycle 入口加 `change_rate_guard`：每 24h accepted 参数变更 ≤ N（建议 3）、accepted 规则入库 ≤ M（建议 2），超限则该轮只评估不应用（dry-run 进台账）。实现上复用 ExperimentLedger 按时间窗口查询，与 FROZEN_MAX_BACKOFF_CYCLES 常量同置。这是单机版"能力增速限制"，防止温和漂移。

**P0-2 改进活动可见性面板（依据：三支柱-可见性）**
在 state_store_ext.py 加 `improvement_activity()`：近 7/30 天变更数、按对象类型（参数/规则/触发器）分布、变更速率曲线、回滚率趋势；daemon 每周期结束后 append 到 `data/optimizer/activity.jsonl`。与既有 verify_log jsonl 同构，成本一个函数。让主人和审计随时回答"lc 这周自己动了什么"。

**P0-3 继承反向配对（依据：挑战① Safe inheritance + 第6章持久状态）**
择优回滚（best_ever_params 恢复）前，先跑一次 replay_objective 在**最近 K 个任务**上做新旧配对：旧基线若在近期分布上劣于当前，则不回滚、只归档并重置基线。改动集中在 daemon.py 的回滚决策点，复用现成 replay 设施。

### P1 — 三条，从 L1 攀 L2

**P1-1 Strategy 成为 Target（依据：七要素 + L2）**
给 optimizer.py 加 meta-optimizer：定期（每 N 个周期）把实验台账聚合成"哪些搜索方向产生过 accepted"（触发器来源 × goal × 参数域的成功率），据此调整下一阶段的搜索权重——先用最简版：一张方向权重表写入 state，trigger 候选按权重排序。这使"决定搜哪里"的方法本身开始被改进，是把七要素中 Strategy 扭成 Target 的最小步。

**P1-2 跨组件干预假设通道（依据：第6章跨组件诊断）**
failure_cluster_analyzer 输出聚类后，自动生成结构化 intervention hypothesis（`{层: data|context|tool|model, 假设, 验证实验, 预期指标}`）写入实验队列；experiments.py 加 hypothesis 字段，结算时校验预期指标。打通"失败→假设→受控对比"全链。

**P1-3 经验三属性打标（依据：第6章可靠学习信号）**
rule_extractor 入库时对每条规则标三属性：合法性来源（哪个用户纠正/哪次验证）、难度（触发频次）、持久收益（rule_effect_verifier 的 7/30 天复发率），三条都低于阈值的规则 90 天自动降级为"候选经验"不直接生效。复用 turn_learner 语义门槛 + data_flywheel 同日去重，改动在 learner/knowledge.py。

### P2 — 两条，治理收尾

**P2-1 自改进/外置归因账（依据：挑战② Autonomy attribution）**
在 verify_ledger 体系加 `artifact_origin` 字段（self-improved / human / external），优化器产出自动标 self-improved。月度出一张"归因表"：哪些改进真是 lc 自己发现的，哪些是外置注入的——回应论文"候选变好≠方法变好"。

**P2-2 部署条件随行（依据：三支柱-导向约束·部署前提）**
accepted 实验结算时要求附带 deployment_conditions（goal、适用分布、回滚触发器），存入 experiments.db；state_store_ext 周期性复核条件是否仍成立，不成立自动转 rolled_back。这是"变更不是永久生效，而是带条件的部署"。

## 3. 明确不做（务实边界）

- 不做自生成任务/self-play（L3）：单机数据量不足，收益密度低，是 L2 走通后的事。
- 不做评估器自我演化（L5 前置）：论文明确警告评估器演化破坏跨轮可比性；lc 的 benchmark/replay 尺子保持外置且 version 冻结，只允许人工升级——这一点应写进治理文档作为长期不变量。
- 不训练模型，全部改动落在 harness/工作流/验证层，总改动量估计 <600 行，均可独立回滚。

## 4. 一句话结论

lc 的 Verifier 与成本治理已是同类项目前列；真正卡在七要素图的 **Strategy 不是 Target**（归因与策略自主双缺）和**速率/可见性缺位**（三闸管质量不管数量）。先上 P0 三件（速率闸、活动面板、继承反向配对）即可把论文 2 的三支柱落到单机；P1 的 meta-optimizer 是从 L1 进入 L2 的最短路径。

DELIVERED
