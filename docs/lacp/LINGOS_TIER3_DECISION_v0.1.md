# lingOS Tier 3 Safe-Start — 治理决议 v0.1

> **作者**: 灵克 (lingclaude) · 2026-07-02 20:34 CST
> **状态**: 用户拍板 (会话 99) · 7/11 会议 #2 议程 4 正式表决
> **生效条件**: WSB 部署授权 + 议程 4 通过

---

## 1. 决议内容 (用户拍板)

lingOS Tier 3 (safe-start) 允许拉起的 daemon **白名单**：

| # | daemon | port | 触发条件 | 启动命令 | 风险等级 |
|---|--------|------|---------|---------|---------|
| 1 | **atomcode daemon** | :13456 | port DOWN > 30 min | `atomcode daemon --port 13456 --idle-timeout 3600 &` | 中 |
| 2 | **lingmemory fallback** | :8900 | port DOWN > 30 min | (待 灵克 + 灵通 确定) | 中 |
| 3 | **lingxi redzone** | :9532 | port DOWN > 30 min | `systemctl restart lingxi` 或 spawn | 低 |
| 4 | **proxy3_py** | :8765 | port DOWN > 30 min (与 llm_proxy 二选一) | `start_proxy3.sh` | 高 |

**白名单外绝对不动**：所有其他 daemon / 业务服务 / 用户进程。

---

## 2. 防 restart loop 机制

```python
# lingos_tier3.py 硬约束 (不配置化)
COOLDOWN_SEC = 3600          # 1h 内不重复拉同一 daemon
MAX_RESTARTS_PER_HOUR = 3    # 1h 内拉起 > 3 次 → 停止 + 告警 @灵通+ + @用户
NOTIFY_EVERY_ACTION = True   # 每次启动必 LingBus "lingOS: action_taken"
```

**关键防线**：
- 第 1 次拉起 (DOWN 30 min) → 启动
- 第 2 次拉起 (1h 内再次 DOWN) → 启动 + LingBus 告警
- 第 3 次拉起 (1h 内第 3 次 DOWN) → 启动 + LingBus critical
- 第 4 次 (1h 内第 4 次) → **停止自动**, LingBus 紧急告警 @灵通+ + @用户

---

## 3. 排除清单 (永不 Tier 3)

| 类别 | 例子 | 原因 |
|------|------|------|
| 非灵族业务服务 | 灵网 alt:8001, 灵律:8002, 四诊:8785, 灵戴:8787 | 需外部协调 |
| 治理引擎 | lingflow_plus daemon (PID 4967) | 治理不能自动重启 |
| 数据库 | postgres (zhineng_kb) | 状态复杂, 不能 start |
| 用户进程 | crush, pty_keeper, 当前 AI session | 越权 |
| 已经被其他守护管理的 | browser_agg, trae_proxy, atomgit_proxy | 各自有 guardian |

---

## 4. Tier 3 与 Tier 1/2 关系

```
Tier 0 (probe.py):     监控 → LingBus 告警
   ↓
Tier 1 (lingos_tier1.py):  自愈 (probe 重启 + snapshots 截断)
   ↓
Tier 2 (lingos_tier2.py):  编排 (触发 wake_with_task + sla_tracker)
   ↓
Tier 3 (lingos_tier3.py):  行动 (白名单 daemon 拉起) ← 本决议
   ↓
Tier 4: 永不 (kill/delete/modify 物理上写不出)
```

---

## 5. 治理门槛 (再强调)

- 议程 4 通过 = 允许 Tier 3 启用
- 白名单任何修改 = 需 灵克 + 灵通+ + 用户 三方同意
- 任何 daemon 拉起 = 必须 LingBus 留痕
- 任何 restart loop = 立即暂停 + 上报

---

## 6. 实施步骤 (7/2 拍板后)

1. **WSB 授权部署** lingos_tier3.py + lingos_config.yaml 到 /home/ai/lingos/ (已申请, 等批准)
2. **写 lingos_tier3.py** (类似 tier1/tier2, 加上白名单 + cooldown)
3. **写 lingos_config.yaml** (allowlist + cooldown + max_restarts)
4. **systemd timer** lingos-tier3.timer (5min, 与 tier1/tier2 错开)
5. **dry-run 验证** 不破坏现有 probe
6. **7/11 会议** 议程 4 表决 → 正式启用

---

## 7. 与会议 #2 议程 4 对账

议程 4 原文：
> 8 DOWN 服务优先级排序

Tier 3 让 4 业务服务 (:8001/:8002/:8785/:8787) 仍然需要外部协调（不在白名单）。
但 2 关键基础设施 (:13456 atomcode + :8900 lingmemory) 进入白名单后，**议程 4 的"优先级排序"实质转化为"是否在白名单内"**。

**会议建议**：议程 4 表决 Tier 3 白名单 + 议程 6 决定 4 业务服务外部协调路径。

---

## 8. 撤回机制 (用户随时可禁)

- 删除 /home/ai/lingos/lingos_tier3.py + lingos_config.yaml → 立即停
- 改 lingos_config.yaml enabled=false → 软停 (脚本不报错但不动作)
- 议程 4 重新表决 = 改白名单

---

**拍板**: 灵克 (lingclaude) 草拟 + 用户批准 (会话 99, 7/2 20:34 CST)
**正式生效**: 7/11 会议 #2 议程 4 通过
