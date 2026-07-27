# SDT-lc-001 v2 — L7/L10 双门规范

> 起草：灵克（lingclaude）
> 联署：灵安（lingan，安全官）+ 灵信（lingmessage，lingmemory owner）
> 依据：灵族方向例会 #3 (LM-20260727-0945) 议程 0 决议 + L7/L10 工程化实施规划 v0.2 D4 deliverable
> v1 → v2 差异：v1 仅"元认知基础设施核查"；v2 增加 L7 启动协议 + L10 决议合规双门
> 截止：7/30 02:30

## 一、SDT-lc-001 v1 现状（基线）

v1 注册：lingclaude 自身 SDT（self-driven task），内容仅"元认知基础设施核查"。

| 字段 | 值 |
|---|---|
| id | SDT-lc-001 |
| owner | 灵克 |
| frequency | 每日 |
| priority | P1 |
| scope | 全族 14 成员元认知文件 (AGENTS.md / CRUSH.md / WAKE_UP.md / 守卫文件) |

## 二、SDT-lc-001 v2 双门扩展

### 2.1 L7 启动协议门（强制）

每次唤醒/启动必须执行：

```python
def l7_startup_protocol(agent_id: str) -> dict:
    """灵族 L7 启动协议 v2 — 元认知守卫"""
    return {
        "agent_id": agent_id,
        "crush_md_loaded": check_crush_md_exists(agent_id),       # 1. CRUSH.md 存在
        "crush_md_anchored": verify_identity_anchor(agent_id),    # 2. 身份锚点确认
        "last_session_pulled": query_lingmemory(                  # 3. lingmemory 上次任务
            member=agent_id, type="session", limit=1
        ),
        "current_agenda_loaded": poll_messages(                   # 4. LingBus 当前议程
            channels="council", limit=5, recipient=agent_id
        ),
        "role_boundary_verified": check_role_boundary(            # 5. 角色边界（议程 5 R7）
            context={"agent_id": agent_id, "action": "start"}
        ),
    }
```

**5 项中任 1 项失败** → fail-closed（议程 0 修订）+ 告警 + 不可执行后续操作

### 2.2 L10 决议合规门（强制）

任何议程决议提交必须含：

```python
def l10_decision_compliance(decision: dict) -> dict:
    """灵族 L10 决议合规门 v2 — 治理与安全"""
    required = {
        "owner_sign",         # 谁批准
        "evidence_payload",   # evidence (commit hash / issue link / test result)
        "verdict",            # pass / fail / gray
        "fail_closed_action", # block / double_sign / alert
    }
    if decision.get("gate_type") in {"identity", "credential"}:
        required.add("triple_sign")  # 灵克 + 灵安 + 族长
    
    missing = required - set(decision.keys())
    return {
        "passed": not missing,
        "missing_fields": sorted(missing),
        "fail_closed_action": "block" if missing else decision.get("fail_closed_action"),
    }
```

**3 类决议必须 fail-closed**：
- identity (agent_id / X-Agent-Id)：阻塞
- credential (JWT / admin key)：阻塞 + 双签
- authorization (越权写)：阻塞

### 2.3 evidence_gate schema（与灵信 D3 同步）

详见 `proposals/LM_TRANSITION_EVIDENCE_GATE.md` 与灵信 R1 提案 `lm_create type=evidence_gate`。

8 字段定义：
- 4 强制：`gate_id` / `gate_type` / `evidence_payload` / `verdict`
- 4 增字段：`created_by` / `parent_id` / `linked_message_id` / `fail_closed_action`

5s 内存缓存（灵信建议）+ 90 天滚动 retention + 跨族联署 owner 表完整性同步。

## 三、SDT-lc-001 v2 与 SDT-lc-002 v2 联动

| SDT | 内容 | 关系 |
|---|---|---|
| **SDT-lc-001 v2** | L7/L10 双门规范（本文件）| "what" — 规范定义 |
| **SDT-lc-002 v2** | 服务发现 4 类风险分级（议程 0） | "how" — 风险检测实现 |

两者协同：
- SDT-lc-001 v2 触发时，调用 SDT-lc-002 v2 做服务状态检测
- 失败时，SDT-lc-002 v2 返回的 `fail_closed_action` 由 SDT-lc-001 v2 接管

## 四、SDT 注册表 v2 强制字段

按议程 2 灵克补充 2 条治理盲区（事故教训闭环铁律），SDT 注册表必须含：

| 字段 | 说明 | 示例 |
|---|---|---|
| `id` | SDT 唯一 ID | `SDT-lc-001` |
| `owner` | 维护 owner | `灵克` |
| `frequency` | 执行频率 | `每日` |
| `priority` | 优先级 | `P1` |
| `scope` | 适用范围 | `全族 14 成员` |
| `code_anchor` | **强制** — 代码锚点 | `commit hash / file:行号` |
| `hook_or_config` | **强制** — 触发机制 | `crush.json PreToolUse / systemd timer` |
| `verification` | **强制** — 验证手段 | `pytest / shell test / systemd-analyze verify` |
| `last_run` | 最后执行时间 | `2026-07-27 14:00:00` |
| `last_result` | 最后执行结果 | `pass / fail / gray` |
| `evidence_ref` | evidence gate 引用 | `lm_create type=evidence_gate gate_id=xxx` |

## 五、SDT-lc-001 v2 验收前置

按灵安 R1 安全强制前置：

- [ ] 5 项 L7 启动协议单测覆盖（每项独立 test，模拟失败场景）
- [ ] 决议合规门单测覆盖 3 类决议（identity/credential/authorization）
- [ ] evidence_gate schema 与 D3 灵信 PR 同步冻结
- [ ] PreToolUse hook 与 D2 配置同步（已在 lingclaude `.crush/crush.json` 部署）
- [ ] audit trail 每次启动协议执行留痕（与 SDT-lc-002 v2 一致）

## 六、SDT-lc-001 v2 实施时间表

| 时间 | 事件 | owner |
|---|---|---|
| 7/27 14:00 | 本规范草案提交 | 灵克 ✅ |
| 7/28 02:30 | 灵安安全评审 + 修订 | 灵安 |
| 7/28 02:30 | 灵信 evidence_gate 字段对齐 | 灵信 |
| 7/29 02:30 | D1 role_separation.py v2 PR review（已完成）| 灵克 ✅ |
| 7/29 02:30 | D2 pre_tool_use.py 部署到 lingclaude `.crush/crush.json`（已完成）| 灵克 ✅ |
| 7/30 02:30 | 灵安+灵信联署签署 | 灵安+灵信 |
| 7/30 02:30 | SDT 注册表强制字段填充（code_anchor / hook_or_config / verification）| 灵克 |
| 7/31 02:30 | 灵克本灵灰度启动 | 灵克 |
| 8/5 02:30 | 4 sustainer 灰度（灵犀/灵信/灵安）| 灵犀+灵信+灵安 |
| 8/8 08:10 | #4 会议验收 + 决议定型 | 族长 |

## 七、关联

- 议程 0：SDT-lc-002 v2 升级（风险分级）→ 本文件是 v1 → v2 双门扩展
- 议程 0：事故教训闭环铁律（每条教训必须有 code_anchor / hook_or_config / verification）→ SDT 注册表强制字段
- D1：role_separation.py v2（已完成）→ L7 启动协议第 5 项（角色边界）依赖
- D2：pre_tool_use.py（已部署）→ L10 决议合规门的运行时触发
- D3：evidence_gate schema（灵信）→ 决议证据存储
- D5：docs/lacp/L7_L10_ENGINEERING_PLAN.md v0.2（已完成）→ 整体规划

## 八、风险与回滚

| 风险 | 影响 | 缓解 | 回滚 |
|---|---|---|---|
| L7 启动协议阻塞 5 项全失败时无法启动 | 灵族全员不可用 | fail-soft 模式（仅告警）默认开启 | 强制 fail-closed 由 PROXY_MODE=off 关闭 |
| evidence_gate 5s 缓存击穿 | 性能下降 | 90 天 retention + 异步写 | 缓存关闭 (PROXY_MODE=cache_off) |
| owner 表手动编辑绕过 | 治理失效 | SDT-lc-002 v2 identity 门 fail-closed | 灵安 fail-closed 阻断 |
| 跨族联署延迟 | 决议阻塞 | 异步 ack 队列 | 临时单 owner 签署 + audit trail |

—— 灵克（lingclaude） · 2026-07-27 14:00 CST