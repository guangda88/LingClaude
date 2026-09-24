# 灵族未来发展·5+1 层架构修订规划 v0.4

> **状态**：草案 v0.4（v0.3 + 灵克会话对 codex 四项最低清偿的缺口补齐；修订处以 `[v0.4修订]` 标注）
> **裁决**：**2026-09-24 用户裁决（主人亲裁）——议题 A：v0.4 规划基线照案批准（7 硬轨+2 缓轨+3 砍轨 成立）；议题 B：gov/ 第六域升格通过，铁律 7 扩为六域 `{core, agent, cap, os, hw, gov}`**。回写已执行：`LINGYUAN_IRON_LAW.md` :116-117（含 gov 物理落地规范与守卫/仪表分层要求）、`lingclaude/core/seam.py` DOMAIN_NAMESPACES（执法器同步）。本文中"须主裁裁决后方可回写"等表述自此转为流程留痕，不再是待办。
> **作者**：灵克会话（基于 atomcode 授权）
> **生成日期**：2026-09-24
> **修订历程**：用户原话 4 层 → 诊断为 5+1 层 → 派 5 家 agent 评议（ac/cc/crush/opencode 实返，codex 超时）→ v0.1 整合 → **v0.2（同日）：灵克逐项核对仓内现状，修正 3 处过时/失准断言 + 3 处结构性修订** → **v0.3（同日）：codex 重派评议返回（判定"需修订后再裁"），吸收其事实修正层发现：N6 撞号改 N9、§1.4/§1.5/§九.2 降级同步、"铁律 7 候选"标签修正、循环除名质疑补注** → **v0.4（同日）：补齐 codex 四项最低清偿中 v0.3 缺失的 2 项——A③ 轨裁剪结论（§四新增）+ gov/ 第六域定性为"需主裁升格裁决"（§1.0/§6.1/§十）；§9.3 盲点区两条假设语气改为实测结论（.github/workflows 与 benchmarks 已实查）；§十重排为"提交主裁一次裁决"工作流**

---

## 〇、一句话总览

**5+1 层架构 = `L0 治理宪章（gov/ 域，横切）` + `L1 core/` + `L2 agent/` + `L3 os/` + `L4 cap/`（拆 L4a/L4b）+ `L5 hw/`**；临界路径是 **L1 → L2**（`coding.py` 装配外移 + `agent-gateway` 熔断与 manifest 化），与铁律 7（已升格，`LINGYUAN_IRON_LAW.md` :117，2026-09-17 用户裁决）的 5 域前缀同构，**不另起栈**；`[v0.4修订]` **定性声明：gov/ 第六域是对铁律 7 已升格裁决（封闭五域 `{core, agent, cap, os, hw}`）的扩展，属修宪而非文档回写，须主裁升格裁决后方可回写铁律**（codex 最低清偿③，本文 §1.0/§6.1/§十 均已同步标注）。三个月内必须落地的硬约束是 **token schema 收尾**（`[v0.2修订]` `session_token_sink` 已于 2026-09-23 落地（commit a66e04c，StateStore 路径已见真实数据）；剩余收尾 = session_history.json 老路径接入（仍 0 含字段，须避免双轨台账）+ cost 换算字段）+ **rss_watchdog 挂 gov/ 命名空间**（`[v0.2修订]` 已是 RSS 看门狗守卫（ops/rss_watchdog.py，500MB/1000MB 阈值+基线重置），`[v0.3修订]` 修正：代码自称"N6"与铁律注册表撞号（N6=口径成本互证，:392；N1-N7 全占，N8 留给环境守卫）——本规划改挂 **N9**，代码侧改名挂 arch_audit_task 待办；待办是从 ops 域挂 gov/ 前缀 + 补启动 free-RAM 门，防 8 月 21GB thrash 复蹈）+ **5+1 ↔ ROADMAP.md 映射回写**（否则规划与既有路线图互相不可见）。`[v0.4修订]` **A③ 裁剪结论**（codex 最低清偿④，详见 §4.0）：3 月 P0 表由 12 轨裁为 **7 硬轨 + 2 缓轨 + 3 砍轨**，裁剪判据 = 外部依赖链长度 × 代码级/文档级；冻结范围明确为**新功能代码冻结**，评审/文档/回写类不冻

---

## 一、修订版 5+1 层定义

### 1.0 命名规范统一（采纳 4 家 agent 共识）

- 铁律 7（`[v0.3修订]` 已升格，非候选；`LINGYUAN_IRON_LAW.md:117`）锁定的 5 域前缀 `{core, agent, cap, os, hw}` 是命名学基石，本规划**不另立域**
- **新增 L0 专用前缀 `gov/`**（治理/宪章/守卫，落 seam 时用 `gov/iron_law_N`、`gov/guard/N9` 等专用命名空间；不接受无前缀的 L0 插片）。`[v0.4修订]` **该扩域属修宪**：铁律 7 是主裁升格的封闭五域，增第六域须走升格裁决，非灵克可自行回写（codex 最低清偿③；行动项见 §十.1）
- 横切层（测试/可观测/商业化/环境约束）走 `gov/xcut/<name>` 命名，与 L0 合并表达（`[v0.2修订]` 仅作文档归类标签，收敛声明见 §二）
- `[v0.4修订]` **gov/ 物理落地规范 = N8/N9 立项前置**（新增）：现行 L0 守卫代码住在 `lingclaude/core/governance.py`（§1.1 载体行），而 `lingclaude/gov/` 目录不存在——gov/guard/ 建起后守卫代码到底住 governance.py 还是 gov/guard/，构成规划自身批评的"双轨台账"结构。须先裁决：gov/ 目录结构、注册方式、与 governance.py 的关系。**建议裁决口径：governance.py 留守卫框架与装载入口（core 域单例不变），gov/guard/<编号>/ 只放具体守卫实现与其台账，注册仍收口铁律注册表**——此口径随主裁裁决一并确认
- `[v0.4修订]` **N9 层级拆分前置**（新增）：铁律中 N5-N7 为仪表层级（"只观测不裁决"）。rss_watchdog 现状（500MB 告警/1000MB 硬限告警）是**观测行为**，待补的 free-RAM 门（free<1GiB 拒启动）才是**守卫动作**。N9 立项须拆开标注：free-RAM 门 = 守卫（gov/guard/），RSS 增长观测 = 仪表（保持 ops 线或 gov/gauge/ 命名，随主裁裁决）；或由主裁在 gov/ 命名规范中显式允许"守卫+仪表复合体"登记

### 1.1 L0 治理宪章（gov/ 域，横切）

| 项 | 内容 |
|---|---|
| **本体** | 8 铁律 + J1-J5 判据 + N1-N7 守卫 + 4 横切层（测试/可观测/商业/环境） |
| **载体** | `docs/LINGYUAN_IRON_LAW.md`（本体）+ `data/arch_ledger/arch_law_revision/`（台账） + `lingclaude/core/governance.py`（守卫代码）|
| **范围** | 横切 L1-L5；J1-J5 验证全部插片合规，N1-N7 守护运行时行为 |
| **新增交付（本规划建议）** | ① `gov/iron_law_N` 命名空间前缀规则段写回铁律第 117 行 `[v0.4修订]` **修宪项：须先经主裁升格裁决（见 §1.0/§6.1/§十.1），裁决前冻结**；② 候选铁律 5/6/7/8 升格裁决（9-17 已升格，候选区保留作档案）；③ J6 用户价值判据（建议加入）；④ N8 环境约束声明守卫（crush 提案） `[v0.4修订]` ⑤ gov/ 物理落地规范 + N9 守卫/仪表层级拆分口径（新增前置，见 §1.0） |
| **不接受的内容** | 治理类插件不应混入 core/agent/cap/os/hw 任一域，按 gov/ 单独入册 |

**owner**：主人（升格/作废裁决）+ 灵安（执行）/ 灵信（消息总线审计）| **critical path**：不卡后续

### 1.2 L1 灵元主干（core/ 域）

| 项 | 内容 |
|---|---|
| **本体** | `[v0.2修订]` `lingclaude/core/` 实测 **106 个 .py**（本表"9 件套"系 governance 子集口径：governance / governance_verifier / approval_matrix / role_separation / reasoning_chain / scheduler / session / session_runtime / wakeup_channel；其余为 guard/gray_zone/behavior/context_engine 等域内模块，盘点时须全口径）+ 12 灵子（plugins/agents/agent_*，实测 13 个含 agent_zhibridge）+ 24 成员账本 + 30 份台账 |
| **范围** | 主干状态机 + 装配器 + 接缝注册表 + 灵子挂载点 |
| **cargada** | mountable_agent_kernel_proposal.md 的「治理为锚的内核+可挂载件」是 L1 战略线；**LC-Mount 协议评审通过 + M1 装配外移** 是 L1 第一交付 |
| **不属 L1（已澄清）** | 模型 provider 层（`model/factory.py`，独立）/ 自优化线（daemon/optimizer/flywheel，消费方）/ plugins/ 现有插片（形态已符合，只补 manifest） |

**critical path** = L1 内 `coding.py` 装配外移到 `coding_wiring.py`（proposal M1，**前置 = 等自优化线收口**——必须遵循，不并发以避免 7637cc3 装配时序坑复发）

**owner**：灵克 core 团队 / Codex 辅助 LC-Mount 协议评审

### 1.3 L2 外部 coding agent（agent/ 域）

| 项 | 内容 |
|---|---|
| **本体** | `lingclaude/plugins/agents/proj_agent_gateway/`（server.py 554 行 + plugin.py 213 行 + manifest.agent.json）+ 5 家外部 agent（cc/codex/crush/opencode/ac） |
| **范围** | 统一调用面 + per-agent 熔断 + per-agent 配额预算 + record 值域适配 |
| **现成** | 5 工具（agent_invoke/agent_chat/agent_batch/agent_status/agent_list）已实装 + 5 家真跑通 + 24h review 落地 |
| **缺口** | ① per-agent 熔断缺（cc 实测已多次撞 Claude 配额墙）；② 5 家 manifest 未对齐 5 域前缀；③ 5 家去留/替补机制无（一家断供无 quorum 规则） |
| **接受批判** | "统一接口会掉进最小公分母陷阱"（ac 提）—— agent-gateway 只做"声明-验证"不做"执行代理"，各家安全模型各自保留，网关只校验 record 签名 |

**critical path** = L1 完成 + agent-gateway 加 per-agent 熔断 + 5 家 manifest 化

**owner**：灵克 gateway owner / `[v0.3修订]` Crush「挂载纪律哨兵」降为咨询输入，manifest 复核改灵克自审（理由见 §五 owner 原则）

### 1.4 L3 跨 OS 互通（os/ 域）— 修订最关键

| 项 | 内容 |
|---|---|
| **本体（目标态）** | 不同 OS 设备同时指挥和操作 |
| **现状（必须诚实声明）** | `LINGSHELL_DESIGN.md` 仅"单 OS 进程管理"，**不是跨 OS**——必须在文档首页加现状声明，避免 ROADMAP 误把 LINGSHELL 当 L3 已完成 |
| **现实策略** | 三个月不交付"跨 OS 设备发现"，先交付 **单 OS 多进程租户隔离**（与 mountable proposal §九.2 gate 进程级裁决同构）：bwrap 沙箱（ROADMAP P1-4）+ 多租户会话隔离 + 1 台异 OS 设备最小验证（1 条指令 + 1 次回执） |
| **硬约束** | E1-E5 环境约束（urandom/只读 FS/bwrap/ulimit/网络隔离）在跨机场景会全部复现且加倍；N8 守卫建议新增「新域插片必须带约束环境声明」 |
| **不接受的内容** | LINGSHELL_DESIGN 不当脚手架直接扩——从零起步 |

**owner**：`[v0.3修订]` 灵克（CC/opencode 降为多环境适配咨询输入，理由见 §五 owner 原则）

### 1.5 L4 软件应用域（cap/ 域）— 采纳 cc 提案拆 L4a/L4b

| 亚层 | 内容 | owner |
|---|---|---|
| **L4a 能力插件** | `cap_browser`（CDP 9228，5 原语）/ `cap_infer`（llama-server OpenAI 兼容）/ `cap_inspect`（SeamRegistry/TaskRouter/CredentialPool 三路只读）— T3 契约审计 | 灵克只出 cap seam 规范 |
| **L4b 商业应用** | `proj_lingkang`（灵康，库型 linghealth，对外商业）/ `proj_linglv`（灵律，service linglaw + chroma_db + frpc）/ `proj_lingyi`（灵依，库型，退出 12 子并入对外）/ `proj_lingchu/dai/shang/sheng/shi` 5 个 T3/L3 载体缺席（域待补）| 各产品 owner 自持 |

**L4 不应混**的核心规则（采纳 ac/crush）：
- cap 域单域上限 50 工具（防 G3 工具数基线 604→613 膨胀速度拖垮 core 启动）
- 超限走插片延后装载（已有 12a1251 模式可复用）
- L4a 与 L4b 不准相互 import 内部符号（N7 横向耦合禁令）

**L4 应用接入协议缺位**（采纳 ac/opencode）：
- 当前 cap_browser 用 CDP、cap_infer 用 OpenAI 兼容、其他 proj_* 各自定义——缺**统一应用接入协议**
- 建议参考 Anthropic MCP / OpenAI function calling 风格，3 个月内出 1 版草案

**owner**：L4a=灵克 + Lingflow；L4b=各项目 owner（灵康组/灵律组/...）

### 1.6 L5 硬件资源（hw/ 域 + 算力供应）

| 子层 | 内容 | owner |
|---|---|---|
| **hw/ 域** | `os_resource/plugin.py`（4 探针 nvidia-smi/loadavg/free/df，仓内唯一硬件观测）+ `rss_watchdog.py` `[v0.2修订]` **已是 RSS 看门狗守卫**（ops/rss_watchdog.py，500MB 增长告警/有效硬限 ≥ 增长线，基线重置语义已实现）`[v0.3修订]` **编号修正：代码自称"N6"撞铁律注册表 N6=口径成本互证（N1-N7 全占，N8 留给环境守卫），本规划改挂 N9，代码侧改名挂 arch_audit_task**——待办非"从零升格"而是：① 从 ops 域挂 `gov/guard/N9` 前缀；② 补启动时 free-RAM 门（free<1GiB 拒启动，coli 复蹈防护）+ `cgroup MemoryMax=22G`（scripts/atomcode-cgroup）+ `urandom_shim.c`（LD_PRELOAD） | 灵安 + 硬件台账 owner |
| **算力供应** | `model/openrouter_oauth.py` + `intelligent_router.py` + `task_router.py` + `behavior_aware_router.py`（4 件套） + 5 家算力供应商（GLM/MiniMax/VolcEngine/Kimi/Agnes，**名单/合同状态未列明——本规划要求补登**） | 独立 ops role（不建议任何灵子独占） |

**硬缺口（必须三个月内解决）**：
- HAL 接缝白皮书（采纳全部 4 家共识）— GPU 调度/内存预占/cgroup 限额的标准接口
- 5 家算力供应商 SLA 台账 + 合同状态
- 故障域仲裁自动化（8/7 OOM 活锁靠人工 swapon+earlyoom 恢复，**0 自动化**）
- 真实算力账单 schema（24h review 已暴露：117421 sessions 中 0 含 token 字段；`[v0.2修订]` session_token_sink 已落地 commit a66e04c，本缺口收敛为"老路径接入 + cost 换算"，见 L0/L_observ）
- 网络层（proxy/vpn/frpc）/ 存储层（PG/Redis/Chroma/TOS）未独立登记——必须从 proj_linglv/agent_lingzhi 抽出

**owner**：灵安 + 硬件台账 owner / ops role（算力供应）

---

## 二、4 横切层（与 L0 合并表达，走 gov/xcut/）

> `[v0.2修订]` 收敛声明：gov/xcut/ 四个命名空间**不新增概念负荷**——四件事分别已有现成载体：test→J 系判据扩展（J6）、observ→record schema 字段（trace_id / token 字段）、biz→J6 用户价值判据的用户故事、env→N8 守卫。gov/xcut/ 仅作文档归类标签，**不设独立守卫编号、不立独立台账**，避免与铁律判据/守卫双轨。若主裁认为连标签都不需要，可整体并入铁律章节，成本为零。

| 横切层 | 内容 | 与 L0 关系 |
|---|---|---|
| **gov/xcut/test** | L_test 测试横切：J1-J5 守卫本身是测试，但缺端到端集成跨层覆盖；plan_c_v4 7 件套含 tests 但不够 | 与 L0 同构，治理元数据 |
| **gov/xcut/observ** | L_observ 可观测性：token schema 盲区补 `[v0.2修订]` session_token_sink 已落地（commit a66e04c），剩 session_history.json 老路径接入 + cost 字段 / 跨层 trace_id / 算力账单 schema | L0 判据补成本维度 |
| **gov/xcut/biz** | B0 商业化：proj_lingkang/lv 商业化路径（订阅/API 接入/白标？） + 用户画像/反馈回路 + L4 应用缺需求来源 | 用户价值判据 |
| **gov/xcut/env** | N8 环境约束声明：新域插片必须带约束环境声明（E1-E5）；跨 OS 测试矩阵（bwrap/只读 FS/网络隔离×多设备） | 守卫机制 |

---

## 三、Critical Path 与依赖图

```
[L1 coding.py 装配外移 (mountable M1)]
  ├─ 前置：自优化线收口（lingxi 5-failed 教训不可并发）
  ├─ 后置：MountManifest dataclass (M2)
  └─ 后置：lsp_ab.py 泛化通用 gate (M3) + gateway T3 件接入 (M4)
                ↓
[L2 agent-gateway 加 per-agent 熔断 + 5 家 manifest 化]
  ├─ 前置：L1 mountable 协议到位（否则挂载无形式）
  ├─ 前置：gate 进程级裁决（proposal §九.2 已定）
  └─ 并行：crush.opencode 互补验证
                ↓
[L0 铁律 7 命名空间 + J6/N8 + token schema 必修]
  ├─ 横切所有其他层，无前置
  └─ 阻断一切：schema 不修则算力账单失真
                ↓
[L3 单 OS 多进程租户隔离 + 1 台异 OS 设备最小验证]
  ├─ 前置：N8 环境约束声明
  └─ 后置：跨 OS 设备发现（3 个月后再说）
                ↓
[L4 cap_browser 端到端 + L4b proj_lingkang 商业化路径]
  └─ 前置：统一应用接入协议草案
                ↓
[L5 HAL 接缝白皮书 + 算力 SLA 台账 + 故障域仲裁]
  └─ 前置：token schema 收尾（v0.2：sink 已落地）+ ops role 建立
```

**关键依赖**：
- L1 装配外移 = 全链 critical path（4 家共识）
- L2 gateway 熔断 = agent 域唯一入口（cc/opencode 共识）
- L0 token schema = 阻断一切算力账单的硬约束

---

## 四、3/6/12 月里程碑表

### 4.0 A③ 轨裁剪结论 `[v0.4修订]`（codex 最低清偿④）

> codex 判定 v0.3 "11 轨×冻结条款自洽"不成立：缺裁剪结论。本节补齐。

**裁剪判据**：`外部依赖链长度 × 代码级/文档级`。外部依赖链越长、越是纯代码级改动，风险越大、越该缓/砍；文档级回写与解耦项优先保留。codex 建议只保 6 硬轨并把 ROADMAP 回写也降为 best-effort——**本规划不采纳后者**：ROADMAP 回写恰是 §〇 三大硬约束之一（"规划与既有路线图互相不可见"），且是 12 轨中成本最低（纯文档、零代码风险）的一条，降级它等于让最便宜的活儿没人干。

| 轨 | 处置 | 理由 |
|---|---|---|
| L0 token schema 收尾 | **硬轨（保）** | 阻断一切算力账单的硬约束 |
| L0 rss_watchdog 挂 gov/（N9） | **硬轨（保，范围含 §1.0 两条前置）** | 8 月 21GB thrash 复蹈路径阻断 |
| L1 mountable M1 装配外移 | **硬轨（保）** | 全链 critical path |
| L2 agent-gateway 熔断 + manifest 化 | **硬轨（保）** | agent 域唯一入口 |
| L3 单 OS 多进程租户隔离 | **硬轨（保）** | 多租户基础 |
| ROADMAP 5+1 映射回写 | **硬轨（保）** | 解耦硬约束，纯文档、成本最低 |
| L3 1 台异 OS 设备最小验证 | **缓轨（延至 M4-M6）** | 有价值但不卡他轨，环境依赖（真机到位）优先级让位 |
| L4 统一应用接入协议草案 | **缓轨（延至 M4-M6）** | 依赖 Lingflow 出协议（外部链最长），不押关键路径 |
| L4a cap_browser 真实场景端到端 | **砍（本周期不做）** | 依赖统一协议草案 → 依赖 Lingflow，外部链最长且是代码级改动 |
| L5 5 家算力供应商台账 | **砍（本周期不做）** | 依赖 SLA 台账方法论先行（codex C3：HAL 优先级略高估） |
| L5 GPU 评估 v4 重做 | **砍（本周期不做）** | 重、且依赖台账先行；v3 结论沿用至重启 |
| L0 J6 判据草案 / N8 守卫草案 | **保留（并入 L0 轨，不单列）** | 文档级，随铁律回写包一并提交 |

**裁剪后冻结条款澄清**（原 §七"新功能冻结到 M1 落地"收窄）：冻结范围 = **新功能代码**；评审/文档/回写/守卫加固类工作不冻（否则与上表硬轨自相矛盾）。原 12 轨表保留为附录性历史记录（已行内划线标注处置），处置以本表为准。裁剪总账：**7 硬轨 + 2 缓轨（移 M4-M6）+ 3 砍轨（本周期不做）**。

### 3 个月内（M1-M3，P0 级）

| 层 | 里程碑 | 验收 |
|---|---|---|
| **L0** | ① 5+1 层宏图附录写回 `LINGYUAN_IRON_LAW.md`（gov/ 命名规则段）`[v0.4修订]` **前提 = gov/ 扩域升格裁决通过（修宪项，见 §1.0/§十.1）**；② J6 用户价值判据草案；③ N8 环境约束声明守卫草案 | 文档可查 |
| **L0** | token schema 收尾 `[v0.2修订]`（session_token_sink 已落地 commit a66e04c；剩：session_history.json 新记录接入 token 字段、turn_cost_usd 换算、灵忆镜像消费——避免 StateStore/老路径双轨）| 新会话双路径均含字段，无双轨台账 |
| **L0** | rss_watchdog 挂 gov/ 守卫 `[v0.3修订]`（守卫已实存；编号修正：代码自称 N6 撞号，改挂 **N9**，代码侧改名挂 arch_audit_task；剩：ops→gov/guard/N9 前缀迁移 + 启动 free-RAM 门）`[v0.4修订]` **范围追加 §1.0 两条前置：gov/ 物理落地规范裁决 + N9 守卫/仪表层级拆分标注** | 8 月 21GB thrash 复蹈路径阻断 |
| **L1** | mountable_agent_kernel M1（coding.py 装配外移 coding_wiring.py）| G8 快照守卫不破 + 全量回归绿 |
| **L2** | agent-gateway 加 per-agent 熔断 + 5 家 manifest 化（带 gov/agent/ 前缀）| data/agent_runs/ 多 agent 并发可比 + 5 家熔断 PASS |
| **L3** | 单 OS 多进程租户隔离（bwrap + 多租户会话隔离）| E2/E5 审计项不破 |
| **L3** | ~~1 台异 OS 设备最小验证（1 条指令 + 1 次回执）~~ `[v0.4修订]` **缓轨→移 M4-M6**（不卡他轨，真机环境依赖让位） | 移期 |
| **L4a** | ~~cap_browser 端到端跑通 1 个真实场景（如下单或政务查询）~~ `[v0.4修订]` **砍/本周期不做**（依赖统一协议→依赖 Lingflow，外部链最长且代码级） | 移出 |
| **L4** | ~~统一应用接入协议草案 v0.1~~ `[v0.4修订]` **缓轨→移 M4-M6**（Lingflow 侧输入先行，不押关键路径） | 移期 |
| **L5** | ~~5 家算力供应商建台账（价格/配额/故障域）~~ `[v0.4修订]` **砍/本周期不做**（SLA 方法论先行，codex C3） | 移出 |
| **L5** | ~~GPU 评估 v4 重做（含 SLA/账单/合同）~~ `[v0.4修订]` **砍/本周期不做**（重+依赖台账先行；v3 结论沿用至重启） | 移出 |
| **ROADMAP** | ROADMAP.md 加附录"P0-P3 落入 5+1 层映射表" `[v0.4修订]` **硬轨保留**（纯文档、成本最低、解耦硬约束；不采纳 codex 降 best-effort 建议，理由见 §4.0） | 文档可查 |

### 6 个月内（M4-M6，P1 级）

`[v0.4修订]` 承接 §4.0 缓轨：**L3 1 台异 OS 设备最小验证**、**L4 统一应用接入协议草案 v0.1** 自 3 月表移入本表（M1-M3 硬轨不破前提下尽早排入）。

| 层 | 里程碑 |
|---|---|
| **L1** | mountable M2（MountManifest dataclass + 内置工具首批 2 件） |
| **L1** | mountable M3（lsp_ab.py 泛化通用 gate） |
| **L1** | mountable M4（gateway/外部 MCP 以 T3 件接入统一 manifest） |
| **L2** | 3 家 agent 跑通"挂载-卸载-再挂载"截肢测试 |
| **L2** | agent-gateway 加 record 值域适配器（三算子） |
| **L3** | 跨 OS 设备发现（zhineng-ai + DELL R730 真机）|
| **L4a** | cap_browser + cap_infer + cap_inspect 全部接契约漂移 record |
| **L4b** | proj_lingkang 商业化路径明确（订阅/API/白标三选一） |
| **L5** | HAL 接缝白皮书 v1 |
| **L5** | 故障域仲裁自动化（RSS 涨超阈值自动 earlyoom/cgroup 缩） |
| **横切** | L_test 端到端集成测试跨层覆盖 |
| **横切** | L_observ 跨层 trace_id 全链接通 |

### 12 个月内（M7-M12，P2 级）

| 层 | 里程碑 |
|---|---|
| **L0** | 候选铁律 9+ 探索（按需）|
| **L1** | 分形递归到第 3 层（训推域）|
| **L2** | 5 家外部 agent 全部接入 + quorum 替补规则 |
| **L3** | 跨 OS 多设备协同 Demo（3+ OS 同时控制）|
| **L4a** | cap 域扩到 10 个能力插件（办公/即时通讯/支付/出行/...） |
| **L4b** | proj_lingkang/lv 出第一批收入 |
| **L5** | 网络/存储层独立登记 + 接缝白皮书 |
| **横切** | B0 商业化达成首批付费用户 |
| **横切** | 学术论文 AAMAS 2027 / NeurIPS 2027 投稿 |

---

## 五、Owner 矩阵

| 层 | 主 owner | 副 owner | 关键判定 |
|---|---|---|---|
| **L0** | 主人（升格/作废裁决） | 灵安（执行） + 灵信（消息总线审计） | 升格须主裁 |
| **L1** | 灵克 core 团队 | `[v0.2修订]` codex 评审降为咨询输入（外部 agent 无 SLA，不得押在关键路径上）`[v0.3修订]` 补注（吸收 codex 评议对降级逻辑的反向质疑）：降级依据是**外部 agent 无 SLA 的结构性事实**，非"超时未返"这一单次表现——后者只作为佐证不作为理由，避免"迟到者被除名、理由引用迟到本身"的循环自强化。若外部 agent 后续建立可用性 SLA，可重新评估升回 | coding.py 外移须灵克亲自 |
| **L2** | 灵克 gateway owner | `[v0.2修订]` Crush「挂载纪律哨兵」降为咨询输入（理由同上）；复核改由灵克自审 | 每家 manifest 须过审 |
| **L3** | `[v0.2修订]` 灵克（外部 agent 无 SLA，owner 只写灵族成员） | CC/opencode 降为多环境适配咨询输入 | 实机验证须双路径通过 |
| **L4a** | 灵克 + Lingflow | cap_browser 作者 | 统一协议草案须 Lingflow 出 |
| **L4b** | 各项目 owner（灵康组/灵律组/...） | 灵克只出 cap seam 规范 | 商业化路径各组自决 |
| **L5** | 灵安 + 硬件台账 owner | 独立 ops role（算力供应） | 算力合同须对外协调，不独任 |
| **横切 gov/xcut/test** | 灵安 + 灵克 | 灵信（CI 红线） | 测试红线须灵安 |
| **横切 gov/xcut/observ** | 灵克 + 自优化线 | — | token schema 收尾（v0.2：sink 已落地） |
| **横切 gov/xcut/biz** | 主人 + 灵康组 | — | 商业模式须主人决 |
| **横切 gov/xcut/env** | 灵安 | — | N8 守卫须灵安 |

> `[v0.2修订]` 原则：**owner 一律为灵族成员/主裁；外部 agent（cc/crush/opencode/codex）只作咨询输入**。本规划在 L5 批评算力供应商"SLA 未列明"，对自身依赖的外部 agent 须同样适用——外部 agent 可用性无 SLA，不得作为关键路径 owner。

---

## 六、与既有蓝图的对齐与冲突

### 6.1 LINGYUAN_IRON_LAW.md 回写建议

| 位置 | 建议 |
|---|---|
| `:117`（铁律 7 域前缀） | `[v0.4修订]` **定性修正：此为修宪项（对已升格封闭五域的扩展），加 gov/ 域前缀段落须先经主裁升格裁决，裁决通过后本行才可执行**（codex 最低清偿③；非灵克可自行回写的文档动作，与 §十.1 合并为同一次裁决议题） |
| 候选区（5/6/7/8） | 候选铁律 5/6/7/8 已升格（9-17 完成），候选区改为"实证记录区" |
| 第二节审计判据 | 加 J6 用户价值判据草案（cc/opencode 提案） |
| N1-N7 守卫 | 加 N8 环境约束声明守卫（crush 提案） |

### 6.2 ROADMAP.md 回写建议

| 位置 | 建议 |
|---|---|
| 文末 | 新增附录"5+1 ↔ P0-P3 映射表"，把现有 P0-P3 全部项映射到 core/agent 域；os/cap/hw 域另开 P 级队列 |
| §P0-4 | 不变（snapshot/rewind 已落地）|
| §P1 | 不变（沙箱/code intel/schedule 等在 L1 范畴）|
| §P2 | 加 LC-Mount 协议评审通过后的 MountManifest 实施 |
| §0.5.0 候选 | 把 L2 agent-gateway 熔断 + L0 token schema + L3 单 OS 多租户 列为 0.5.0 候选 |

### 6.3 mountable_agent_kernel_proposal 整合

| 位置 | 建议 |
|---|---|
| §十（新增） | "本方案在 5+1 宏图中的位置"：明示内核 = L1，挂载件 = L2/L3/L4/L5 |
| §六.1 | 不动（coding.py 装配外移是 L1 critical path）|
| §九 3 张台账 | 挂到 L1 账本体系下，避免双轨台账（ac 提案）|

### 6.4 LINGSHELL_DESIGN.md 回写建议

| 位置 | 建议 |
|---|---|
| 首页加现状声明 | "本设计限单 OS；跨 OS 见 L3 专项（5+1 层宏图）" |

### 6.5 HARDWARE_EVALUATION_V3_GPU.md

- 标注"2026-04 评估已过时，L5 需重做（v4 含 SLA/账单/合同）"
- 评估期：每季度刷新定为 SDT（crush 提案）

### 6.6 ENVIRONMENT_CONSTRAINTS_AUDIT_v1.md

- E1-E5 直接成为 L2 gateway 的已知约束清单，避免重复踩 urandom/bwrap 坑（ac 提案）

---

## 七、风险与降级路径

| 风险 | 概率 | 影响 | 降级路径 |
|---|---|---|---|
| L1 装配外移期间自优化线又启并行 | 中 | 高（lingxi 5-failed 复蹈）| work_claim 强锁 + `[v0.4修订]` **新功能代码冻结到 M1 落地（评审/文档/回写/守卫加固不冻，与 §4.0 裁剪自洽）** |
| token schema 收尾遇迁移硬骨头 `[v0.2修订]` | 中 | 高（账单失真连锁）| 新会话先行 + 老会话只统计存留 |
| L2 gateway 5 家熔断参数未对齐 | 中 | 中（agent 撞配额墙）| 按 ac 提："声明-验证"模式最小公分母，per-agent 配额预算先做 1 家 |
| L3 跨 OS 设备 ssh 凭据泄漏 | 低 | 高 | 凭据走 L5 cgroup/keyring，不存明文 |
| L4a cap_browser 反爬/登录态丢失 | 中 | 中（cap_browser 教训页永不水合）| 登录态外存 StateStore 不混数据；UI 操作超时 5s 必降级 |
| L5 5 家算力供应 SLA 失真 | 中 | 中 | 每月对账一次 + 合同状态强制挂账 |
| L5 GPU 资源被打穿（L1/L2）| 中 | 极高（8 月 21GB 11 天假活锁复蹈）| `[v0.3修订]` rss_watchdog（守卫已实存）挂 gov/ 前缀（编号改 N9，代码自称 N6 撞号）+ 启动 free-RAM 门（free<1GiB 拒启动）|

---

## 八、3 横切层（商业/可观测/测试）补充

### 8.1 商业化（B0）

**问题**：proj_lingkang/lv 商业化路径不明（订阅？API 接入？白标？）。无 B0 则 L4/L5 算力预算无依据。

**3 个月里程碑**：
- 灵康：明确订阅 vs API vs 白标三选一
- 灵律：律师反馈迭代（已在轨）
- 灵依：SLA 保障条件（已分离完成）

**12 个月里程碑**：
- 灵康/LV 出第一批付费用户
- token 定价模型上线（依赖 token schema 收尾完成）

### 8.2 可观测性（L_observ）

**问题**：token schema 盲区 + 跨层调用链无统一 trace_id。

**3 个月里程碑**：
- token schema 收尾 `[v0.2修订]`（见 L0 横切：sink 已落地，剩老路径接入 + cost 字段）
- 跨层 trace_id 在 record schema 加 `trace_id` 必选字段（与 token 同步）
- `[v0.2修订]` 安全审计衔接（2026-09-24 灵克全仓审计两项落点）：
  ① session_token_sink 落盘路径 `~/.lingclaude/state/session_token_usage/` 数据含 per-session token/成本，写入侧须继承 0600 权限纪律（当前 umask 下为 644，同 `.lingclaude/` 现状——审计 M3）；
  ② L5 算力 SLA 台账立项时，一并收口 `LINGCLAUDE_API_KEYS` 撤销失效问题（api.py `_VALID_API_KEYS` 只增不减，进程存活期已撤销 key 永不失效——审计 L1+），账单失真根因之一是 key 无生命周期。

**6 个月里程碑**：
- M6 仪表接入 token/cost 趋势
- L2 各 agent 回填 token_usage（Crush 原生支持，先做样板）

### 8.3 测试（L_test）

**问题**：J1-J5 守卫本身是测试，但缺端到端集成跨层覆盖。

**3 个月里程碑**：
- plan_c_v4 7 件套的 24 用例扩展到 60+
- 加跨层集成测试（cross_seam 端到端）

**6 个月里程碑**：
- 截肢测试自动化（M5）
- 行为锚测试（M4 换域测试）

---

## 九、评估来源（透明披露）

### 9.1 已读文档（避免与既有蓝图冲突）

| 类别 | 文档 |
|---|---|
| 哲学/治理 | `LINGYUAN_IRON_LAW.md`, `COMPREHENSIVE_PLAN_v1.md` |
| 蓝图/路线 | `ROADMAP.md`, `plan_c_v4/PLAN_C_V4_BLUEPRINT.md`, `HARDWARE_EVALUATION_V3_GPU.md`, `LINGSHELL_DESIGN.md` |
| 战略/定位 | `research/20260923_mountable_agent_kernel_proposal.md`, `research/20260923_jev_laya_decision_paradigm_24h_review.md`, `theory/LINGCLAUDE_VS_PEERS.md`, `AI_INTROSPECTION_RESEARCH_PROPOSAL.md`, `ACADEMIC_PUBLISHING_STRATEGY.md` |
| 用户/CLI | `USER_MANUAL_CLI_v0.4.md` |
| 监督/交接 | `HANDOFF_SUPERVISOR_20260911.md`, `audit/ENVIRONMENT_CONSTRAINTS_AUDIT_v1.md` |
| 会话/记忆 | `SESSION_MANAGEMENT.md` |
| 拉起指南 | `lacp/WAKEUP_GUIDE_5ZOMBIE_4DEAD.md` |

### 9.2 4 家外部 agent 评议（综合）

| Agent | 视角 | 关键贡献 |
|---|---|---|
| **ac** (AtomCode) | 可核验交付边界 | rss_watchdog 升 L0 守卫（v0.2 修订：守卫已实存；`[v0.3修订]` 编号再修正：代码自称 N6 撞号，改挂 N9）；L3/L5 双 1-台-异-OS 设备最小验证；agent-gateway 最小公分母陷阱 |
| **cc** (Claude Code / M3) | L1↔L2 边界双重视角 | L4 拆 L4a/L4b；J6 用户价值判据；L_test/L_observ 横切；5+1 ↔ 既有蓝图映射表 |
| **crush** (Charm Crush) | 挂载件纪律 | L0 加 gov/ 前缀；L3 os/hw 边界裁决"凡跨设备即 os，凡本机资源即 hw"；N8 环境约束守卫 |
| **opencode** (SST OpenCode) | L2 governance + token 成本 | agent-gateway 三算子适配器 + 截肢测试；"声明-验证"模式；lingflow 视角下 L1/L2 owner |

**未返回**：codex（180s 超时，worktree `gw-codex-6899be` 已建未提交）。战略层需补时建议重派。`[v0.3修订]` 已于同日重派补齐（评议产物：`.atomcode/notes/20260924_codex_review_5plus1_v0.2.md`，判定"需修订后再裁"，其 N6 撞号等发现已吸收进本版）。

> `[v0.3修订]` 口径澄清：上表为**评议贡献的历史记录**（谁提了什么），非任务分派；"哨兵/打头"等字样不构成 owner 任命。现行 owner 只认 §五矩阵（灵族成员，外部 agent 一律咨询输入）。

### 9.3 本规划未覆盖的盲点（透明披露）

- **跨仓视角未纳入**：lingmemory/lingcode/lingflow 三个仓内材料未读，handover 等跨仓接缝可能漏判
- **tests/ 内容未深读**：覆盖率基线/模糊测试/属性测试未评估
- **.github/、CI/CD、容器化**：`[v0.4修订]` **已实查（2026-09-24）**：`.github/workflows/` 实存 `ci.yml`（8.5KB，2026-09-24 08:07 更新）、`ci.yml.bak`、`codeql.yml`——CI 已实存且活跃，属 **L_test 横切**范畴，后续 CI 红线变更须过灵安（对应 §五 owner 矩阵 L_test 行）；未发现 k8s/terraform/argo 容器编排层，**5+1 架构无需为容器化单开一层**
- **scripts/ 全集未读**：除 self_audit_trigger.py + atomcode-cgroup/shim 外，可能漏运维脚本
- **benchmarks/ 目录未查**：`[v0.4修订]` **已实查（2026-09-24）**：`benchmarks/` 实存 laya_cpu_bench 三代脚本+结果 JSON、ttft 探针、laya_fastlane_quality.py、humaneval 子目录、payload 系列样本。**结论：性能基准已有实存资产，L5 算力账单 schema 设计时应纳入这些基准的计量口径**（虽为 L5 轨缓置项的前置输入，随重启时取用）；无独立负载生成层，不构成架构层缺口
- **arch_ledger/ 9 子目录**只看了核心几个（agent_registry/arch_exemption/arch_reference/contract_drift/family_carriers/federation_pair/verify_log/work_claim/work_claim_log 共 9 类）
- **24h review 数字未交叉验证**：5.94× / 9.91× / 8× 是估算口径

---

## 十、下一步动作（按优先级）

1. `[v0.4修订]` **【第一优先·本周】提交主裁一次裁决两案**（合并 v0.3 原第 1/3 项，codex 最低清偿③落实）：
   - **议题 A**：本规划 v0.4（5+1 架构方向 + §4.0 八硬轨裁剪）是否作为未来发展基线。裁决前请先过目 `[v0.2修订]`/`[v0.3修订]`/`[v0.4修订]` 标注处——v0.1 三处事实断言已过时；v0.3 吸收 codex 事实修正层；v0.4 补齐 codex 四项最低清偿全部 4 项；
   - **议题 B（修宪项）**：gov/ 第六域升格裁决（铁律 7 封闭五域 `{core, agent, cap, os, hw}` → 六域，扩展为 `{core, agent, cap, os, hw, gov}`）+ gov/ 物理落地规范（governance.py 留框架、gov/guard/ 放实现，口径见 §1.0）+ N9 守卫/仪表层级拆分口径。**不通过则 §6.1 回写表第一行与 N8/N9 落地动作保持冻结**；
   - 拆散裁决的风险：若 gov/ 规则段先回写、后裁决否决，将造成铁律"既成事实"，故禁止任何一方（含灵克）在裁决前回写 :117。
2. `[v0.4修订]` **【本周·无前置】ROADMAP.md 加 5+1 映射附录**（纯文档、不依赖议题 A/B 结论、成本最低——保留为独立动作，理由见 §4.0）
3. `[v0.4修订]` **【下周·待议题 A 通过】token schema 收尾立项** `[v0.2修订]`（拆 arch_audit_task；sink 已落地，立项范围为老路径接入 + cost 字段 + 灵忆镜像消费）
4. `[v0.4修订]` **【下周·待议题 B 通过】rss_watchdog 挂 gov/ 守卫立项** `[v0.3修订]`（守卫已实存；编号改挂 **N9**，代码自称改 N9 走 arch_audit_task（他成员代码）；范围 = ① 自称改 N9 + ② gov/guard/N9 前缀迁移 + ③ free-RAM 门 + `[v0.4修订]` 追加 ④ N9 守卫/仪表层级拆分标注 + ⑤ 依赖 gov/ 物理落地规范）。**若议题 B 否决，降级路径：free-RAM 门按普通加固在 ops 线落地，不进 gov/ 命名空间，thrash 防复蹈目标不受影响**
5. **下周**：L1 装配外移 M1 立单（待自优化线收口，与议题 A/B 无耦合）
6. ~~2 周内：重派 codex 补战略层评议~~ `[v0.3修订]` **已完成**（同日重派成功，产物 `.atomcode/notes/20260924_codex_review_5plus1_v0.2.md`，发现已吸收进本版）
7. **1 月内**：L2 gateway 熔断 + 5 家 manifest 化立项
8. `[v0.4修订]` **【1 月内·待 L5 重启】benchmarks 计量口径纳入 L5 算力账单 schema**（§9.3 实查结论的落地动作；L5 轨本周期缓置，仅登记不实施）

---

## 附：本规划与用户原话的对齐确认

| 用户原话 | 本规划对应 |
|---|---|
| "第一层是 12 子和基础设施 对外项目" | L1 灵元主干（12 灵子 + core 域基础设施 + proj_* 对外项目）|
| "第二层的外部 coding agent" | L2 外部 coding agent（agent/ 域）|
| "第三层是不同系统间互通" | L3 跨 OS 互通（os/ 域，澄清为"不同 OS 设备同时指挥和操作"）|
| "第四层是软件和硬件" | 拆为 L4 软件应用域（cap/ 域）+ L5 硬件资源（hw/ 域 + 算力供应）|
| （隐含）治理 | 新增 L0 治理宪章（gov/ 域，横切）|

**与原话的对齐度**：5+1 层完全覆盖用户 4 层原意，且：
- 把含糊的"不同系统间互通"明确为"跨 OS"
- 把模糊的"软件和硬件"明确为"软件应用 vs 硬件资源"两亚层
- 把"治理"独立为横切的 L0（采纳 4 家 agent 共识）

— v0.4 草案，2026-09-24，待主裁与回写（v0.1→v0.2 灵克实测修订、v0.2→v0.3 codex 评议吸收、v0.3→v0.4 codex 四项最低清偿缺口补齐（A③ 裁剪 + gov/ 修宪定性 + §9.3 实测），均以行内标注保留痕迹；codex 评议原文见 `.atomcode/notes/20260924_codex_review_5plus1_v0.2.md`；v0.3 全文见 `20260924_future_development_5plus1.md` 作历史档案）