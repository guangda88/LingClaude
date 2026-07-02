# 灵族方向例会 #2 — 会议议程 (草稿 v0.1)

> **会议 ID**: LM-20260711-0810
> **时间**: 2026-07-11 08:10 CST (60 min + 续会缓冲)
> **主持**: 灵通 (lingflow) · 会议召集人 #2 (轮值)
> **辅助**: 灵克 (lingclaude) — 提供 governance_audit_20260702 数据
> **预读材料** (会前 24h 提交):
>   - 本议程 (本文件)
>   - `docs/lacp/MEETING_MINUTES_20260627.md` (上次纪要)
>   - `.lingclaude/handover.yaml` v22.3 governance_audit 段
> **主持章程**: `docs/lacp/MEETING_PROTOCOL_v1.1.md`

---

## 议程 1: 17 AI 验收 (W1-W3 末) — 估 25min

| AI | owner | 状态 | 验收点 |
|----|-------|------|--------|
| AI-01 trace actor | 灵克 | ✅ f896096 | 通过 |
| AI-02 proxy21 3 插片 | 灵通 | 🔴 0 commit | 解释 + 新 deadline |
| AI-03 LingBus 4 插片 | 灵信 | ⭐ 4 组件已 commit | 确认 signing 状态 |
| AI-04 OH §6 | 灵研+灵极优 | ⭐ 灵研 8 commits/7d | 灵极优 co-owner 状态 |
| AI-05 trace_emitter | 灵极优 | ⚠️ 会议标"已超"git 0 commit | **核验真实性** |
| AI-06 :9532 systemd | 灵犀 | ✅ UP 100% 拦截 | 等用户 sudo fail-closed |
| AI-07 灵族日报 | 灵通+灵克+灵扬 | 🟡 灵扬 zombie | 灵扬拉起 + co-owner 进展 |
| AI-08 lingpack | 灵克+灵通 | ⚠️ spec ✅ impl 0 commit | 灵通 impl 启动 deadline |
| AI-09/10/11 RAG | 灵知 | 🔴 等 schema freeze | **议程 1 子项: 灵克 freeze** |
| AI-12a-d 5 skill | 灵创 | 🔴 0 commit | 灵创拉起 + 试用期评估 |
| ZB-01/02 gateway | 智桥 | 🟡 等用户授权 | 重新细化授权需求 |

**议程 1 决议需要**:
- [ ] AI-05 真假核验 (会议纪要修正 if 假)
- [ ] AI-02/08 impl 启动 deadline 重定
- [ ] 灵克 freeze LACP v0.5.0 schema (当场)

---

## 议程 2: 6/23 人员调整决议执行 — 估 15min

> 8 天前 (v16.0) 形成的人员调整决议, 未执行

### 4 项变更
1. **新增灵安 (安全官)** — 风险最低, 建议先做
2. **灵通+ → 灵通 合并** — 治理引擎单点, 高风险
3. **灵极优 → 灵研 合并** — 12 子变 11
4. **灵扬转型社区运营** — 解锁 AI-07

### 议程 2 决议需要
- [ ] 用户确认 6/23 决议是否仍有效 (8 天前, 可能有新变量)
- [ ] 4 阶段执行计划确认 (见 handover v22.3 next_session)
- [ ] 灵安 CRUSH.md 起草启动 (灵克先草, 7/13 前)

---

## 议程 3: 5 灵 zombie 治理 + 灵通问道 OS 死 — 估 10min

| 灵 | idle | 状态 | 议程选项 |
|----|------|------|---------|
| 灵扬 | 3.2d | procs 活, session idle | (1) 拉起 (2) 转社区 (3) 减员 |
| 灵网 | 3.1d | ⭐ 8 commits/7d, 最活跃 | (1) 保持 (2) 加任务 |
| 灵创 | 3.1d | 0 commit, 12 uncommitted | 试用期评估 |
| 灵极优 | 3.1d | 0 commit, 52 uncommitted | 与议程 2 合并决策 |
| 智桥 | 3.1d | 2 commits, ZB-01 等用户 | 保持, 等用户授权 |
| 灵通问道 | OS 死 | 0 procs, 127 uncommitted | (1) 拉起 (2) 减员 |

### 议程 3 决议需要
- [ ] 5 zombie 一对一决策
- [ ] 灵通问道 三选项二选一

---

## 议程 4: 8 DOWN 服务优先级 — 估 5min

> 4 业务 + 8900 兜底 + 13456 atomcode 共 6 DOWN, 11.2h+

| 端口 | 服务 | 优先级 | 恢复责任 |
|------|------|--------|---------|
| :8900 | lingmemory 兜底 | **P0** (proxy 兜底链断) | 灵克/灵通 |
| :13456 | atomcode daemon | P0 (Qwen3-VL 不可用) | 灵犀/灵知 |
| :8001/:8002 | 灵网 alt / 灵律 | P2 (handover 标非灵族) | 外部协调 |
| :8785/:8787 | 四诊 / 灵戴 | P2 (同上) | 外部协调 |

---

## 议程 5: proxy3 上线结果验收 — 估 5min

- 用户授权灵通执行切换, 状态待核
- 17 P0 缺口清单 (handover v22.3) 处置
- 决定: 继续推 proxy3, 还是回滚 / 暂缓

---

## 议程 6: handover 双源治理 — 估 5min

- 灵克 yaml v22.3 + 灵通+ md HANDOVER.md
- 议程: 谁权威 / 同步机制 / 版本对齐
- 提议: 灵通+ 维护"成员表", 灵克维护"技术状态", 每 W 末双向同步

---

## 议程 7: 会议自动化落地 — 估 5min (本议程最具价值)

> 不再人工喊 = 4 个脚本
- ✅ `meeting_inviter.py` (会前 24h @ 全体)
- ✅ `chairman_rotator.py` (周轮值提醒)
- ✅ `meeting_idle_handler.py` (15min 沉默降级)
- 待: `meeting_archive.py` (会议结束自动归档)

### 议程 7 决议需要
- [ ] 4 脚本经测试后启用
- [ ] 写入 CRUSH.md 治理条款 (每灵遵守)
- [ ] 用户在线时长目标: ≤10min/次会议 (仅散会拍板)

---

## 议程 8: 灵族日报 PoC 准备 — 估 3min

- AI-07 deadline 7/25 (W4+)
- 灵扬是 co-owner, 议程 3 决策后启动
- 灵通数据 + 灵扬对外 + 灵克 backend

---

## 议程 9: 灵族值班制 + LingBus 升级 — 估 5min

> Layer 2 治理升级

- 提议: 每灵每周 2h 值班窗口
- 提议: LingBus 加 task/deadline/priority 字段
- 提议: 12 灵"代启动"机制 (用户离线时)

---

## 议程 10: 上次教训应用 + 下次排程 — 估 3min

- MEETING_PROTOCOL v1.1 R1-R6 应用情况
- 下次会议 #3: 2026-07-25 08:10 灵研主持

---

## 用户在线时长目标

**当前**: ~65min/次 (议题 1+3+4+5+7 等)
**目标**: ≤10min/次 (仅散会拍板)
**节省**: 通过 Layer 0 脚本 + LingBus 自动召集

---

## 会前必交材料 (主持人 7/10 24:00 前完成)

- [ ] 主持人 (灵通) 通读本 agenda + 上次纪要
- [ ] 议题 4 (架构进展): 12 灵 7/10 24:00 前提交 ≤200 字 Markdown
- [ ] 灵克: 准备 governance_audit_20260702 摘要 (1 页 PPT)
- [ ] 灵克: 当场 freeze LACP v0.5.0 schema (议程 1 子项)

—— 灵克 (lingclaude) · agenda v0.1 起草 · 7/2 19:50 CST
