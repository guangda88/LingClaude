# 第二层 + 第三层试验田方案（v0.1 草案，2026-09-18，待用户裁决）

> **定位**：四层宏图的**第二层**（Coding Agent 全插件化）与**第三层**（能力域插片化）
> 的试验田选型方案。承接 `CAPABILITY_PLUGIN_PLAN_L3.md`（第三层五域方案）与
> `AGENT_PLUGIN_INTEGRATION_PLAN.md`（第一层 20 实体），本方案回答两个待裁问题：
> 1. 第二层以谁作试验田？——**atomcode 作 lc 插片**（主）/ VS Code（备选）；
> 2. 第三层以谁作试验田？——**harness / 浏览器**（接在 cap/browser + cap/crew 之后）。
>
> **理论底座**：铁律 5 双向插片互认（我可为人之插片，人可为我之插片）+
> 铁律 6 信任等级（atomcode/VS Code/harness 都是外部黑盒，按 T2/T3 定级）+
> 铁律 7 域前缀（agent/、cap/、os/）+ 铁律 8 隔离故障域（缺席=absent 不假活）。
> **复用资产**：agent_lingxi 三件套（插片样板）、bus_bridge（LingBus→插片路由）、
> work_claim/worktree_node（并行修改防护）、mcp-wrap skill（MCP 封装模板）、
> cap_infer + os_resource（本会话刚落的两个插片，铁律 8 缺席查的两个实例）。
> **与第一层关系**：第一层 20 实体（12 子 + 8 工程）已批量接入 lc（lingxi 首片已通）；
> 本方案是**第二层/第三层的试验田**，不是新增第一层实体——atomcode 是 lc 自身
> （AtomCode 即本仓 agent），不是灵族成员，不入 ling_org，走**插片协议互认**
> 而非 org_member record。

---

## 一、第二层试验田：atomcode 作 lc 插片（主方案）

### 1.1 命题（铁律 5 双向互认的具体实例）

**"lc 作为 atomcode 的插片，atomcode 调用 lc 的各层守卫"** —— 这是铁律 5
"可把我变成别人的插片"的**反向实例**：

- 正向（已做）：atomcode 把 atomcode 自身做成 ling-term-mcp，挂进 lc 的
  AGENT 缝（agent/atomcode，等价于 agent/lingxi 的第二个实例）——
  atomcode 是 lc 的插片。
- **反向（本方案）**：lc 把自己做成 atomcode 的一个 plugin/extension，
  让 atomcode（在 gitee 完全开源、缺各层守卫）能调用 lc 的
  铁律 1-8 + N1-N7 + M1-M6 + J1-J5 守卫体系 + 台账（arch_ledger/ling_org）。
  **lc 是 atomcode 的插片**。

### 1.2 为什么 atomcode 比 VS Code 更适合作第二层试验田

| 维度 | atomcode | VS Code |
|------|----------|---------|
| 开源形态 | gitee 完全开源（用户裁定） | 微软闭源（仅 extension API） |
| 可观测性 | 进程内插片，可全审计 T1 | 跨进程 extension host，T2 契约审计 |
| 信任等级（铁律 6） | **T1 全审计**（代码在手） | **T2 契约审计**（黑盒但可观测履约） |
| 协议 | MCP（atomcode 已封 ling-term-mcp，同协议族） | extension API（非 MCP，需另封适配层） |
| 各层守卫缺口 | atomcode 缺 lc 的铁律 1-8 守卫，可注入 | VS Code 缺的不是守卫，是"灵元 record 体系" |
| 封装难度 | **低**（MCP 封装模板 mcp-wrap skill 直接套） | 高（VS Code extension 是 .vsix 包，需 Node/TypeScript 重写） |
| 拔插（铁律 5） | L1 替换（MCP 可换） | L3 缺席裸奔（extension 拔了功能消失） |

**结论：atomcode 主、VS Code 备选**。atomcode 的优势=同协议族（MCP）+
T1 全审计（代码在手）+ 封装难度低（mcp-wrap skill 直接套）。
VS Code 的优势=用户基数大、生态成熟，但封装成本高（非 MCP）+ T2 黑盒。
若 atomcode 实例跑通（铁律 5 反向互账成功），再推广到 VS Code（同协议族
MCP 可复用 mcp-wrap 模板，T2 降级处理）。

**备选实例接线（2026-09-18 已落，第二层 VS Code 口径）**：
- 接线文件：`.vscode/mcp.json`（workspace 级，VS Code 1.99+ MCP 客户端标准位；
  用户级在 `~/.config/Code/User/mcp.json`，本机 VS Code 1.137.0 在位）；
- server 复用：同一 lc-guard 薄壳（`lingclaude/plugins/agents/lc_mcp_guard/server.py`，
  6 守卫工具，atomcode/VS Code 两个宿主共用一个 server——薄壳零业务判断，
  宿主差异只在客户端配置层）；
- **信任等级差异（铁律 6）**：atomcode 宿主=T1 全审计（gitee 开源代码在手）；
  VS Code 宿主=T2 契约审计（闭源宿主黑盒，但 MCP 履约可观测——tools/list、
  调用延迟、超时均可审）。**同一 lc-guard server 对两个宿主 T 级不同是合法的**：
  T 级定的是"缝后宿主"的可信度，不是 server 自身；
- 拔插等级：两宿主均 L1（拔掉 VS Code 的 lc-guard 配置，VS Code 主干照跑）。

### 1.3 插片设计（atomcode-as-lc-plugin）

**缝 key**：`agent/atomcode`（铁律 7，域前缀 agent/，与 agent/lingxi 同域）
**信任等级**：T1（atomcode 仓代码在手，J1-J5 全套 + M 件套）
**拔插等级**：L1 替换（MCP 插片可换其他 Coding Agent，接口一致即不崩）
**传输**：MCP（复用 ling-term-mcp 协议族，atomcode 已封 ling-term-mcp）

**能力面（atomcode 调用 lc 的各层守卫）**：

| 能力 | lc 侧实体 | 协议面 |
|------|----------|--------|
| 守卫查询（J1-J5） | `scripts/self_audit_trigger.py` + `tests/test_iron_law_guards.py` | MCP 工具 `lc_audit` |
| 台账查询（arch_ledger） | `data/arch_ledger/`（StateStore） | MCP 工具 `lc_ledger_query` |
| 灵族组织（ling_org） | `data/ling_org/`（org_member record） | MCP 工具 `lc_org_query` |
| 铁律条文（铁律 1-8） | `docs/LINGYUAN_IRON_LAW.md`（文本） | MCP 工具 `lc_law_read` |
| work_claim 锁（并行修改） | `plugins/agents/work_claim.py` | MCP 工具 `lc_workclaim_*` |
| 返审触发（SDT-lc-006） | `scripts/self_audit_trigger.py` | MCP 工具 `lc_audit_trigger` |

**封装路径**：
1. 在 atomcode 仓（gitee）建 `atomcode/lc-plugin/` 目录，MCP server 骨架
   （复用 mcp-wrap skill 模板，输入 7 项：名称/入口/传输/能力/信任/拔插/探针）；
2. 该 MCP server 的每个工具**转发到 lc 侧对应实体**（lc 仓内建一个
   "对外 MCP 面"薄壳，把 J1-J5/台账/灵族组织/铁律条文 暴露为 MCP 工具）；
3. atomcode 侧声明 `lc-plugin` 为可插拔 extension（拔了 atomcode 主干照跑，L1）；
4. **铁律 5 双向互账**：lc 建 `federation_pair` record（type=federation_pair，
   key=atomcode-lc，state=proposing），atomcode 侧同步建对偶 record，
   N1 守卫周期对账（缺一/不一致即警）。

**关键判断**：atomcode 仓是外部仓（gitee），**不能直接在 atomcode 仓改代码
（审计权限：有权全族代码审计，无权自主修改其他成员代码）**——本方案的
atomcode 侧改动需 atomcode 成员自做，lc 侧只做"对外 MCP 面"薄壳 +
federation_pair 互账 record。

### 1.4 缺席语义（铁律 8 隔离故障域）

- atomcode 仓 MCP 探针失败 → `agent/atomcode` 插片 absent（不假活），
  lc 主干照跑（L1 可替换为其他 Coding Agent，如 agent/lingxi）；
- lc 侧"对外 MCP 面"探针失败 → 该薄壳 absent，atomcode 侧看到 lc 缺席
  （对称故障域，互账 federation_pair state → drift）；
- 域级圈死：atomcode 故障只影响 `agent/atomcode` 域，不扩散到
  agent/lingxi、cap/infer、os/resource 等其他域（域前缀即故障域）。

### 1.5 验证（M3+M5 双绿终审）

- M3 依赖方向：lc 主干（core/）不 import atomcode 仓任何符号（依赖封闭）；
- M5 截肢：unregister(agent/atomcode) 后 lc 主干照跑（L1 替换，J2 可证）；
- N1 互账：federation_pair 两侧 record 对账（缺一/不一致即警）；
- N4 缺席查：atomcode absent → 该域 query 全返 absent，域外不受影响。

---

## 二、第三层试验田：harness / 浏览器（接在 cap/browser + cap/crew 之后）

### 2.1 命题

第三层 `CAPABILITY_PLUGIN_PLAN_L3.md` 已列五域（cap/infer、cap/crew、
cap/browser、os/resource、cap/hermes）。其中：
- **cap/infer 已落**（本会话 Phase A，灵元推理栈封插片 + 16 测试）；
- **os/resource 已落**（本会话 Phase C，SeamType.RESOURCE + 四探针 + 15 测试）；
- **剩三域**：cap/crew（lingflow 工作流）、cap/browser（CDP 动作面）、
  cap/hermes（Hermes 面板对偶，可选）。

本方案的第三层试验田=**cap/browser（浏览器动作面）+ cap/crew（harness
工作流）**，理由：
- cap/browser 是"补动作面"（web 工具只有查询无动作），CDP 9228 现成，
  封装工作量主要在 manifest 与测试（关键判断：第三层不是新建能力，
  是封已存在的能力面）；
- cap/crew（harness）是"让空协议有血肉"（CrewSeam 协议已就绪但零实现，
  铁律 4 回收候选），落 lingflow 工作流引擎为首插片。

### 2.2 cap/browser（浏览器动作面，第三层主试验田）

**缝 key**：`cap/browser`（铁律 7，域前缀 cap/）
**信任等级**：T2（CDP 引擎是本机可读进程，契约审计）
**拔插等级**：L1 替换（playwright headless 可替换 CDP 窗口版，接口一致）
**传输**：直调（CDP 9228 现成）

**能力面（5 原语起步，对齐 Hermes 实测路径）**：
- page_load / screenshot / click / fill / evaluate
- 探针：CDP 端点可达性 + /json/list 返回 tab 列表
- **写操作（点/填/发）= work_claim 锁住 DOM 写（写前认领，铁律 8 操作域）**
- record 化：每次动作记 `browser_action` record（含"动作前后 DOM 快照 hash"，
  J5 行为级：成功不能只锚定 exit，要锚定"页面状态确实变了"）

**关键风险（如实声明）**：
- CDP 9222 归 yai-cdp-guard（无头守护），窗口版调试 Chrome 用 9228；
  默认 profile 目录会禁用调试端口，必须用非默认目录（会话简报踩坑 #6）；
- 浏览器动作是写操作，work_claim 锁必须覆盖同 tab 双写（N4 互斥查：
  同 tab 双写即警）；
- DOM 快照 hash 是 J5 行为锚（"页面状态确实变了"），需 playwright/CDP
  截图 + hash 计算，首次封装工作量在这里。

### 2.3 cap/crew（harness 工作流，第三层辅试验田）

**缝 key**：`cap/crew`（铁律 7，域前缀 cap/）
**信任等级**：T2（lingflow 仓内可读，契约审计）
**拔插等级**：L1 替换（CrewSeam 协议一致即可换实现）
**传输**：直调/MCP（lingflow 工作流引擎）

**能力面（CrewSeam 协议，已就绪但零实现）**：
- create_crew / dispatch / status（core/seam.py:144-146 已声明）
- 落 lingflow 工作流引擎为首插片（让空协议有血肉，铁律 4 回收候选解除）
- **同仓双向（铁律 5 双向互认的同仓实例）**：lc 的 crew 能力调 lingflow，
  lingflow 的任务能力也经第一层（agent/lingflow）回投 lc
- record 化：每次 dispatch 记 `crew_run` record（crew_id/task/mode/exit），J4 语义

**关键依赖（如实声明）**：cap/crew 依赖 agent/lingflow 插片已挂
（第一层 20 实体），若 lc 尚未完成 12 子批量接入，cap/crew 顺延至
agent/lingflow 在位（CAPABILITY_PLUGIN_PLAN_L3.md §五 风险 4）。

### 2.4 cap/hermes（可选对偶，最低优先级）

接在 atomcode 实例之后（cap/hermes 的"lc 即面板插片"对偶，验证铁律 5
"可把我变成别人的插片"命题）：若 atomcode 反向互账（federation_pair
atomcode-lc）成功，cap/hermes 有现成对偶实体（atomcode 已把 lc 注册为
它的插片）；若未成功，Phase D 顺延。

---

## 三、分阶段落地（一层养一层）

| 阶段 | 内容 | 工时 | 依赖 | 新增/复用 |
|------|------|------|------|----------|
| Phase B1 | cap/browser 封插片（CDP 动作面，5 原语 + DOM 快照 hash） | 2 天 | CDP 9228 现成 | 新增 |
| Phase B2 | cap/crew 封插片（lingflow 工作流，CrewSeam 首实现） | 2 天 | agent/lingflow 已挂 | 新增 |
| Phase B3 | atomcode 反向互账（federation_pair atomcode-lc，lc 侧 MCP 薄壳） | 1 天 | atomcode 仓 MCP server（atomcode 成员自做） | 新增（lc 侧薄壳 + 互账 record） |
| Phase D（可选） | cap/hermes 对偶（接 atomcode 实例） | 1 天 | Phase B3 成功 | 新增 |

**铁律 5 双向互账（federation_pair 状态机）**：
- proposing → paired（两侧 record 一致，N1 周期对账）
- paired → drift（N5 行为指纹侦测契约漂移，旧结论标 stale）
- drift → restored / broken（对账失败/删库恢复各有确定语义）
- broken → dissolved（单向撤回即整对失效，不留半边）

---

## 四、须用户裁决的问题

1. **第二层试验田**：atomcode 主 / VS Code 备选，同意？
   （atomcode 仓 MCP server 需 atomcode 成员自做，lc 侧只做"对外 MCP 面"
   薄壳 + federation_pair 互账 record；lc 无 atomcode 仓写权限，审计权限边界）
2. **第三层试验田**：cap/browser（主）+ cap/crew（辅）顺序，同意？
   （cap/browser 先行=CDP 现成、工作量主要在 DOM 快照 hash；cap/crew 依赖
   agent/lingflow 在位，若第一层 12 子批量接入未完成则顺延）
3. **Phase B3（atomcode 反向互账）**：是否本轮就做 lc 侧 MCP 薄壳？
   （lc 侧薄壳=把 J1-J5/台账/灵族组织/铁律条文 暴露为 MCP 工具，
   复用 mcp-wrap skill 模板；atomcode 仓 MCP server 另起）
4. **work_claim 锁 DOM 写**：cap/browser 的写操作（click/fill/evaluate）
   走 work_claim 锁（铁律 8 操作域），同 tab 双写即警（N4 互斥查），同意？

*本方案为 v0.1 草案，叙事层；事实层以 `data/arch_ledger/` + `data/ling_org/`
台账为准（H17）。裁决后入册 arch_law_revision（drafted），Phase B 开工前
M3+M5 双绿终审（铁律 1 接缝协议）。*
