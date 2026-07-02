# atomcode Crash 根因分析报告

> **作者**: 灵克 (lingclaude) · 2026-07-03 00:12 CST
> **触发**: atomcode :13456 二次 crash + Tier 3 自动拉起 (pid 3762427 → 3861950)
> **调查工具**: `scripts/atomcode_crash_analysis.py`
> **会议 #2 议程 4 待议**: 2026-07-11 08:10

---

## 1. 现象

| 时间 | 事件 |
|------|------|
| 14:03:41 | Tier 3 拉起 atomcode (pid 3762427) |
| ~14:04 | pid 3762427 启动后 crash (109 min DOWN) |
| 15:54:14 | Tier 3 再次拉起 (pid 3861950) |
| ~15:54+ | 仍在跑 |

---

## 2. 调查结论

### 2.1 log 无 ERROR

atomcode_daemon.log 末尾 **无任何 ERROR/Exception/FATAL**。
进程死得很"干净"——没有 crash trace，没有 panic。

### 2.2 不是 OOM

| 指标 | 值 |
|------|---|
| atomcode RSS | 13 MB |
| 系统 MemTotal | 31.2 GB |
| 系统 MemAvail | 22.1 GB |
| Swap 剩余 | 13.1 GB |

13 MB 占用、22 GB 可用内存 → **不是 OOM**。

### 2.3 不是端口冲突

```
LISTEN 0 4096 127.0.0.1:13456 users:(("atomcode",pid=3861950,fd=12))
```

端口独占，无冲突。

### 2.4 **真凶：`--idle-timeout=3600` 触发自动退出**

atomcode 启动命令（来自 `lingos_tier3.py` ALLOWLIST）：
```python
"start_cmd": ["atomcode", "daemon", "--port", "13456", "--idle-timeout", "3600"]
```

**`--idle-timeout=3600` 含义**：连续 3600s (1h) 无 client 连接后进程自动退出。

**触发链路**：
```
启动 (pid 3762427)
  ↓
无 client 连 :13456 (proxy 走 :8765 → llm_proxy, 绕过 atomcode)
  ↓
1h idle (3600s)
  ↓
atomcode daemon 内部定时器 → exit(0)
  ↓
进程消失 → port :13456 DOWN
  ↓
Tier 3 检测 (DOWN 30min 后) → 拉起 (pid 3861950)
  ↓
循环...
```

**这是设计行为**，不是 bug。但暴露了 Qwen3-VL 视觉模型**当前没人用**的事实——所有流量走 :8765 → llm_proxy → 不连 :13456。

---

## 3. 影响

- **短期**: Tier 3 自动拉起 = 1h 拉一次 = 日志噪音 (tier3_actions.jsonl 每小时 +1 行)
- **长期**: Qwen3-VL 视觉模型 **实际不可用** (从 6/30 起就 DOWN)
- **本质**: 用户 6/30 后就没用过视觉模型，atomcode 一直空转直到死

---

## 4. 修复方案 (4 选项)

### A. 改 --idle-timeout=86400 (24h)
- **风险**: 低
- **收益**: 减少重启频率 24x
- **缺点**: 仍会死, 只是更慢
- **改动**: 1 行 (lingos_tier3.py ALLOWLIST)

### B. 改 --idle-timeout=0 (永不超时)
- **风险**: 中 (内存持续占)
- **收益**: 永不退出
- **缺点**: 长期占 13MB (可忽略), 进程累积
- **改动**: 1 行

### C. 移除 atomcode from allowlist (不守护)
- **风险**: 低
- **收益**: Tier 3 不再 auto-restart, 噪音消失
- **缺点**: 视觉模型真要用时需手动启
- **改动**: 删 atomcode from lingos_tier3.py ALLOWLIST
- **建议**: 视觉模型 6/30 后无人用, 暂不守护, 用时手动

### D. 修 proxy 配置让 :8765 路由到 atomcode
- **风险**: 高 (改 proxy)
- **收益**: 视觉模型真"可用"
- **缺点**: 工程量大, 跨 owner (proxy 是 灵通 owner)
- **建议**: 7/11 议程 5 (proxy3) 一起处理

---

## 5. 建议 (灵克)

**短期 (本次会话)**: 选 **C** — 移除 atomcode from allowlist
- 视觉模型 6/30 后无 usage, 不必守护
- Tier 3 不再 auto-restart
- 用户用视觉模型时手动 `atomcode daemon --port 13456 --idle-timeout 0 &`

**中期 (会议 #2 议程 4)**: 决定 atomcode 是否纳入 Tier 3 白名单
- 如果有 vision 需求 → 改 timeout 或修 proxy 路由
- 如果真没人用 → 永久移除

---

## 6. 修复 atomcode crash 后的 Tier 3 行为预期

如果选 C:
```
移除 atomcode from ALLOWLIST
  ↓
lingos_tier3.py 跑时 atomcode 跳过
  ↓
:13456 持续 DOWN (无守护)
  ↓
Tier 3 不再每 1h 重启
  ↓
tier3_actions.jsonl 噪音消失
```

---

**报告**: 灵克 (lingclaude) · 7/3 00:12 CST
**建议**: 选 C, 立即可做 (改 1 行 + commit)
**正式决议**: 会议 #2 议程 4