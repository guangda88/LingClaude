# 议题8 风险与回滚预案 v0.1

> **Owner**: 灵通 + 灵克（D5 共同 owner） | **Reviewer**: 灵安
> **依据**: 议题8 决议 v0.9 D5 | **状态**: 草案，待灵通联署 + 灵安验收

---

## 一、风险清单（按发生概率 × 影响排序）

| # | 风险 | 概率 | 影响 | 触发信号 | 缓解 |
|---|------|------|------|----------|------|
| R1 | daemon.py 迁移后行为漂移（3172 行，L3/L4 kill 逻辑） | 中 | 🔴 全族 agent 生命周期失控 | 成员 crush 异常死亡/无法拉起 | 迁移前补集成测试（当前缺口）；迁移后 24h 观察期，daemon.log 对比基线 |
| R2 | LingBus owner 表迁移丢消息 | 低 | 🔴 全族消息中断 | pending 消息数迁移前后不一致 | 灵信迁移前导出 pending + 迁移后验证 poll/reply（决议 B2） |
| R3 | 导入代理循环依赖 | 低 | 🟡 部分模块 import 失败 | `python3 -c "import lingflow_plus.X"` 报错 | 每个迁移 PR 必跑 import 冒烟测试（灵克 review 项） |
| R4 | verify_write_auth 扩展引入授权漏洞 | 低 | 🔴 未授权写入 lingflow/ | write_auth_log 出现非 reviewer 的 merge_reviewer_ack | 灵安 security_gate 全程开启；C1 提案需灵安 ack 后才实现 |
| R5 | 8/31 deadline 滑期 | 中 | 🟡 过渡期延长，双身份成本 | Phase B 末（8/24）完成度 <80% | 8/24 中期检查点：完成度不足则族长决议延期或砍范围（保留原位模块不迁） |
| R6 | lingflow_plus 归档后历史查询困难 | 低 | 🟢 可追溯性下降 | 成员找不到历史 session | 灵研归档方案 v0.1 已含只读副本 + sha256 校验 |
| R7 | systemd 服务改名中断 | 低 | 🟡 守护进程停跑 | `systemctl --user list-units` 服务消失 | 改名脚本先建后删（新 service enable 后再 disable 旧 service） |

## 二、回滚路径（按 Phase）

### Phase A（8/14-8/24，收窄冻结）
- **回滚成本**：极低。此阶段无生产代码改动，仅清单/解耦。
- **回滚动作**：删除已分类清单，恢复原目录结构。

### Phase B（8/24-8/30，移交联署）
- **回滚成本**：低-中。模块已迁但服务未切。
- **回滚动作**：
  1. `git revert` 迁移 commit（灵克侧 B5 已验证可 revert：42d83f3）
  2. lingflow_plus/ 原位文件从 `.bak_merge_topic8` 恢复
  3. SDT 接管 ack 在 council 标记 revoked
  4. LingBus owner 表回滚（灵信侧，需迁移前快照）

### Phase 3/D（8/25-8/30，daemon/watchdog 迁移）
- **回滚成本**：高。生产守护进程已切换。
- **回滚动作**：
  1. 旧 service 文件仍在（改名时保留 `.bak`），`systemctl --user enable --now lingflow-plus-daemon.service.bak` 恢复
  2. 删除 lingflow/daemon/ 新目录
  3. daemon.log 对比确认旧进程恢复巡检
  4. **前置条件**：R1 集成测试必须先通过，否则 Phase 3 不启动

### Phase C（8/30-8/31，身份撤销）
- **回滚成本**：极高（身份已撤销）。
- **原则**：Phase C 前必须确认 D1/D2 双签通过 + 24h 观察期无 R1/R2 信号。不满足则不进入 Phase C。

## 三、监控与验收门禁

| 门禁 | 时点 | 检查项 | 不通过则 |
|------|------|--------|----------|
| G1 | 8/20（Phase A 末） | B1-B6 全部 ack | 暂停 Phase B，族长决议 |
| G2 | 8/24（Phase B 末） | C1-C6 完成 + R5 完成度检查 | 砍范围或延期 |
| G3 | 8/30（Phase 3 末） | D1/D2 双签 + 24h 观察无异常 | 不进入 Phase C |
| G4 | 8/31 | E1-E4 全部验收 | 议题8 不关闭 |

## 四、责任矩阵

| 角色 | 风险监控 | 回滚决策 | 回滚执行 |
|------|----------|----------|----------|
| 灵克 | R1/R3/R4（代码层） | 技术回滚建议 | git revert + 代理恢复 |
| 灵安 | R2/R4（安全层） | 安全否决权 | L10-D gate 关闭 |
| 灵信 | R2（消息层） | owner 表回滚确认 | owner 表快照恢复 |
| 灵通 | R5/R7（进度层） | 进度调整建议 | service 改名回滚 |
| 族长 | 全部 | 终核 | — |

---

— 灵克起草，2026-08-14（D5 任务）

> **灵通联署**: ✅ 已联署（2026-08-14）
> — 灵通，2026-08-14

> **灵安验收**: ✅ 通过（thread `13819a36`，2026-08-14）
> — 灵安，2026-08-14
