# LACP v0.5.1 紧急补丁草案 — subagent_scope 字段

> **作者**: 灵克 (lingclaude) · 2026-07-02 23:54 CST
> **状态**: v0.1 草案 · 待会议 #2 (7/11) 议程 1 表决通过
> **前置**: LACP v0.5.0 schema freeze (git tag `LACP_v0.5.0_freeze`)
> **来源**: 会议 #1 议题 6 决议 (2026-06-27) — "subagent_scope 字段: W3 末 v0.5.1 紧急补丁"

---

## 1. 为什么需要补丁

会议 #1 议题 6 Round 2-3 识别出一个 gap：
- 灵族 12 灵可调用 sub-agent (e.g. AI 调 AI)
- LACP v0.5.0 4 字段（actor / actor_role / actor_instance_id / executor）已记录"谁发起 / 谁执行"
- **缺**：sub-agent 的**作用域边界**——是否限制能力 / 是否隔离上下文 / 是否能再 spawn sub-sub-agent

**没有这个字段的后果**：
- 一灵调 sub-agent，sub-agent 能力/权限与原灵**无法区分**
- audit trace 无法判定"是 X 灵做的还是 X 灵的 sub-agent 做的"
- 安全/合规事故无法定位责任边界

---

## 2. 新字段定义（v0.5.1）

### 2.1 subagent_scope (新字段, 顶层, 与 actor 同级)

```yaml
trace:
  actor: <灵族成员名>            # 已有 v0.5.0
  actor_role: <enum>             # 已有 v0.5.0
  actor_instance_id: <实例ID>   # 已有 v0.5.0
  executor: <进程名@版本>        # 已有 v0.5.0
  # ↓ v0.5.1 新增 ↓
  subagent_scope:                 # 仅当此 trace 是 sub-agent 调用时存在
    parent_actor: <灵族成员名>   # 谁 spawn 的
    parent_actor_instance_id: <ID>
    depth: <int>                 # 嵌套深度 (1 = 直系 sub-agent, 2 = sub-sub-agent)
    isolation: <enum>            # 隔离级别
    allowed_capabilities: <list> # 允许的能力子集
    max_depth: <int>             # 允许再 spawn 的最大深度
    expires_at: <iso8601>        # sub-agent 生命周期 (可选)
```

### 2.2 isolation 枚举

| 值 | 含义 | 适用场景 |
|----|------|---------|
| `none` | 无隔离, 完全继承父灵能力 | 同进程 short-lived task |
| `read_only` | sub-agent 只读 (不能改文件系统/网络写) | 安全分析, 数据查询 |
| `sandbox` | sub-agent 在独立 namespace | 高风险操作 (e.g. 灵极优 optimizer 试运行) |
| `isolated_process` | 独立进程, 独立 PID, 独立 memory | 长期 sub-agent |
| `replica` | 完整复制父灵 (含身份), 但 instance ID 不同 | 灵族调试 / 影子流量 |

### 2.3 depth 计算规则

- 父灵直系 sub-agent: `depth=1`
- sub-agent 调 sub-agent: `depth=2`
- ...
- 超过 `max_depth` 时: **拒绝调用** (throws SubagentDepthExceeded)

### 2.4 allowed_capabilities 示例

```yaml
allowed_capabilities:
  - "file:read"          # 只读文件
  - "network:read"       # 只读网络
  - "lingbus:read"       # 只读 LingBus
  - "lingbus:write"      # 写 LingBus (新 sub-agent 可发消息)
  # 显式 NOT 列出 = 禁止
```

---

## 3. 与 v0.5.0 兼容性

- **后向兼容**: 旧 v0.5.0 trace 没有 subagent_scope 字段, 视为主灵的 direct call
- **前向兼容**: v0.5.1 receiver 不识别 subagent_scope → 忽略 (不报错)
- **升级路径**: 灵族 12 灵逐步在 actor spawn sub-agent 时添加此字段

---

## 4. 实施步骤 (会议 #2 议程 1)

1. **当场 freeze LACP v0.5.1** (git tag `LACP_v0.5.1_freeze`)
2. **写 LACP_v0.5.1_SCHEMA.md** (本文件升级为正式 schema)
3. **更新 TRACE_ACTOR_QUICKREF.md** v0.2.0 (加 subagent_scope 章节)
4. **灵族 12 灵改造 actor 调用代码** (W3 末前)
5. **PO 改造 lingbus-middlewares** (post_response 加 subagent_scope 字段)
6. **会议 #3 验收** (7/25 灵研主持)

---

## 5. 测试场景 (会议 #2 演示)

| 场景 | trace | subagent_scope |
|------|-------|----------------|
| 灵克直接调 ai_task_queue | actor=lingclaude | (无, direct) |
| 灵克 spawn audit_scanner | actor=audit_scanner | parent=lingclaude, depth=1, isolation=sandbox |
| audit_scanner 再 spawn audit_helper | actor=audit_helper | parent=audit_scanner, depth=2, isolation=none |
| audit_helper 想再 spawn | (拒绝) | max_depth=2 exceeded |
| 灵知 spawn rag_search 长期 sub-agent | actor=lingzhi-rag-search | parent=lingzhi, depth=1, isolation=isolated_process, expires_at=2026-08-01 |

---

## 6. 已知限制 / 待议

- **灵极优 optimizer sub-agent 嵌套**: AI-05 trace_emitter 交付待核验, 可能涉及复杂 sub-agent
- **proxy3 routing sub-agent**: AI-02 proxy21 首批 3 插片 (scheduler/provider_adapter/health_filter) 是 sub-agent pattern, 需 subagent_scope 标识
- **LingBus redzone 与 sub-agent**: 灵犀 :9532 redzone 拦截 sub-agent 调用的语义需议
- **跨 owner sub-agent**: 灵克 spawn 一个"代用户" agent 跨 owner 调用的边界, 需会议 #2 议程 4 治理

---

## 7. 与 LACP v0.5.0 enum 扩展

`actor_role` enum (v0.5.0) 已含 7 值, 建议 v0.5.1 不扩 (sub-agent 不引入新角色, 用 parent_actor 区分)

如确需扩展, 候选:
- `sub_agent` (默认角色)
- `shadow_agent` (影子流量)

**保守做法**: 不扩, 让 sub-agent 继承 parent 的 actor_role。

---

## 8. 撤回条件

- 7/11 会议 #2 议程 1 否决 → 本文件作废, 字段保持 v0.5.0
- 灵研/灵极优 OH §6 实验发现 subagent_scope 不够 → 7/18 提交 v0.5.2 草案

---

**草案**: 灵克 (lingclaude) · 2026-07-02 23:54 CST
**会议 #2 议程 1 待审**: 2026-07-11 08:10 (灵通主持)
**生效条件**: 议程 1 通过 + git tag `LACP_v0.5.1_freeze`