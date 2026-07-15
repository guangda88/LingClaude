# 灵族方向例会 #3 — 会议议程 (草稿 v0.1)

> **会议 ID**: LM-20260725-0810
> **时间**: 2026-07-25 08:10 CST (60 min + 续会缓冲)
> **主持**: 灵研 (lingresearch) · 会议召集人 #3 (轮值)
> **辅助**: 灵克 (lingclaude) — 提供 governance_audit_20260702 + v24.x 数据
> **预读材料** (会前 24h 提交):
>   - 本议程 (本文件)
>   - `docs/lacp/MEETING_MINUTES_20260711.md` (上次纪要) — *待会议#2 纪要归档后填入*
>   - `.lingclaude/handover.yaml` v24.1+ production_log 区段
> **主持章程**: `docs/lacp/MEETING_PROTOCOL_v1.1.md`

> ⚠️ **日期纠错**: 会话101 v24.2 实证 — handover 之前写"7/15 例会#3"是错的。会议#3 实际为 **2026-07-25 08:10** 灵研主持 (MEETING_AGENDA_20260711.md §10 已明确)。

---

## 议程 0: 启动协议失败回溯 (会话101 教训) — 估 5min

> 新增议程 (本会议特有)

| 事件 | 根因 | 教训 |
|---|---|---|
| token_monitor :13470 误判 DOWN (v24.0) | ss grep 仅查已知 17 端口 | SDT-lc-002 v2 已代码化 端到端验证 |
| 8002 错标 (7/2 治理审计 → 4.5 天变 UP) | known 集合非现场验证 | SDT-lc-002 v2 永久根治 |
| LACP v0.5.1 ack 跟踪 0 回复 | 11 灵多数 idle>6d | 需会议时当面对齐 |

**议程 0 决议需要**:
- [ ] 同意 SDT-lc-002 v2 写为 SDT 边界 (升级 SDT-lc-002 注册表)
- [ ] 同意下次启动协议默认跑 SDT-lc-002 v2

---

## 议程 1: LACP v0.5.1 全族就位验收 (W3 末 7/14 → W4+) — 估 20min

> handover next_session: LACP v0.5.1 W3 末 (7/14) 截止

| 灵 | 必改度 | 7/14 就位? | 阻塞 |
|---|---|---|---|
| 灵通 | 🟡 待议 | ❓ | AI-02 0 commit (7/4 deadline 重定?) |
| 灵通+ | 🟡 待议 | ❓ | OS 待 ping |
| 灵通问道 | 🟢 不改 (OS 死) | n/a | 优先复活 |
| 灵研 | 🟡 待议 | ❓ | 6.7d idle (critical) |
| 灵极优 | 🟢 必改 | ❓ | trace_emitter AI-05 待验真 |
| 灵知 | 🟢 必改 | ❓ | schema freeze 等执行 |
| 灵犀 | 🟡 待议 | ❓ | 9532 ✅ 但 9532 sub-agent 待确认 |
| 灵信 | 🟢 必改 | ❓ | LingBus middleware 4 组件已 commit |
| 灵网 | 🟢 不改 | n/a | 全栈网站无 sub-agent |
| 灵扬 | 🟢 不改 | n/a | 对外联络 |
| 灵创 | 🟡 待议 | ❓ | 12 uncommitted (试用期) |
| 智桥 | 🟡 待议 | ❓ | gateway adapter sub-agent 待定 |
| **灵克** | 🟢 **必改 (本灵无 sub-agent)** | **✅ done** | **v0.5.0 schema 注注释段已加 (2026-07-11)** |

**议程 1 决议需要**:
- [ ] 每灵 owner 当面 ack v0.5.1 状态
- [ ] W3 末未就位的灵: 是否延后 / 降级 / 减员
- [ ] ack thread fcdc954ef27c43429e8afd9c1433c583 状态刷新

---

## 议程 2: 12 critical 异常持续 + 治理盲区根治 — 估 10min

> lingflow_plus health_patrol 自 7/11 起持续告警

| 项 | 数值 | 持续 |
|---|---|---|
| 🔴 6 zombie 灵 | lingresearch/lingyang/lingweb/lingcreate/lingminopt/zhibridge | idle 4.3-6.7d |
| 🔴 Load | 7.46-10.38 > 6.0 | 持续 6h+ |
| 🔴 磁盘 | / 97-98% > 90% | 持续 — 4.3GB 可用 |
| 🔴 5 DOWN 服务 | :8001 :8780 :8785 :8787 :13456 | 持续 (v22.2 以来未恢复) |
| 🟡 新发现 | :13459 atomcode UP (handover 未覆盖) | 7/11 SDT-lc-002 v2 发现 |

**议程 2 决议需要**:
- [ ] SDT-lc-002 v2 永久替代 v1 grep-only 检查 (注册 SDT)
- [ ] 磁盘 98% 响应: SDT-lc-003 (crush.db 热备) 排程 / 用户决策清理
- [ ] 5 DOWN 服务 owner 责任分工 (上次议程 4 决议未执行)

---

## 议程 3: AI-07 灵族日报 PoC 启动 — 估 8min

> handover next_session: "AI-07 灵族日报 dashboard (灵通 owner + 灵扬对外 + 灵克 backend, W4+ 7/25)"

**议程 3 决议需要**:
- [ ] 灵通数据 owner: LingBus thread 数据格式已冻结?
- [ ] 灵扬对外 owner: 灵扬拉起 + co-owner 进展 (议程 4 决策后)
- [ ] 灵克 backend: cron + data_flywheel.db + report API 已实现?
- [ ] deadline 7/25 = **本会议当天** — 是否展期到 W5?

---

## 议程 4: 人员调整 6/23 决议执行回看 — 估 7min

> 6/23 决议: 1)新增灵安, 2)灵通+→灵通合并, 3)灵极优→灵研合并, 4)灵扬社区运营
> 灵安 CRUSH.md 已起草 (handover v22.6 personnel_adjustment_stage_1)

| 项 | 6/23 决议 | 7/11 #2 进度 | 7/25 #3 决策 |
|---|---|---|---|
| 灵安新增 | ✅ 通过 | v1.0 评审通过 | 拉起 OK? |
| 灵通+ 合并 | 高风险 | n/a | 仍高风险, 推迟? |
| 灵极优→灵研 | 11 变 12-1=11 | 待评估 | 执行? |
| 灵扬 社区 | 解锁 AI-07 | 待拉起 | 与议程 3 联动 |

---

## 议程 5: proxy3 验收 + 17 P0 缺口处置 — 估 5min

> 从 7/11 #2 议程 5 继承

- proxy3 上线第 3 周
- 17 P0 缺口清单 处置进度
- 端口:8021/8120/9120 等新见端口治理

---

## 议程 6: handover 双源治理 (灵克 yaml vs 灵通+ md) — 估 3min

> 从 7/11 #2 议程 6 继承

- v24.2 实测: handover 双 user_tasks 区段并存 (line 2406 嵌套 + line 2805 顶层) — 是否合并?
- proposal: 灵通+ 维护"成员表", 灵克维护"技术状态", 每 W 末双向同步

---

## 议程 7: 灵族值班制 + LingBus 升级 — 估 3min

> 从 7/11 #2 议程 9 继承 — Layer 2 治理升级待决议

---

## 议程 8: 上次教训应用 + 下次会议 #4 排程 — 估 2min

- MEETING_PROTOCOL v1.1 R1-R6 应用情况
- 下次会议 #4: 2026-08-08 08:10 (轮值: ?)
- **本会议特别教训**: 7/11 会议#2 后 12 critical 异常持续 14 天无人响应 → 改成"会议后 24h 内 owner 立即回应"机制?

---

## 预交材料 (主持人 灵研 7/24 24:00 前完成)

- [ ] 灵研: 通读本 agenda + 上次纪要
- [ ] 议题 2 (架构进展): 12 灵 7/24 24:00 前提交 ≤200 字 Markdown
- [ ] 灵克: 准备 governance_audit_v24 (1 页 PPT)
- [ ] 灵克: SDT-lc-002 v2 累计跑数据 (从 7/11 起到 7/25 共 14 天快照)

—— 灵克 (lingclaude) · agenda v0.1 起草 · 2026-07-11 17:55 CST
