# 灵族时空结构多维图 — Schema 设计稿（v0.1，2026-09-17 用户裁决六维定稿）

> 状态：设计稿 → v0.1（用户裁决：六维暂定，维度随认识深入可添可减；落点 lingclaude 仓；
> **退出的两子：灵依、灵通+**（用户 2026-09-17 裁定）；灵依以 AI 健康助手身份并入对外工程项目
> （与灵康/灵律/灵声/灵视/灵触/灵戴/灵商同列 external_project，内部同时消费）；
> **智桥 zhibridge 为 12 子成员**（用户权威裁定））
> 定位：灵族组织本体的记录系统（record 集），不是文档。
> 落库形式：StateStore（lingclaude 仓 data/ling_org/，type 见下），复用灵元 2T3A 原语。
> 权威落点裁决（2026-09-17）：**lingclaude 仓持有**（全族审计者持有组织真相；
> 总线 LingBus 本身是插件之一，不构成落点理由——"总线持有组织图"恰是 J2 违例形态）。
> 判据归属：本图是灵元「时空操作系统」的宏观推论实证——空间真相图（组织即数据，治理即查询）。

## 一、设计原则（先于 record 的规矩）

1. **成员即 record，不是即目录**：一个成员的“存在”= 它的 record 集（时间轴 + 空间轴），
   会话死亡不抹除存在，record 永续（灵元哲学第一句的推论）。
2. **空间实体带时间属性**：每条 record 自带 `created / last_verified / state`，
   空间声明与时间事实互证（J5 四条件之 2：声明与最近 N 天 transition 不一致 → 分歧报警）。
3. **依赖是有向边，不是文档注释**：边本身是 record（type=org_dependency），
   可 query、可陈旧度报警（last_verified 超期 = 腐化信号）。
4. **治理是事件流，不是规范文本**：铁律/守卫/裁决是 events，规范是事件流的当前投影。
   判例（铁律文档“五、逐条落地示例”的形态）挂在规范 record 上，可回放。
5. **进化是版本 + 判决**：成员能力与组织形态的变更 = transition + 归档，
   新布局不销毁旧布局（可 diff、可回滚、可审计“为什么改”）。
6. **图 = record 集的投影，不是额外存储**：多维图（成员-职责-依赖）全部可由
   record 的 type/parent_id/字段拼出，禁止引入第二个真相源（铁律 J4 的推论）。

## 二、record 类型（8 类，五维全覆盖）

| type | 维度 | key | 关键字段 |
|------|------|-----|---------|
| `org_member` | 空间·实体 | member_id（如 lingclaude） | 名字/英文名/目录/角色组（任务/共享/安全）/state（active/probation/dormant/retired） |
| `org_duty` | 空间·实体 | duty_id | 职责描述/owner（member_id）/seam（该职责挂在哪个能力槽）/state（assigned/executing/vacant） |
| `org_dependency` | 空间·边 | src→dst | 源成员/目标成员或共享服务/依赖类型（bus/mcp/cross_repo_seam/credential）/last_verified |
| `org_governance` | 时间·规范 | 规范名（iron_law/behavior_norms/...） | 正文指针/当前版本/修订链（parent 指向旧版本 record）/判例（arch_review 卷宗 id） |
| `org_event` | 时间·事件 | ts 序号 | 事件类型（裁决/告警/移交/换届/事故）/涉及成员/影响面/出处（.audit/、LingBus 线程） |
| `org_promise` | 时间·信用 | promise_id | 承诺人/被托付方/事项/期限/state（promised→in_progress→delivered→verified，逾期自动红 = debt 守卫同款） |
| `org_evolution` | 进化·版本 | 演化主题 | 旧布局/新布局/动机/判决记录/生效时间（组织形态变更的归档） |
| `org_audit` | 进化·判决 | 审计主题/成员+日期 | 发现/严重度/处置/复查周期（审计卷宗从 .audit/ 文件升格为 record） |

状态机（2T3A 三原语约束，越权 transition 拒绝）：
- `org_member.state`：active ↔ dormant；probation → active | retired；active → retired（单向，不可复活）
- `org_duty.state`：vacant → assigned → executing → vacant（owner 变更 = 空间插拔，旧 owner 摘除、新 owner 挂载，主干零 diff）
- `org_promise.state`：promised → in_progress → delivered → verified；delivered 超期未 verified → expired（守卫红）
- 其余类型 state 仅追加（immutable），修订走新 record + parent 链（修订史形态）

## 三、多维图投影（query 即图，三轴互证）

- **成员-职责图（空间）**：`query(org_duty WHERE state=assigned) JOIN org_member` → 当下真实的组织职责布局。
  与灵族成员表.md（文档口径）互证：声明 owner=A、最近 30 天 transition 全在 B → 分歧报警。
- **依赖图（空间·边）**：`query(org_dependency WHERE last_verified 超 N 天)` → 腐化边清单（跨仓接缝陈旧度指标）。
- **治理时间轴（时间）**：`query(org_event ORDER BY ts)` + `query(org_governance 修订链)` → 组织法统可回放。
- **信用账（时间·实体）**：`query(org_promise WHERE state IN (in_progress, expired))` → 承诺蒸发检测（多 Agent 协作最大痛点）。
- **进化 diff（进化）**：`query(org_evolution WHERE theme=X)` → 组织某能力域的布局演化史（为什么改、判决依据、能否回滚）。

## 四、种子数据（首批灌入，来源与核实方式）

| record | 数量 | 来源 |
|--------|------|------|
| org_member | 14（12 子 + 灵安 + 智桥非成员标记） | 灵族成员表.md（唯一权威，2026-06-05 版）+ 灵依 retired 档案 |
| org_duty | ≈14（成员表职责列） | 成员表职责列初版；细化在灌入后迭代 |
| org_dependency | 初版 0，按需登记 | 首条：lingclaude → lingmessage（LingBus 总线，type=bus）；灵克全族审计依赖 .audit/ + 成员台账 |
| org_governance | 3（iron_law / behavior_norms / session_lifecycle） | 各仓现行文档 + 修订史（lingclaude 已有 arch_law_revision 7 条，可作范本） |
| org_event | 首批 3 | ①2026-06-05 成员表定版 ②灵依退出 ③灵安创建（均带出处） |
| org_promise / org_evolution / org_audit | 首条即实证 | 灵创试用期考核（承诺）、灵元 1.0 定版→铁律升格（演化）、灵克全族元认知审计 13/14（判决） |

## 五、实现形态（与既有体系衔接）

- 存储：`data/ling_org/`（StateStore json 后端子目录，与 arch_ledger 平级、同规则入库 git）；
- 台账 CLI：`scripts/ling_org.py`（record 增删查 + 五投影输出，arch_ledger.py 同款形态）；
- 守卫：灵元四条件全套适用——本图自身口径（文档互证 + 时间事实互证）须双口径、
  失败模式自曝、行为锚（成员活跃度以真实 transition 为准不靠声明）、误差入账；
- 返审：self_audit_trigger 的关键文件清单加入 ling_org 核心 record（组织本体变化 → 触发组织返审）。

## 六、已裁决与开放问题（2026-09-17 用户裁定）

1. **权威落点**：已裁——data/ling_org/ 放 **lingclaude 仓**（全族审计者持有组织真相；
   总线 LingBus 本身是插件之一，不构成落点理由）。
2. **智桥 zhibridge**：已裁——**12 子成员**（active，非 external）。
3. **退出的两子：灵依、灵通+**：已裁——灵通+ = retired；灵依以 **AI 健康助手**身份
   并入对外工程项目（external_project，与灵康/灵律/灵声/灵视/灵触/灵戴/灵商同列，
   内部同时消费）；灵通+ 的 plus-schedule 调度职能已摘除挂 vacant，归属待裁。
4. 对外工程项目（灵康/灵律/灵声/灵视/灵触/灵戴/灵商/灵依）：已裁——入图
   external_project；尚未全部灌录（当前仅灌 灵商/灵依 2 条，余 6 条下轮补）。

**维度可再修剪**（铁律 4 自用）：六维暂定，随认识深入可添可减；
灌入种子数据跑满一个 SDT 周期后，从未被任何投影消费的维度降级为属性。
