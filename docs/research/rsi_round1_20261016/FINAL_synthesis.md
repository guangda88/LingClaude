# lc 自进化优化终案：两篇 RSI 论文的借鉴意义与综合方案（五方讨论汇总）

> 2026-10-16 ｜ 讨论轮：data/cross_agent/20261016_rsi_round1/
> 参与方：cc（claude-sonnet）、codex、atomcode、灵克（lk）。oc 两轮均失败判死（死因见 §六）。
> 依据论文：①《The Last AI Built by Humans: Toward Genuine Recursive Self-Improvement》（arXiv:2609.11873，清华/上交/字节/上海AI Lab 等）②《What if automating AI R&D triggers an intelligence explosion?》（arXiv:2609.36054，Hinton/Bengio/Pachocki 等联署）
> 原始稿件：同目录 deliverable_{cc,codex,ac,lk}.md，本文观点均可回溯。

## 一、两篇论文各给 lc 什么（借鉴意义）

**论文 1 给的是「解剖学 + 路线图」**：改进循环七要素（system/state/experience/target/improver/strategy/verifier）、五级自主性（B0→L1 执行→L2 策略→L3 经验→L4 部署适应→L5 元改进）、三大挑战（安全继承/自主性归因/可靠验证）、HCI（未闭合提升空间）。它回答「lc 该往哪走、卡在哪」。

**论文 2 给的是「治理前置」**：自动化改进的风险三同构物（变更速率超过可观测速率 / 控制权静默转移 / 评估与被控同源自证）+ 三支柱（Visibility / Steer & Constrain / Adapt）。它回答「lc 走的时候别摔」。

## 二、定位判定（五方共识）

**lc 处于「L1 低配~后期」，够不着 L2**；codex 提醒默认 report-only 下严格说是 B0+，ac 认为已在向 L2 攀爬——分歧只是措辞，实质一致：
- **已达**：改进执行自主（触发→候选→评估→许可后写配置/规则入库全自动，四闸裁决）
- **未达 L2**：七要素中 **Strategy 不是 Target**——搜索策略（random/grid、seed=42、stall≥8 轮换）全写死在 `optimizer.py:158`、`daemon.py:690`，lc 从不改进自己「怎么改进」的方法
- **特别缺陷（cc 独家实证发现）**：structure 回路在**自调量具**——`_build_search_space` 搜的 5 个结构阈值同时是 evaluator 判据与 trigger 比较基准（`optimizer.py:56-61` / `evaluator.py:50-52` / `trigger.py` 同源），候选「变好」可能只是阈值被放宽。`optimizer.py:71-72` 的 F1 注释早已认知此 Goodhart 问题，但只修了 behavior 分支，structure 分支带病运行至今。**正中论文挑战③可靠验证 + 挑战②自主性归因双重命中。**

## 三、四方观点交叉对照

| 议题 | cc | codex | ac | lk |
|---|---|---|---|---|
| 最大缺口 | 自调量具（尺子不可信） | 验证/归因/安全继承三缺 | Strategy 不是 Target + 速率/可见性缺位 | L2 缺失 + 持久状态无准入 |
| headroom 用法 | HCI-lite 趋势标量，headroom 低则不触发 | 边际收益判据做预算分配，不造总能力分数 | 执行层 headroom 闭合、策略层刚开 | — |
| 验证器治理 | 评估器冻结+指纹化（P0） | 评估器/数据集 hash 入账、禁自改（P0） | 评估器保持外置+version 冻结，**写进治理文档作为长期不变量** | Verifier 冻结与轮换纪律 |
| 状态继承 | 版本谱系+一键回滚（P1） | artifact 状态机 + last-known-good 快照（P0） | **继承反向配对**（P0）：旧基线回滚前先在近 K 任务上重验 | 变更准入闸 |
| 速率治理 | 日上限+冷却+白名单键只读（P1） | 日/周上限+冷却+全局暂停（P0） | **P0-1 速率总闸**（24h accepted≤3 参数/≤2 规则） | 改动速率预算 |
| L2 路径 | strategy/seed/trials 入 DaemonState（P1） | P2 只搜声明式 Strategy，先可证再自主 | meta-optimizer 搜索方向权重表（P1） | 经验获取调度器（P2） |
| 诊断→假设 | 签名加 suspect_component（P1） | diagnosis 插片产出干预假设（P1） | intervention hypothesis 入实验队列（P1） | 同左（P1） |
| 经验三属性 | legality/difficulty/persistent_gain（P1） | 来源合法性/难度/延迟结果（P1） | 同左+90天降级（P1） | — |
| 归因账 | — | — | artifact_origin=self/human/external（P2） | 台账含策略改动记录位 |

### oc 观点（第五方，10-17 补交，全稿见 deliverable_oc.md）

- **判位**：lc = B0 + 人工驱动 L1 过渡带；最大缺口是**双脊柱：Verifier 可信 + L2 策略自主**——与主判定/cc/codex 收敛。
- **P0 处方与已采纳方案完全一致**：结构量具固化为模块常量（=cc-P0-1）+ 变更速率总闸 24h accepted≤3/≤2（=ac-P0-1）。第五方在隔离环境下独立作答得出同案，**交叉验证通过，终案结论不变**。
- **独家增量**：① meta-optimizer 直接复用 lingminopt 原生多策略支持（阶段二实现提示）；② 自检从固定 cron 转为复发率/成本/耗时异常信号驱动（更贴 Visibility 支柱）；③ 失败聚类输出挂组件标签（data/context/tool_api/model）再生成干预假设。
- 引用行号抽查属实（evaluator.py:138-143 现值 max_class_size=500/max_method_count=25/max_complexity=20）。

**分歧与仲裁**：
1. **HCI 要不要造总分数**：cc 主张 HCI-lite 标量，codex 反对总能力分数、主张向量做预算分配 → **采纳 codex**（单机造总分会造出第二个 Goodhart 面），但吸收 cc 的「headroom 低不触发」语义，用向量边际收益判据实现同一效果。
2. **structure 回路处置**：cc 主张直接停用 structure goal、阈值回归模块常量 → **采纳**，这是四方中唯一给出可立即执行的止血方案。
3. **速率闸的优先级**：lk 放 P2、ac/codex 放 P0 → **采纳 ac**（成本一个函数、防温和漂移，性价比极高）。

## 四、最终方案（合成后，按执行顺序）

### 阶段〇：止血（本周，<100 行改动）
1. **停用 structure 自调量具回路**【cc-P0-1】
   - evaluator/trigger 的结构阈值改回模块内固定常量（复用 `evaluator.py:138-143` 现值 500/25/20），下线 `_build_search_space("structure")` 与 `daemon._apply_params` 的 param_map；structure goal 暂停，只保留 behavior goal（其 Target 是真实行为旋钮）。
   - 依据：挑战③可靠验证 + 挑战②自主性归因。
2. **变更速率总闸**【ac-P0-1，codex 呼应】
   - `daemon.py` run_cycle 入口加 change_rate_guard：24h accepted 参数变更 ≤3、规则入库 ≤2，超限本轮只评估不应用（dry-run 进台账）；复用 ExperimentLedger 时间窗查询 + F7 frozen zone 退避。
   - 依据：三支柱 Steer & Constrain（单机版「能力增速限制」）。
3. **评估器冻结+指纹化**【cc-P0-3，ac/codex 呼应】
   - BenchmarkEvaluator/RuleEffectVerifier 加 `evaluator_version`+源码指纹（沿 arch_ledger 指纹先例），写入 experiments.db 每条记录；跨轮比较仅同指纹进行，指纹变更标 `incomparable`。
   - 同时立为治理不变量：**评估器只许人工升级**，任何自动路径不得修改 evaluator/replay/回滚代码【ac 明确不做清单】。

### 阶段一：可证可退可停（1-2 周）
4. **artifact 状态机与 last-known-good**【codex-P0-2】
   - experiments.py 状态流改 `proposed→approved→applied→observing→accepted/rejected/rolled_back`；应用前原子快照目标文件+hash，观察期出现回退/复发上升即自动恢复快照；留出集只读、搜索不得访问；「无 traces 返回 0 分」改判 `insufficient` 禁止接受。
5. **继承反向配对**【ac-P0-3】
   - best_ever_params 回滚前先跑 replay_objective 在最近 K 个任务上配对：旧基线近期劣化则不回滚只归档重置——防「赢在历史、输在现在」。
6. **HCI-lite headroom 判据**【cc-P0-2 + codex 向量修正】
   - 新增 `self_optimizer/headroom.py`：用现有语料产出可解释 headroom 向量（任务通过率趋势 / 单位任务成本 / 修复复发率），trigger 的 `_check_time` 纯定时触发改为「headroom 边际收益 > 0 才触发」；不造单一总分。
   - 依据：HCI；三支柱 Visibility（观测价值量而非内部量）。
7. **改进活动台账（单机 Visibility 落点）**【ac-P0-2 + lk 台账】
   - state_store_ext 加 `improvement_activity()`：近 7/30 天变更数/对象分布/速率曲线/回滚率，append 到 `data/optimizer/activity.jsonl`（与 verify_log 同构）；每条变更记录带 artifact_origin（self/human/external）【ac-P2-1 提前合并】。
8. **端到端审计事件流**【codex-P0-3】
   - experiment_id 串联触发→候选→评估→审批→写盘→观察→回滚；audit_watch 消费并报断链；真实应用开启时审计库不可用必须 fail-closed；startup_watch 启动核验 artifact hash 与最近已知良好版本。

### 阶段二：从 L1 攀 L2（本季度）
9. **Strategy 入 Target**【cc-P1-1 + ac-P1-1 合成】
   - 第一步（cc）：strategy/seed/max_trials/goal 序列落 DaemonState；seed 改按 cycle_id 派生；lingminopt 已原生支持 5 种策略，纯配置改动。
   - 第二步（ac）：meta-optimizer 每 N 周期聚合台账（触发源×goal×参数域的 accepted 率）调整搜索方向权重表——「决定搜哪里」的方法开始被改进。
10. **跨组件诊断→干预假设**【四方一致】
    - failure_cluster_analyzer 后加 diagnosis 插片：失败簇归因到 data/context/tool_api/model/harness，输出「干预对象+预期指标+反证条件」入实验队列；每次只改一个因素受控对比。
11. **经验三属性打标**【四方一致】
    - 规则入库标 legality/difficulty/persistent_gain（复用 turn_learner 语义门槛 + rule_effect_verifier 7/30 天窗 + 同日去重），三属性不全停 draft；90 天无收益自动降级。
12. **回滚 CLI 与演练**【cc-P1-2 + lk-P2-6】
    - `daemon rollback --to <cycle_id>`（走权限闸+record_change，回滚本身入台账）；每季度演练「回滚到上一验证态」+ 一键暂停实测【codex-P0-4】。

### 长期边界（五方一致的不做清单）
- 不训练/微调模型；不做 L3 自生成任务/self-play（数据量不足）；不做 L5 评估器自我演化（论文明确警告破坏可比性）；不把 backlog 白名单扩到自动改代码（RuleEffectVerifier 7 天窗 + MIN_SESSIONS=4 不足以支撑验证强度）；不造跨基准 HCI 严格归一。

## 五、论文映射速查

| 论文概念 | lc 落点 |
|---|---|
| 七要素-Strategy 可为 Target | 阶段二-9 |
| 挑战①安全继承 | 阶段一-4/5/12 |
| 挑战②自主性归因 | 阶段〇-1 + artifact_origin 账 |
| 挑战③可靠验证 | 阶段〇-1/3 |
| 第6章持久状态管理 | 阶段一-4/5 |
| 第6章经验三属性 | 阶段二-11 |
| HCI | 阶段一-6（headroom 向量） |
| 三支柱 Visibility | 阶段一-7/8 |
| 三支柱 Steer & Constrain | 阶段〇-2/3 |
| 三支柱 Adapt | 阶段一-12 |

## 六、执行复盘与教训（10-17 修订：oc 死因已定案）

- 四家派发全程按 runbook 纯形态，cc/codex/ac 三家 3.5-4.5 分钟全交稿，配方有效。
- **oc 死因终审（第 5 死法：env 污染型 JSON 崩溃）**：`~/.ling_keys.env:60` 行尾 `#` 注释被 env 装载器灌进 `PROXY3_LING_SHARED_KEY` 值 → 全局 `opencode.json` 的 `${env:...}` 展开后 JSON 被炸毁 → 启动即崩（三次探针 stderr 均 62037 字节、逐字节一致的静态指纹）。**昨晚记录的「第 4 死法：serve 僵尸」已被证伪**（杀僵尸后探针照死；该 serve 是 oc-serve.service 正当常驻，Restart=always 会复活，禁止再误杀）。
- **修复与复活**：keys 注释剥离（防复发，lingclaude 重启后生效）+ apiKey 临时明文化（绕开本会话进程的脏 env 快照）。probe6 于 10-17 07:11 建 session（6 天断流终结），07:28 交稿达标（deliverable_oc.md，`DELIVERED` 结尾）。注：runner 900s timeout 只杀包装 shell，oc 本体存活至写完交付物——zen-free 档慢但可靠，timeout 建议 ≥1800s。
- 全程证据链：`oc_forensics/`（6 份探针日志 + 污染存证 + DIAGNOSIS_oc_death4.md 诊断定稿）。
- 灵克自踩已知坑一次：`head -c` 截多字节中文产生非法字节（project_memory 2026-10-09 在案）——检索/统计一律 Python bytes+errors='replace'。
- 派发纪要保持：同题独立作答→一手事实最多者（cc，全稿文件:行号）做骨架→增量并入→分歧表格仲裁。
