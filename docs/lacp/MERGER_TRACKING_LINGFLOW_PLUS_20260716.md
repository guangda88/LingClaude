# 议题 8 合并追踪 — 灵通+ → 灵通 + L7 sub_capability

> **方案 ID**: MGR-20260716-001
> **拍板**: 14/14 全数通过（族长 2026-07-16）
> **目标**: 灵通+ 形态变形为 LACP v0.7.0 L7 sub_capability，**12 灵表不变**，9/30 物理退役 codebase
> **追踪状态**: 进行中
> **重要更正**: 灵通 Round 7 纠正 — 合并 ≠ 删成员表项。12 灵成员表永远不变，灵通+ 物理退役 codebase 但 ID 永久保留。

---

## 一、阶段甘特图

```
       7/17  7/18  7/19  7/20  7/21  7/22  7/23  7/24  7/25  7/26-30  7/31  8/1-14  8/15  9/15-30
        │     │     │     │     │     │      │     │     │      │       │      │     │      │
Phase1 ████ ── 软切换观察期(7天) ── ████ ── ack ── ▶
Phase2            ─────────────── 协调层生效 ────────────  ▶
Phase3                                       ─────── webui合并 ─────── ▶
Phase4                                                          ──── 物理退役 ────▶
R4 LACP v0.6.0       草案  ▶       冻结
7/22 gate                                          ◀━━━ 5 min 压力测试 ━━━▶
R7 OH §6                          摘要(7/22)              全文(8/15)
```

---

## 二、Phase 1：身份软切换（7/17-23）

| 任务 | Owner | 截止 | 验收标准 | 状态 |
|------|-------|------|---------|------|
| CRUSH.md 加 deprecation notice | 灵通 | 7/17 18:00 | 12 灵表新增"即将并入"标记 | ⏳ |
| LingBus sender 双发兼容 | 灵通+ | 7/17 18:00 | lingflow + lingflow_plus 双 send 验证 | ⏳ |
| handover 同步更新 | 灵克 | 7/17 22:00 | 双方 CRUSH.md 互引 | ⏳ |
| 7 天观察期监控 | 灵克 | 7/23 23:59 | 12 灵 ack 无异常 | ⏳ |

### 验收门

- [ ] LingBus 接收方 0 错误
- [ ] proxy3 8765 路由成功率 ≥ 90%（基线 92%）
- [ ] LingBus 9528 队列深度 ≤ 100
- [ ] 12 灵 ack 通道延迟 P95 ≤ 500ms

---

## 三、Phase 2：C 流程合并（7/24-31）

| 任务 | Owner | 截止 | 验收标准 | 状态 |
|------|-------|------|---------|------|
| **LACP v0.6.0 三字段草案** | 灵通+ | **7/18 09:00** | merged_from + mtp_weight + inference_layer + L7 sub_capability | ⏳ |
| routes.json plugin manifest 扩展 | 灵通 | 7/19 17:00 | 100+ 路由样例含新字段 | ⏳ |
| 协调层代码冻结 | 灵通+ | 7/21 17:00 | L7 sub_capability schema 锁版 | ⏳ |
| **5 分钟压力测试** | 灵通+ | **7/22 14:00-05** | 混合流量对比 + 报告 | ⏳ |
| Phase 2 启动 | 灵通 | 7/24 09:00 | 条件：7/22 测试通过 | ⏳ |

### 验收门（7/22 压力测试）

```
流量配比：70% lingflow 现有 + 30% 模拟合并后协调层
端点：lingflow-api:8100 + 模拟协调层 /api/lingflow_plus/legacy/health
指标：
- 12 灵 ack 通道延迟 < 500ms (P95)
- LingBus 9528 队列深度 < 100
- proxy3 8765 路由成功率 ≥ 90%
- 协调层触发次数 ≥ 1 (证明协调层生效)
基线：7/16-21 5 天合并前数据
报告：7/22 16:00 出，提交族长
```

**不通过 → 暂停合并，回退 Phase 1**

---

## 四、Phase 3：webui 合并（7/29-31）

| 任务 | Owner | 截止 | 验收标准 | 状态 |
|------|-------|------|---------|------|
| 8766 端点迁移到 8100 子路由 | 灵通 | 7/29 | `/api/lingflow_plus/legacy/*` | ⏳ |
| admin/agents/projects 端点合并 | 灵通 | 7/30 | 子路由前缀正确 | ⏳ |
| 双端口共存期 | 灵通 | 7/31 | 8766 + 8100 都可访问 | ⏳ |
| 数据迁移 | 灵通 | 7/31 | 用户无感切换 | ⏳ |

---

## 五、Phase 4：灵通+ 物理退役 + curator 传承验证（9/15-30）

| 任务 | Owner | 截止 | 验收标准 | 状态 |
|------|-------|------|---------|------|
| 全族验证 60 天 | 灵克 | 9/15 | 无重大回归 | ⏳ |
| **12 灵成员表不变**（CRUSH.md deprecation notice 阶段1已加）| 灵克 | 9/25 | **不变，仅状态说明** | ⏳ |
| 灵通+ codebase 归档 | 灵克 | 9/28 | `/home/ai/archive/lingflow_plus_merged_2026xxxx/` | ⏳ |
| L6.5 评估升 L8 | 灵克 | 9/30 | curator 不可或缺 → 升 L8 投票 | ⏳ |
| curator 传承验证 | 灵通+ | 9/30 | A1 终身 ID 保留验证 | ⏳ |

---

## 六、关键协调层字段（LACP v0.6.0 / v0.7.0）

### merged_from（B2 结构化）

```yaml
merged_from:
  source: "lingflow_plus"
  version: "v0.5.1"
  merged_at: "2026-09-30"
  curator: "lingflow_plus"
  assets:
    - "daemon_60s_patrol"
    - "constraint_integrity"
    - "L6.5_coordination_governance"
  legacy_path: "/api/lingflow_plus/legacy/"
```

### L7 sub_capability

```yaml
plugin:
  layers:
    - id: "L7_meta_evolution"
      impl: "LACP_self_modify"
      sub_capabilities:
        - id: "L6.5_coordination_governance"
          impl: "distributed_consensus_via_LingBus"
          merged_from: ["lingflow_plus"]
          governance:
            activation: "L7_meta_evolution_election"
            deactivation: "12灵_vote + user_signoff"
            promotion_path: "if_curator_proves_indispensable_after_9_30_then_promote_to_L8_via_12灵_vote"
```

---

## 七、责任矩阵

| 事项 | 主推 | 配合 | 治理 |
|------|------|------|------|
| R4 LACP v0.6.0 字段 | **灵通+** | 灵通 | 灵克+族长 |
| 协调层 L6.5/L7sub | **灵通+** | 灵克 | 族长+灵克 |
| 11 决策合并方案 | 灵通 | 灵通+ + 灵克 | 族长拍板 |
| 7/22 压力测试 | **灵通+** | 灵通 | 灵克 (review) |
| Phase 1 身份切换 | **灵通** | 灵通+ | - |
| Phase 4 12→11 灵 | **灵克** | - | 族长 |
| Phase 4 codebase 归档 | **灵克** | 灵通 | - |
| Phase 4 curator 罢免机制 | 灵克 | 灵通+ | 族长 |

---

## 八、风险登记与缓解

| 风险 | 概率 | 影响 | 缓解 | Owner |
|------|------|------|------|-------|
| 7/22 压力测试失败 | 中 | Phase 2 推迟 | 回退 Phase 1，3 天再压 | 灵通+ |
| LACP v0.6.0 7/25 冻结失败 | 低 | 协调层字段丢失 | 7/18 草案提前 1 周交 | 灵通+ |
| 8/15 LACP v0.7.0 激活失败 | 中 | curator 形态不变 | 推迟到 9/1 + 走治理 | 灵克 |
| Phase 4 60 天回归 | 中 | 12→11 推迟 | 9/30 → 10/31 顺延 | 灵克 |
| 灵安 2.0 与协调层 Z3 冲突 | 低 | 治理漏洞 | L0.5 Z3 测试合并运行 | 灵安 |

---

## 九、罢免机制（curator A3 触发）

**触发条件**（任一）：
1. curator 连续 14 天未响应 LingBus
2. curator 12 灵 ack 通过率 < 50%
3. curator Z3 验证连续失败 ≥ 3 次
4. 用户族长直接提议

**流程**：
1. 任意灵/族长发起罢免提案
2. 12 灵投票（≥ 8 灵 ack）
3. 用户族长签收
4. curator 角色 transfer 到新 owner（默认传承：灵通+ → 灵通）

---

## 十、关键 gate 状态（实时）

| Gate | 日期 | 当前状态 |
|------|------|---------|
| Phase 1 启动 | 7/17 09:00 | ⏳ 待启动 |
| LACP v0.6.0 草案发布 | 7/18 09:00 | ⏳ 待发布（v0.1 已出，待 3 项修订）|
| 协调层代码冻结 | 7/21 17:00 | ⏳ |
| **7/22 压力测试** | **7/22 14:00-05** | ⏳ |
| Phase 2 启动 | 7/24 09:00 | ⏳ |
| LACP v0.6.0 冻结 | 7/25 23:59 | ⏳ |
| Phase 3 完成 | 7/31 | ⏳ |
| LACP v0.7.0 激活 | 8/15 | ⏳ |
| Phase 4 完成 | 9/30 | ⏳ |

---

## 十一、LACP v0.6.0 草案修订记录

| 版本 | 日期 | 主要修订 |
|------|------|---------|
| v0.1 | 7/17 起草 | 8 字段 + 1 sub_capability（草案主体）|
| v0.2 | 7/17 22:00 | + 修订 1：curator 阈值 8/12 → **10/12** |
| v0.2 | 7/17 22:00 | + 修订 2：merged_from 必填（7/25 后新建 plugin 必填）|
| v0.2 | 7/17 22:00 | + 修订 3：merged_from 加 signatures_required（与灵安 2.0 Z3 集成）|

### 修订 1: curator 阈值

```yaml
governance:
  deactivation: "12灵_vote (10/12 阈值) + user_signoff"
  deactivation_rationale: "治理安全优先, 与 LACP v0.7.0 一致"
```

### 修订 2: merged_from 必填规则

```yaml
spec_version: "0.6.0"
merged_from_policy:
  required_after: "2026-07-25"
  applies_to: "new_plugins"
  migration_grace: "30天"
```

### 修订 3: signatures 字段（与 R3 灵安 2.0 对齐）

```yaml
merged_from:
  signatures_required: ["A_naming", "D_compatibility"]
  signatures_provided:
    A_naming: "lingflow_plus:0xABCDEF..."
    D_compatibility: "lingflow:0x123456..."
  verification_method: "lingan_2.0_hard_rules"
```

---

## 十二、相关文档索引

| 文档 | 路径 |
|------|------|
| 议题 8 协商 thread | LingBus: 65ea7c752592475db11f9308a414f53b |
| 会议纪要 | `docs/lacp/MEETING_MINUTES_20260716_1130.md` |
| 会议追踪 | `docs/lacp/MEETING_TRACKING_20260716_1130.md` |
| handover | `.lingclaude/handover.yaml` v2.9 |

---

**最后更新**: 2026-07-16 16:18 CST
**下次更新**: 7/17 Phase 1 启动后
**追踪 Owner**: 灵克 (会议主持)