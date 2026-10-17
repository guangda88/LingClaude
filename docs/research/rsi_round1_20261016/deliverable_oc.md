# 两篇 RSI 论文对灵克（lingclaude）自进化框架的借鉴意义与优化方案 · 第 1 轮独立稿

## 背景

基于主人核实的 lc 自优化事实底座：self_optimizer 模块约 4800 行，覆盖 trigger 触发器、evaluator 评估器、optimizer 优化器、experiments 实验、rule_effect_verifier 规则效果验证、learner 学习器、daemon 常驻、benchmark 基准、replay_objective 回放目标、state_store_ext 状态扩展、advisor 顾问、audit_watch 审计、startup_watch 启动监听等模块；学习回路 turn_learner 以语义门槛过滤寒暄式纠正、data_flywheel 按同日去重统计复发率；治理层 R9 三闸（验收闸 / 漂移复核 / 成本总闸）叠加 6 层 security_gate（安全官灵安 owner）。现有优化计划集中于效率：token 三板斧（输出截断 / 结果缓存 / 上下文压缩）、轮次墙收敛、按 token 记账。

## Q1 借鉴意义

### 论文一（arXiv:2609.11873）给 lc 的工程框架启示

HCI（Headroom-Closed Index）提出跨基准归一化的未闭合提升空间指数，用于量化模型距能力上限还有多少可改进余量，并预期多数基准上 headroom 正在收敛。对 lc 而言，现有效率指标自证优化收益，却缺乏统一能力上限基线，难以回答「再改进还有余地吗、该不该继续改」。

七要素解剖给出 AI system / System state / Experience / Target / Improver / Strategy / Verifier 七个闭环节点。lc 现链路已有 Target 与 Improver 雏形，但 Strategy（搜索策略、固定 seed、stall 换策略）硬编码于 optimizer 与 daemon，未被纳入候选闭环；Verifier 侧自适应结构阈值又暴露可靠验证缺口。

五级自主性路线图自 B0 任务内改进到 L5 元改进。lc 目前更接近人工驱动的 L1 低配形态，最大缺口是 L2：改进策略本身不是后续改进的目标。

三大挑战同样直指 lc：持久化变更的安全继承需要版本历史 / 回滚 / 迁移测试；自主性归因要求区分候选变好是否源于方法真正变好；可靠验证要求评估器不可被反复投喂、跨轮可比。

### 论文二（arXiv:2609.36054）给 lc 的风险治理启示

智能爆炸论点指出：软件驱动 R&D 自动化回路趋近 r>1 即自持加速。lc 自优化若每轮投入持续超过每轮收益，会陷入低水平自我改良的失控。论文三大风险（能力增长超社会驾驭速度、人类监督与控制丧失、权力制衡侵蚀）映射为 lc 必须补齐的可见性、导向约束、适应三环。

### lc 当前自主性位置与最大缺口

我方判断：lc 处于 B0 与人工驱动 L1 之间过渡带。最大缺口是两条脊柱并存：Verifier 可信（结构自调量具使「变好」可能只是阈值放宽，与论文挑战二 / 三同构），以及 L2 策略自主缺失。单看即时止血，Verifier 可信是本周可处理的硬伤。

## Q2 优化方案

### P0 · 本周止血（预估 <100 行改动）

1. **[P0｜依据：三挑战-可靠验证 / 自主性归因]** Verifier 冻结与结构量具固化：evaluator / trigger 中 structure 类阈值恢复为模块内固定常量（如 evaluator.py:138-143 现值），下线 `_build_search_space("structure")` 与 daemon 的 param_map 结构分支，behavior goal 保留真实行为旋钮。
2. **[P0｜依据：三支柱-Steer & Constrain]** 变更速率总闸：daemon run_cycle 入口限制 accepted 速率（如 24h accepted 参数变更 ≤3、规则入库 ≤2），超限评估后 dry-run 入账、不直接应用。

### P1 · 闭环补全（1-2 周）

3. **[P1｜依据：HCI / 持久状态 / L4]** 持久状态与准入：state_store_ext 持久化变更附证据链（adopt_reason / evidence / 适用条件 / dependency）；best_ever 回滚前先 replay_objective 近 K 任务做反向配对验证。
4. **[P1｜依据：第六章-跨组件诊断]** 诊断转假设：failure_cluster_analyzer 失败聚类挂组件标签（data / context / tool_api / model），输出干预假设入 experiments。

### P2 · 自主性推进（本季度）

5. **[P2｜依据：L2 / 第七要素]** Strategy 自演化：strategy / seed / goal 序列纳入 DaemonState，利用 lingminopt 原生多策略支持，使「如何改进」成为后续 Target。
6. **[P2｜依据：L3 / Visibility]** 经验获取信号驱动：自检从固定 cron 转为复发率 / 成本 / 耗时异常信号驱动。

## 治理三支柱在 lc 中的落点

- **Visibility**：改进活动台账（trigger → candidate → verdict → artifact_origin）落盘。
- **Steer & Constrain**：变更速率闸、回滚演练、评估器冻结。
- **Adapt**：准入测试、回滚反向配对、适应性回退。

## 务实边界

不训练 / 微调大模型；单机开源项目聚焦 harness / workflow / 验证层；L5 元改进搁置。

DELIVERED
