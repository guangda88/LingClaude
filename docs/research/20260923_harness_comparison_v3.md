# 各 coding agent 横向比较 v3（10 家 harness 全景 + 扩展 5 家：cc/codex/opencode/crush/atomcode）

- 日期: 2026-09-23（第三轮，v2 复核版之后同日增量）
- 作者: 灵克 (lingclaude)
- 前作链: `20260921_coding_agent_expansion.md`（首版+路线图）→ `20260923_harness_comparison_v2.md`（落地对账）→ `20260923_coding_agent_recompare.md`（弱点复核）→ 本篇
- 本篇性质: **不再重述 15 家机制全景**（对侧沿用 09-21 快照，48h 内无重拉，标记 ✅已验证(历史,锚点=09-21 快照)）；只做三件事：
  1. 对账 v2/recompare 之后 4 小时内 lc 的最新状态（HEAD `9790999`，✅已验证(本轮)）
  2. 给出**收敛后的独有优势矩阵**与**残余差距终版清单**
  3. 裁定 lc 今后优化方向（与今日 4 项挂账裁定对齐）

## 一、lc 最新状态锚点（HEAD 9790999，本轮 git 实证）

| 事项 | 状态 | 证据 |
|------|------|------|
| spec_decision 消费层 | **10 消费点，主线默认转正** | `9790999` 翻 spec_decision/bounded_compaction 默认开；`c3102e1`/`6f56726`/`e6acfde` 依次铺开工具输出精简/安全守门/有界压缩/Noul 门控/工具调用风险门控 |
| fast lane 死插片 | **裁定选 (b) 摘除降级**（due 2026-11-21） | 今日用户裁定入册 `arch_audit_task/fastlane_dead_plugin_downgrade_20260923`；理由=spec_decision 概率层已兜底，fast lane 冗余 |
| SFT checkpoint | 挂账 | `arch_audit_task/spec_decision_sft_checkpoint_pending_20260923`——7 消费点现走本地先验概率，lc 会话数据 SFT 后质量兑现 |
| 循环控制流决策点 | 单独立项 | `arch_audit_task/loop_control_flow_separate_project_20260923`（防回归放大） |
| loop_body 纯度 | **仍 586 行**（hooks.py 227 行 seam） | `wc -l` 本轮实测；vs atomcode/Pi ~120 行，纯度差 ~5x |
| 飞轮 | f1/f2/f3 落地 | `2ba7571`/`da08bfc`/`cdec8d9`——归因链+学习固化+换目标函数 |

## 二、15 家横向：收敛后的定位矩阵

> 机制层面对账结论与 v2/recompare 一致（8 弱点关 7.5），本版不再逐项重复，只给终版矩阵。

### lc 独有或领先（15 家均无对位）

| 优势 | 15 家最近对位 | 差距性质 |
|------|--------------|----------|
| **编队+治理**（跨进程成员身份/审计台账/配额治理） | Orca 编排层（无成员身份）、codex Starlark 沉淀（无台账） | 结构性独有 |
| **NanoJev 三原语概率层**（10 消费点、0 LLM token、0.0275ms/组） | 无对位——cc/codex 的判断点全是 LLM 直判 | 独有；质量待 SFT 兑现 |
| **自进化飞轮**（ExperimentLedger 归因链+离线回放评分） | Penguin 3-role RSI（无归因账本） | 独有；收益待审计 |
| **投机扇出调度**（difficulty 分桶+confidence 门控+重复度终止） | cc N-agent 并行=人工编排 | 独有；收益待实测 |
| **门禁→转正→挂账闭环**（env 门禁/默认开/到期二选一，铁律约束） | 15 家 feature flag 无到期裁定机制 | 治理方法论独有 |

### 残余差距（终版，按差距排序）

| # | 差距 | 领先者 | 实况 | 归属 |
|---|------|--------|------|------|
| 1 | 循环纯度 586 vs ~120 行 | atomcode/Pi | seam 已接口化，治理逻辑仍内联 | P1 主干债 |
| 2 | 引擎循环本体不可热切 | Pi 双代 | 09-21 §3.1 裁定的唯一真依赖项，依赖 #1 | P1（#1 完成后） |
| 3 | fork/revert/share 无用户面 | codex/opencode | 后端全在，缺 3 条命令 | P0 半天活 |
| 4 | 沙箱网络 allowlist 维 | cc | approval_matrix 有沙箱维无网络维 | P1 |
| 5 | 跨 provider 切换语义保留未实测 | crush | /model pin 已通，语义等价断言缺 | P1 一条测试 |
| 6 | 门禁默认关堆积（credential_pool/worktree/投机扇出） | — | fast lane 已裁定，其余三项待收益审计 | **P0 最大治理债** |

## 三、lc 今后优化方向（与今日 4 项裁定对齐的终版）

> 主题延续 v2 判断：**不再开新机制**。三个词：转正、纯化、兑现。

### P0（一周）——转正与关门

1. **门禁收益审计**（挂账 #1 的姊妹项）：credential_pool / worktree / 投机扇出三项逐一实机收益审计，达标默认开或挂账拆除——今日 fast lane 裁定已立模板（「消费方实测 or 摘除」，豁免不得永续）。
2. **fork/revert/share 用户面**：`/fork <tag>` `/revert <ordinal>` `/share` 三命令，关闭差距 #3。
3. **飞轮第一份收益周报**：ExperimentLedger 数据回流，回答「自优化是否真赚钱」——没有这份报告，f1/f2/f3 是负债。

### P1（两周）——纯化与兑现

4. **loop_body 586→~200 行**（状态外置，opencode 四件套 delta 文档已给顺序）；同时**遵守今日裁定**：循环控制流中的"是否行动/是否安全"决策点不并入 spec_decision 主线，单独立项防回归放大。
5. **引擎循环双代热更**（依赖 #4，复用 plugin/TUI 蓝绿语义）。
6. **spec_decision SFT 数据管道启动**：10 消费点跑起来后 lc 会话数据即训练料——先落「数据沉淀→SFT→checkpoint 热替换」管道，等数据量达标即兑现挂账 #3 的概率质量。Laya ECE 0.081 vs 自研 0.246 的 3 倍差距只有这条路能关。
7. 沙箱网络 allowlist + 跨 provider 语义等价断言（小活，顺手关 #4/#5）。

### P2（一月）——B 路线物理化

8. **族协议标准化**：proj_agent_gateway 已能 headless 调度 opencode/crush/atomcode；把消息/回执/审计三格式抽公开协议草案。
9. **SQLite 事实源** + **治理层作为可挂载中间件输出**（C 路线形态）。

### 不采纳清单（显式留档，避免下轮重新起疑）

Penguin GOAL.yaml（fact-check 证实不存在该文件，已被 goal_receipt 两值语义覆盖）、OMH repair card、codex-host 投影、hermes-webui、omarchy 懒加载、agent-harness .harness/ 单源。

## 四、一句话结论

三轮对比的轨迹：09-21 **补债**（8 弱点开清单）→ 09-23 **对账**（7.5 项关闭，两天干完两周的活）→ 09-23 晚 **收敛**（fast lane 摘除裁定宣告「机制扩张期」结束）。lc 与 15 家的差距只剩两个数字：**586 行循环体**和 **0.246 的 ECE**——前者靠状态外置，后者靠 SFT 兑现，其余全是执行而非设计问题。战略判断不变：B 路线（Agent 编队操作系统），且 spec_decision 10 消费点 + gateway 三件套已让它从蓝图变成可运行的物理雏形。
