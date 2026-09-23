# 锚定方案：治理层为锚的「可挂载 agent 操作内核」

- 日期：2026-09-23
- 作者：灵克会话（路 B·战略线开工稿，评审前置文档，未动任何生产代码）
- 状态：**PROPOSAL**——评审通过前不得据此改代码

---

## 一、问题定义

灵克当前是一个「单体 agent」：操作能力（bash/read/edit/LSP/审批/会话）与治理逻辑（审批矩阵、风险门、敏感路径门、审计入账）生长在同一棵 runtime 树上。战略方向是把它反转为**内核 + 可挂载件**：

> **内核只保留治理锚点（审批/schema/审计/生命周期），一切操作能力皆可插拔。**

对标参照：VS Code 的 activation events + contribution points；MCP 的 server 生命周期。差异化定位：**治理为锚**——挂载件默认不可信，能力经治理层授予，行为全程入账。这与灵克既有铁律体系（plugins 的 trust_level/plug_level）天然同构，不是新造范式，是把已验证的 plugin 纪律**反向内化到 core**。

## 二、既有资产盘点（全部实锚，这是本方案的可行性基础）

勘察发现可挂载化**不是从零开始**，仓库里已有三代 seam 先例：

| # | 资产 | 位置 | 形态 | 对本方案的启示 |
|---|---|---|---|---|
| 1 | **SeamRegistry 热拔插** | `lingclaude/engine/tools.py`（`_ToolSeamProxy`，`execute` 优先查外部换血代理，指向自身则 graceful degrade） | 同名覆盖即热拔插，防自指死循环 | 挂载/卸载的**运行时语义已解决** |
| 2 | **plugins 铁律体系** | `lingclaude/plugins/`（`trust_level=T3`、`plug_level=L2 缺席降级`、缝 key 域前缀、J1 薄壳纪律、J4 每调用入账） | 每个插片自述铁律锚点（见 `proj_agent_gateway/plugin.py` 头注） | 治理元数据的**词表已存在**，内核化时直接复用 |
| 3 | **LoopHooks 可选协议** | `3082d7f`：`pre_decide/post_check/decide_continue`，`hasattr` 探测、默认直通零变化 | 可选钩子 + fail-soft | 挂载件**不实现也不崩**的协议范本 |
| 4 | **工具注册数据与运行时解耦** | `engine/tool_registration.py`（SPECS 表，AST 机械提取，G8 守卫锁定） + `engine/coding_wiring.py`（装配 spec） | 声明式注册：name/params/handler_attr/security_scope/concurrency_safe | **挂载清单（manifest）的雏形已在** |
| 5 | **gateway 薄壳** | `plugins/agents/proj_agent_gateway/server.py`（554 行，FastMCP stdio 5 工具，J4 落盘 `data/agent_runs/`） | core 零 diff、缺席降级、每次调用入账 | 「外部 agent 也是一种挂载件」的活样本 |
| 6 | **A/B 闸门先例** | `lingclaude/engine/lsp_ab.py`（本次路 A 落地） | env 三态闸门 + 装配点接管 + fail-open | 挂载件灰度开停的**标准姿势** |

**结论：内核化 = 把 1–4 从「特例机制」升格为「统一挂载协议」，5 是外部形态，6 是发布纪律。**

## 三、内核边界定义（什么进内核，什么做成挂载件）

### 进内核（治理锚，不可卸载）

| 锚点 | 现状 | 说明 |
|---|---|---|
| 审批矩阵 | `core/approval_matrix.py` + `data/approvals.json` | 权限档位（auto/ask/strict/plan）全局语义（`0ac7b21` 已裁定维持全局） |
| 风险门管线 | `tool_pipeline`（sensitive_path_guard → risk_guard，见 `coding.py`） | PreToolUse 门控序列是挂载件能力的**唯一发放通道** |
| 审计入账 | J4 范式（`data/agent_runs/`、flywheel、`session_journal`） | 每次能力调用必须落 record，失败也记（不假活） |
| 工具注册表 | `ToolRegistry`（register/unregister/handler 解耦） | 挂载件唯一合法的注册入口 |
| 会话生命周期 | session_id 统一根源（`e7d1dcd`） | 挂载件状态按 sid 隔离，遵循「与 session 生命周期绑定」契约 |

### 做成挂载件（操作能力，可插拔）

| 能力 | 现状 | 挂载化差距 |
|---|---|---|
| 内置工具（bash/read/edit/…） | SPECS 表声明式，但启动时**全量硬装** | 差 manifest 声明 + 闸门；注册机制已就绪（路 A 已验证 A/B 闸门模式可行） |
| LSP 引擎 | 惰性初始化 + session pool（`coding.py:68-72`） | 差缺席降级语义（lsp server 不可用时应整件 absent 而非逐调用报错） |
| STT/TTS 等外设 | mixin 内联 | 同上 |
| 外部 agent（cc/codex/…） | gateway 薄壳已是插片 | 差统一 manifest（现靠 .mcp.json + plugin 头注铁律） |
| LoopHooks 扩展点 | 已是可选协议 | 无差距，作为挂载件接入内核事件的范本 |

### 明确不内核化（防过度设计）

- 模型 provider 层（`model/factory.py`）——已独立，不动
- 自优化线（daemon/optimizer/flywheel）——消费方，不是挂载对象
- plugins/ 现有插片——形态已符合，只补 manifest，不重写

## 四、挂载协议草案（Mountable Capability Protocol，MCP-内部版命名避让，下称 **LC-Mount**）

每个挂载件必须提供一份声明（对齐 tool_registration 的 ToolSpec 字段风格）：

```python
@dataclass(frozen=True)
class MountManifest:
    key: str                  # 域前缀缝 key，如 "tool/lsp"、"agent/gateway"（铁律 7 词表）
    trust_level: str          # T1 内置 / T2 受控 / T3 外部（复用 plugins 词表）
    plug_level: str           # L1 必需 / L2 缺席降级 / L3 纯增强
    provides: tuple[str, ...] # 贡献的工具名 / 钩子名（activation 语义）
    security_scope: str       # 复用 SPECS 词表：execute/read/modify/...
    audit: str                # 入账槽位（J4 record 类型）
    gate: str = "off"         # 发布闸门：off / ab-control / ab-treatment / on（复用 lsp_ab 三态）
```

内核侧四条纪律（全部是既有铁律的升格，无新发明）：

1. **能力发放唯一通道**：挂载件贡献的工具必须过 `tool_pipeline` 门控序列，绕行即违规（现状已是如此，_manifest 化后加守卫）
2. **缺席降级**：L2 件 absent 时内核零感知（LoopHooks hasattr 范式 + gateway N4 探针范式合并）
3. **账面强制**：无 audit 槽位的挂载件拒绝挂载（fail-fast 而非 fail-open——与记账失败 fail-open 区分：**挂载时严格，运行时宽容**）
4. **灰度闸门**：每个件的 gate 字段控制注册行为，treatment 组自动接记账器（路 A 的 `lsp_ab.py` 泛化版）

## 五、与现有体系的关系

- **plugins/**：不是替代是收编。现有插片头注里的铁律文字 → 结构化 MountManifest；`agent_family.py`/`bus_bridge.py` 保持为 plugin 侧编排层
- **SeamRegistry**：TOOL 槽位热拔插语义保留，成为 LC-Mount 的运行时通道之一；MountManifest 是声明层，Seam 是执行层
- **.mcp.json / 外部 MCP**：外部 MCP server 是 T3 挂载件的最简形态，gateway 证明可行；不需要 MCP 协议改动
- **审批体系**：不变。挂载件让「谁在请求能力」更清晰（缝 key 域前缀已进审批记录），审批矩阵本身零改动

## 六、风险与耦合点（最大三个）

1. **coding.py 装配 God-object**：`CodingRuntime` 同时是 mixin 聚合体 + 注册器 + 管线持有者。挂载协议落地前需先做「装配外移」——`coding_wiring.py` 已有 WiringSpec 机制，是现成的承接面。**这是实施期最大工作量**，建议独立任务单
2. **G8 黄金快照守卫**：SPECS 表是 AST 机械提取 + 快照对照的。挂载件动态注册会与「静态快照」守卫冲突，需给守卫加「manifest 内挂载件」白名单区，否则每次挂载都要改快照
3. **审批语义漂移**：条目按 sid 隔离（`e7d1dcd`）+ 档位全局（`0ac7b21`）的格局下，挂载件的 gate 若也按 sid 走，会出现「A 会话装了件 B 没装」——需裁定 gate 是进程级（建议）还是会话级。~~挂账至方案评审，不预设~~（已裁定：进程级，见 §九.2）

## 七、实施分期（评审通过后生效）

| 期 | 内容 | 验收 |
|---|---|---|
| **M1**（立单，先不动代码） | coding.py 装配外移到 coding_wiring.py，行为零变化 | 全量回归绿 + G8 快照不变 |
| **M2** | MountManifest dataclass + 校验器，先把 SPECS 表 32 条工具**原样**翻译成内置 manifest（零行为变化） | 快照守卫改造完成，回归绿 |
| **M3** | lsp_ab.py 泛化为通用 gate 机制，lsp/STT 两个件先行迁移，A/B 数据收 1–2 周 | control/treatment 数据可比 |
| **M4** | gateway/外部 MCP 以 T3 件接入统一 manifest | 探针缺席降级实测 |
| **每期纪律** | 单独提交、单独台账、过期不加期 | 台账驱动 |

## 八、需要评审拍板的三个问题

1. M1 装配外移是否先行（它是纯重构，无功能收益但有风险，不先做则 M2–M4 全部垫在 God-object 上）？
2. gate 进程级还是会话级（§六.3）？
3. plugins 现有插片的 manifest 补录，是随 M2 一起做（一次性）还是按插片活跃度分期（渐进）？

## 九、裁决记录（2026-09-23，用户授权采纳灵克建议）

| 问 | 裁决 | 核心依据 |
|---|---|---|
| 1. M1 先行 | **是，带前置条件** | ① `coding_wiring.py`/`WiringSpec` 实锚存在，承接面为真；② 装配时序坑有复发实据（`7637cc3` 提交信息：`_setup_tools` 先于 `session_id` 赋值踩中 AttributeError）——不外移则复发模式固化；③ **前置条件：等隔壁自优化线（工作区 6 文件在途）收口后动工**，纯重构不与并行开发交织 |
| 2. gate 级别 | **进程级** | ① 进程 = 可预见演进中的**租户边界**：多租户正确形态是多进程编排（凭据/文件/审批上下文隔离只有进程线可靠），gate 跟进程走即天然跟租户走；会话级 gate 在多租户下语义反了（租户 A 装的件不能因 B 开新会话而不可见）；② 与 `0ac7b21`「档位=策略全局、条目=按会话隔离」同构——gate 是策略不是数据；③ 会话差异由**缺席降级**表达（用不用可会话级，装没装是全局事实），升级路径 = 进程默认之上加租户 override 层，不动装配骨架 |
| 3. manifest 补录 | **随 M2 一次性** | ① 拒绝双轨：渐进 = 校验器长期容忍「有/无 manifest」并存，即 `audit-gate-schema-drift` 台账刚经历的病灶；② 插片量小（agents/tools 两目录），一次性成本可控；③ 冷插片用 `activation: manual` 字段表达活跃度，不用时间表表达 |

**三问裁决同步动作**：M1 立单（含前置条件）、gate 裁决台账（resolved，含多租户重审条款）、manifest 补录挂 M2 台账——三张均已入 `data/arch_ledger/arch_audit_task/`。

**gate 重审触发条件**（防「无多租户」式现状论据复发）：灵克进入单进程多租户部署形态，或灵原编排要求租户级挂载差异时，本条裁决必须重开推演。
