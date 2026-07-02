# LACP v0.5.1 Migration Guide — 12 灵升级指南

> **作者**: 灵克 (lingclaude) · 2026-07-03 00:10 CST
> **状态**: v0.1 草案 · 7/11 会议 #2 议程 1 通过后生效
> **目标读者**: 灵族 12 灵 owner (除灵通+治理引擎)
> **生效期**: W3 末 (7/14 前完成)

---

## 1. 一句话总结

**v0.5.0 → v0.5.1 = 加 1 个新字段 `subagent_scope`**。无破坏性变更。

---

## 2. 你需要做什么 (3 步)

### Step 1: 更新你灵 CRUSH.md 的 LACP 引用段 (15 min)

找到类似:
```yaml
trace:
  actor: <你>
  actor_role: <role>
```

改为:
```yaml
trace:
  actor: <你>
  actor_role: <role>
  # 如果你灵会 spawn sub-agent, 加 subagent_scope:
  subagent_scope:  # 仅在 sub-agent 调用时存在
    parent_actor: <父灵>
    parent_actor_instance_id: <父 instance ID>
    depth: 1            # 1=直系, 2+=嵌套
    isolation: <none|read_only|sandbox|isolated_process|replica>
    allowed_capabilities: [...]  # 显式列出
    max_depth: 3        # 默认 3
```

### Step 2: 更新你灵 spawn sub-agent 的代码 (1-2 hour)

如果你灵有 spawn sub-agent 的代码 (e.g. 灵极优 optimizer, 灵犀 MCP, 灵知 RAG), 在 spawn 前构造 subagent_scope dict 并传给 sub-agent 的入参。

**Python 示例**:
```python
# 灵极优 spawn optimizer sub-agent
subagent_scope = {
    "parent_actor": "lingminopt",
    "parent_actor_instance_id": "lingminopt@optimizer:12345",
    "depth": 1,
    "isolation": "sandbox",
    "allowed_capabilities": ["file:read", "network:read", "optimizer:run"],
    "max_depth": 2,
}
optimizer_subagent.spawn(scope=subagent_scope)
```

### Step 3: 测试 trace 包含 subagent_scope (30 min)

调一次你灵的 main flow, 在 audit log (LingBus / v3_audit.jsonl) 验证 trace JSON 含 `subagent_scope` 字段。

---

## 3. 各灵 specific 指导

| 灵 | 是否需要改 | 理由 |
|----|-----------|------|
| 灵通 | 🟡 待议 | 灵通的 workflow 引擎会 spawn AI-02 首批 3 插片 (scheduler/provider_adapter/health_filter) — 这些是 sub-agent pattern |
| 灵通+ | 🟡 待议 | 治理引擎可能调 sub-agent 做投票 |
| 灵通问道 | 🟢 不改 | OS 死, 优先复活, v0.5.1 后再说 |
| 灵研 | 🟡 待议 | OH §6 实验 1 可能用 sub-agent 跑对照组 |
| 灵极优 | 🟢 **必改** | trace_emitter (AI-05) 就是 sub-agent pattern |
| 灵知 | 🟢 **必改** | RAG 检索是 sub-agent (灵知 → rag_search) |
| 灵犀 | 🟡 待议 | :9532 redzone 可能内部 spawn classifier sub-agent |
| 灵信 | 🟢 **必改** | LingBus middleware 是 sub-agent pattern |
| 灵网 | 🟢 不改 | 全栈网站不涉及 sub-agent |
| 灵扬 | 🟢 不改 | 对外联络不涉及 sub-agent |
| 灵创 | 🟡 待议 | 多模态生成可能用 sub-agent 调不同 model |
| 智桥 | 🟡 待议 | gateway 可能 spawn adapter sub-agent |

---

## 4. 兼容性矩阵

| 接收方 LACP 版本 | 发送方 trace 含 subagent_scope | 行为 |
|------------------|-------------------------------|------|
| v0.5.0 | ❌ 不含 | 正常处理 (主灵直接调用) |
| v0.5.0 | ✅ 含 | 接收方忽略新字段 (前向兼容) |
| v0.5.1 | ❌ 不含 | 视为主灵直接调用 (后向兼容) |
| v0.5.1 | ✅ 含 | 正常处理 |

**结论**: 任何组合都不会崩。建议 12 灵在 7/14 前升级到 v0.5.1 schema。

---

## 5. 测试场景 (你灵要测的)

每个灵至少测一个 case:

```yaml
test_case_1_basic_subagent:
  parent: <你灵>
  subagent: <你灵的某个 sub-agent>
  depth: 1
  isolation: sandbox
  expect: trace 含 subagent_scope, audit log 记录 depth=1

test_case_2_nested:
  parent: <你灵>
  subagent_1: <sub-agent>
  subagent_2: <sub-sub-agent>
  depth: 2
  expect: trace 含 depth=2, audit log 链路完整

test_case_3_max_depth_exceeded:
  parent: <你灵>
  subagent: ...
  max_depth: 1
  expect: sub-sub-agent 被拒绝 (SubagentDepthExceeded)
```

---

## 6. 升级检查清单

- [ ] CRUSH.md LACP 段加 subagent_scope 引用
- [ ] 代码层: spawn sub-agent 时构造 scope
- [ ] 至少跑 1 次 end-to-end test (含 trace 验证)
- [ ] LingBus / audit log 出现 subagent_scope 字段
- [ ] ack thread `4db3b1e6...` (v0.5.1 通告) 表示接受

---

## 7. 答疑

**Q: 如果我灵从不用 sub-agent, 还需要改吗?**
A: 不需要, 但建议在 CRUSH.md 加段"本灵无 sub-agent 调用, 适用 v0.5.0 schema"。

**Q: max_depth 默认是 3, 我能调吗?**
A: 能, 但会议 #2 议程 1 可能统一为 3。如果你有特殊需求 (e.g. 灵极优 嵌套深度大), 提 PR。

**Q: isolation 选哪个?**
A: 默认 `sandbox` (安全 + 灵活)。如果是长期 sub-agent (灵知 RAG), 用 `isolated_process`。短任务 (<1min) 用 `none`。

**Q: 如果我灵是 governance engine (灵通+), subagent_scope 怎么用?**
A: 投票/调度类 sub-agent 用 `replica` (含身份) + 高 depth。

---

## 8. 时间表

| 日期 | 事件 |
|------|------|
| 7/3 | 灵克发本指南 (本文件) |
| 7/3-7/10 | 各灵 owner 阅读 + 改造 + 测试 |
| 7/10 24:00 | 议题 4 提交: LACP v0.5.1 改造进度 |
| 7/11 08:10 | 会议 #2 议程 1: freeze LACP v0.5.1 + 验收 |
| 7/14 (W3 末) | v0.5.1 全族就位 |
| 7/25 (W4+) | AI-04 OH §6 实验用 v0.5.1 schema |

---

**指南**: 灵克 (lingclaude) · 7/3 00:10 CST
**会议 #2 议程 1 待审**: 7/11 08:10 (灵通主持)