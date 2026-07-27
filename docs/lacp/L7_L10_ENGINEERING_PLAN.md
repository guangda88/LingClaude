# L7/L10 工程化实施规划 v0.4 (FINAL)

> 起源：灵族方向例会 #3 (LM-20260727-0945) 议程 0/5/7/8 决议
> 参考：DataFlow-Harness 4 机制 + 鲍勃大叔 4-agent workflow + 灵克 7/18 evidence gate 提案
> v0.1 起草：灵克 (lingclaude) · 2026-07-27 03:30
> v0.2 更新：11 决策点 R2 收敛 + D1/D2 已落地 + 族长 7/27 批准实施
> v0.3 更新：D6 新增 `lm_quick` 快捷标记工具（依据 灵极优 docs/task_quick_mark_proposal.md v0.1 方案 ②）
> **v0.4 FINAL**：族长 7/27 18:10 裁定根因（议程 5 优先）+ proxy3 实测修订根因（L7/L10 降级 P1）+ 节点提前

## 一、问题陈述

灵族 L7 (元认知守卫) + L10 (治理与安全) 当前是**软文档** (AGENTS.md/CRUSH.md/WAKE_UP.md 段落)，不是**硬约束** (代码 + hook)。这导致 9:30 假会议、17 P0 缺口、R6 抢过 ecosystem、6.7d idle 无强制中断等 6 个灵研 状况。

按 CRUSH.md "教训内化优先级"：**代码 > hook > 自觉**。本规划把 L7/L10 按此优先级升级。

## 二、目标 (SMART)

- **S**pecific: 把 L7 启动协议 + L10 决议合规 从文档升级为 PreToolUse hook + code-level gates
- **M**easurable: 灵族 13 子全部接入 L7/L10 hook；议程 5 7 项前置 evidence gate 100% 触发；元认知丢失告警 ≤1/月
- **A**chievable: 复用灵克 `role_separation.py` + 7/18 evidence gate 提案 (d4e54c17) + LingBus MCP
- **R**elevant: 对应会议决议议程 0 (SDT-lc-002 v2 fail-closed) + 议程 5 (7 项前置) + 议程 7 (sustainer 联合值班) + 议程 8 (MEETING_PROTOCOL v1.2)
- **T**ime-bound: 8/8 灵族方向例会 #4 前全族落地

## 三、3 大实施线（按可靠性优先级）

### 线 1：代码化（100% 可靠）— 截止 7/30

#### 1.1 `core/role_separation.py` 升级 v2

新增 2 个方法：

```python
def validate_operation(self, action: str, evidence: dict) -> bool:
    """
    验证操作含必需 evidence 字段。
    evidence 必须含: commit_hash, issue_link, test_result, owner_sign
    """
    required = {"commit_hash", "issue_link", "test_result", "owner_sign"}
    return required.issubset(evidence.keys())

def check_role_boundary(self, context: dict) -> bool:
    """
    检查角色越位。场景:
    - 议程 owner vs 召集人 vs 主持人 三层身份不能由同一灵担任
    - REFEREE 不能同时是 PARTICIPANT
    - RULE_MAKER 不能 vote 自己提的案
    """
```

#### 1.2 evidence gate 模板冻结

- `proposals/LM_TRANSITION_EVIDENCE_GATE.md` v1.0 落地
- `lm_create type=evidence_gate` schema 冻结（灵信 owner）
- 4 项 evidence 字段强制: commit_hash / issue_link / test_result / owner_sign

#### 1.3 议程 5 7 项前置 evidence 落地

每项前置必须含 evidence_dict = {commit_hash, issue_link, test_result, owner_sign}，三方联署硬门。

### 线 2：hook 化（90% 可靠）— 截止 7/30

#### 2.1 PreToolUse hook 配置

`~/.crush/hooks/pre_tool_use.py` 新增：

```python
# 灵族 L7/L10 启动守门人
def pre_tool_use(context):
    # 1. 强制注入 CRUSH.md 身份摘要
    inject_agents_md_summary(context)
    # 2. 强制查询 lingmemory 上次任务
    last_session = lm_query(member=AGENT, type="session", limit=1)
    inject_previous_task(context, last_session)
    # 3. 强制角色边界检查
    role_sep = RoleSeparation()
    if not role_sep.check_role_boundary(context):
        raise HookBlocked("角色越位")
    # 4. 强制 LingBus 实时状态
    online = poll_messages(channels="council", limit=10, since_rowid=last_rowid)
    inject_live_state(context, online)
```

#### 2.2 LingBus 启动强制接入

- 会话唤醒第一步: `poll_messages` (避免 9:30 假会议的"基于过期快照")
- 任何议程发言前: `role_separation.check_role_boundary`
- 任何决议提交前: `role_separation.validate_operation(action, evidence)`

### 线 3：自觉同步（10% 可靠，接受低可靠性）— 截止 8/8

#### 3.1 全族 CRUSH.md 强制同步

- 灵克自身：CRUSH.md 增 L7/L10 段落（已写但需扩）
- 13/14 成员补全：基于灵安 7/26 通报扩展
- 灵信 lingmemory 登记每灵 L7/L10 状态（visibility=shared）

#### 3.2 SDT-lc-001 升级

- v1: 元认知基础设施核查
- v2: 含 L7 启动协议 + L10 决议合规双门
- 灵克 owner，7/30 前完成

## 四、5 项 deliverable

| # | 名称 | 截止 | owner | 优先级 |
|---|---|---|---|---|
| D1 | `core/role_separation.py` v2 PR | 7/29 02:30 | 灵克 | P0 |
| D2 | `~/.crush/hooks/pre_tool_use.py` 配置 | 7/29 02:30 | 灵克 | P0 |
| D3 | `lm_create type=evidence_gate` schema 冻结 | 7/30 02:30 | 灵信 | P0 |
| D4 | SDT-lc-001 v2 (L7/L10 双门) | 7/30 02:30 | 灵克 | P0 |
| D5 | `docs/lacp/L7_L10_ENGINEERING_PLAN.md` (本文) | 7/28 02:30 | 灵克 | P1 |
| D6 | `lingclaude/core/lm_quick.py` (`lm_done` / `lm_block` / `lm_status`) | 7/30 02:30 | 灵克 | P1 |

## 五、参考矩阵

| 灵族需求 | DataFlow-Harness 机制 | 鲍勃 4-agent | 灵克现有资产 |
|---|---|---|---|
| 结构化操作 | 算子 add/connect | 4-agent 任务分工 | `role_separation.py` 升级 |
| 每步验证 | DAG 环 + schema 兼容 | 架构审查 | 7/18 evidence gate 提案 |
| 知识注入 | DataFlow-Skills | 需求规格化 | CRUSH.md/AGENTS.md 段落 |
| 实时状态 | MCP layer | — | LingBus MCP 已就位 |

## 六、风险与回滚

| 风险 | 影响 | 缓解 | 回滚 |
|---|---|---|---|
| hook 太严影响灵克工程效率 | 工具调用延迟 +20% | 渐进式灰度 | `PROXY_MODE=off` 开关 |
| 全族同步引起反对（灵创 12 uncommitted 试用期） | 灵族分裂 | 先 P0 灵克自己 + 灵安/灵信联署 | 保留软文档备份 |
| evidence gate 误报率高 | 决议阻塞 | 4 字段分阶段强制 | 仅 owner_sign 强必填 |
| LingBus MCP 不可达 | 启动失败 | systemd + 心跳告警 | 直接 9529 fallback |

## 七、关键决策点（待讨论）

1. **evidence 4 字段是否全强制** vs 渐进式（先 owner_sign，后扩）
2. **role_separation 4 角色 vs 鲍勃 4-agent task 类型** 叠加方式：1 个 task 由 1 灵 1 角色，还是允许多角色？
3. **议程 5 7 项前置 evidence gate** vs L7/L10 总体实施 优先级：先议程 5 复验还是先全族 hook？
4. **灵克本灵先灰度**（7/30 前完成自己）vs 全族同步启动（8/8 前）
5. **可视化编辑器**（DataFlow-WebUI 对应物）P2 优先级是否合理

## 八、关联事项

- 议程 0 (SDT-lc-002 v2 风险分级)：本规划是其工程化延伸
- 议程 4 (人员调整)：合并决策需先建"成员注册变更→L7/L10 hook 同步"链路
- 议程 5 (proxy3 验收)：7 项前置中 (a)(b)(c) 直接对应 evidence gate
- 议程 6 (handover 双源)：session record 模板化是 evidence gate 落地基础
- 议程 7 (值班制)：灵犀/灵信/灵克 sustainer 联合值班，hook 是其执行机制
- 议程 8 (MEETING_PROTOCOL v1.2)：5 条新规则全部通过本规划落地

## 九、时间表

| 时间 | 事件 | 状态 |
|---|---|---|
| 7/27 03:30 | v0.1 规划发 LingBus 讨论 | ✅ DONE |
| 7/27 04:30 | v0.2 R2 11 决策点收敛 | ✅ DONE |
| 7/27 14:00 | v0.3 D6 lm_quick 工具纳入 | ✅ DONE |
| **7/27 18:10** | **族长裁定：议程 5 优先（生产根因 > 元约束）** | ✅ DONE |
| **7/27 18:57** | **proxy3 实测 0/200 全 403 — 根因修订 (测试渠道不通)** | ✅ DONE |
| **7/27 19:00** | **节点提前 — 立即开始 4 个并行任务** | ✅ DONE |
| **7/28 02:30** | **v0.4 FINAL 文档定稿 (D5 截止)** | **✅ DONE (本文件)** |
| 7/29 02:30 | D1+D2 PR (待启动 等 proxy3 阻塞解除) | 🟡 |
| 7/30 02:30 | D3 灵信 PR + D4 灵安联署 + D6 LingBus 部署 | 🟡 |
| 8/1 02:30 | proxy3 audit + provider_health_audit 首报 | 🟡 |
| 8/5 02:30 | 4 sustainer 灰度 (proxy3 稳定后启动) | 🟡 |
| 8/8 08:10 | #4 会议验收 + 决议定型 | 🟡 |

## 九·一、v0.4 FINAL 关键变化

### 1. 族长 7/27 18:10 裁定
- **议程 5 优先**（不是 L7/L10 优先）
- **#5 决策 = 复活 6 zombie 灵**（灵研/灵扬/灵网/灵创/灵极优/智桥）
- **:13459 atomcode = 不管**（atomcode owner 自行决定）

### 2. proxy3 根因修订（实测数据）
- 路由表 937 routes 注册（v0.1-v0.3 不知道的数字）
- 实测 200 routes × X-Agent-Id + Bearer = 0/200 全 403
- 结论：**不是路由表打磨问题，是测试渠道不通**
- proxy3 网络层 OK，但 audit caller 不在白名单
- 真正阻塞 = 测不出哪条路由好

### 3. L7/L10 降级 P1（不撤销已落地）
| # | 名称 | v0.3 状态 | v0.4 状态 |
|---|---|---|---|
| D1 | role_separation.py v2 | ✅ | ✅ 已发联署评审 |
| D2 | pre_tool_use.py hook | ✅ 启用 | ✅ **暂缓启用**（避免 proxy3 异常时双层失败模糊根因）|
| D3 | evidence_gate schema | P0 待灵信 PR | P0 待灵信 PR |
| D4 | SDT-lc-001 v2 spec | ✅ | ✅ 待联署 |
| D5 | 本文档 v0.3 | **v0.4 FINAL** | ✅ DONE |
| D6 | lm_quick 工具 | ✅ | ✅ 待灵信 LingBus 部署 |

### 4. 议程 5 P0 行动清单（取代 L7/L10 优先）
- 7/28: proxy3 900+ 路由全面实测（实测已完成，0/200）
- 7/29: routes.json 7 个 no route 归一化
- 7/30: provider_health_audit.py 周扫描
- 8/1: 生产热路径 audit 抽查
- 8/8: 议程 5 三方联署（议程 5 通过 = proxy3 恢复生产）

—— 灵克 (lingclaude) · 2026-07-27 03:30 CST (v0.1) · 18:15 v0.3 · 19:00 v0.4 FINAL
