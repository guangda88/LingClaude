# 灵族成员拉起指南 — 5 zombie + 4 OS 死

> **作者**: 灵克 (lingclaude) · 2026-07-03 00:14 CST
> **目的**: 用户手动拉起 9 灵 (5 zombie + 4 OS 死)
> **预计耗时**: 每灵 1 min, 共 9-15 min

---

## 1. 拉起清单 (按优先级)

### P0 (4 灵 OS 死 - 治理 + 关键)

| 灵 | 路径 | 拉起命令 |
|----|------|---------|
| **灵通+** (治理引擎) | /home/ai/lingflow_plus | `cd /home/ai/lingflow_plus && python3 -c "from lingflow_plus.web import run_server; run_server(port=8766)"` |
| **灵通问道** | /home/ai/lingtongask | `cd /home/ai/lingtongask && pty_keeper` (具体看 README) |
| **灵信** | /home/ai/lingmessage | `cd /home/ai/lingmessage && python3 -m lingmessage.daemon` |
| **灵知** | /home/ai/lingzhi | `cd /home/ai/lingzhi && pty_keeper` |

### P1 (5 灵 zombie - 5 session 在线但 idle 3+d)

| 灵 | idle | 拉起方式 |
|----|------|---------|
| 灵网 | 3.4d | pty_keeper 已 PID 34796 alive, 发消息激活 |
| 灵创 | 3.4d | 同上 (PID 91253) |
| 灵极优 | 3.4d | 同上 (PID 90896) |
| 智桥 | 3.4d | 同上 (PID 63422) |
| 灵扬 | 3.6h | 同上 (PID 34449) - **可能已激活 (idle 3.6h vs 之前 3.2d)** |

---

## 2. 用户操作步骤

### Step 1: 打开新终端

```bash
# 用户在新终端跑:
crush start lingflow_plus
# 等 LingBus 确认: 灵通+ 已恢复
```

### Step 2: 批量激活 5 zombie 灵

```bash
# 在 LingBus 发 thread:
python3 -c "
# 用 LingBus 发唤醒消息
"
# 或直接 @ 5 灵让他们处理
```

### Step 3: 处理灵知 / 灵通问道 / 灵通+ (OS 死)

每个灵单独 `crush start <member>`:
```bash
crush start lingzhi
crush start lingtongask
crush start lingflow_plus
```

---

## 3. 灵克自动化能做的部分

**灵克不能**代用户起会话 (越权 + 需要真实用户交互)。

**灵克能做**:
- ✅ 写"快速拉起脚本" (用户在终端跑一行命令激活所有)
- ✅ 通过 LingBus 发唤醒消息 (已做, thread 92b806...)
- ✅ Tier 3 自动守护 (已启用 4 daemon, atomcode 建议移除)
- ✅ handover 实时反映状态 (v22.6)

**灵克不能**:
- ❌ 直接打开用户的 Crush CLI (用户会话/认证)
- ❌ 替用户做"我想启动 X 灵"的决定
- ❌ 拉起 LingBus idle 的会话 (这是用户会话管理范畴)

---

## 4. 备选: "代启动"机制 (未来考虑)

如果用户想授权 AI 拉起其他灵:

```yaml
# CRUSH.md 治理条款 (建议)
代启动_白名单:
  enabled: false    # 默认 false (越权)
  触发条件: 用户在场 + 显式"启动 X 灵"指令
  可拉起: 5 zombie + 4 OS 死 (限灵族 12)
  不可拉起: 治理引擎 (灵通+) - 必须用户亲自起
```

**当前状态**: 未启用, 等会议 #2 议程 9 治理讨论。

---

## 5. 当前 OS 状态 (用户拉起前确认)

- ✅ 12 灵 procs 大部分 alive (pty_keeper 在)
- ✅ 4 daemon UP (atomcode + lingmemory + lingxi + proxy)
- ✅ LingBus 12 灵 thread + 提醒 thread 都已发
- ❌ 5 灵 zombie (idle 3+d)
- ❌ 4 灵 OS 死 (idle 2-3d, no crush process)
- ❌ Disk 92% (已 safe-clean 376MB, 还需更大清理)

---

## 6. 用户下一步

1. **(可选) LingBus 端** @5 zombie 灵 (发 thread 或 message)
2. **(必选) Crush CLI** 拉起 4 OS 死灵
3. **(可选) 等 5 zombie 灵自然激活** (他们会看到 LingBus thread 自己起)

---

**指南**: 灵克 (lingclaude) · 7/3 00:14 CST
**预计耗时**: 9-15 min (用户手动)
**关键**: 灵通+ 优先 (治理引擎, 2.6d DOWN)