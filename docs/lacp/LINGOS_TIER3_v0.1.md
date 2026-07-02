# lingOS Tier 3 Safe-Start 实施说明 v0.1 (待议程 4 决议)

> **作者**: 灵克 (lingclaude) · 2026-07-02 20:36 CST
> **状态**: 实施说明 · 待会议 #2 议程 4 正式表决
> **关系**: Tier 3 = lingOS "看见 + 有限动手" 的动手部分

---

## 1. Tier 3 设计原则 (再确认)

```yaml
tier_3_safe_start:
  enabled: true                # 拍板 OK, 部署待 WSB + 议程 4
  allowlist:                    # 白名单 (静态, 不动态)
    - "atomcode daemon"          # :13456
    - "lingmemory fallback"      # :8900
    - "lingxi redzone"           # :9532
    - "proxy3_py"                # :8765
  cooldown_sec: 3600            # 1h 内不重复拉
  max_restarts_per_hour: 3      # 防 restart loop
  require_lingbus_alert: true   # 每次启动必 LingBus 留痕
```

---

## 2. 4 个白名单 daemon 启动策略

### 2.1 atomcode daemon (:13456)
- **命令**: `atomcode daemon --port 13456 --idle-timeout 3600 &`
- **状态**: DOWN 11.2h+, 影响 Qwen3-VL
- **优先级**: P0

### 2.2 lingmemory fallback (:8900)
- **命令**: `start_lingmemory.sh` (待确认)
- **状态**: DOWN, 影响 llm_proxy 兜底链
- **优先级**: P0

### 2.3 lingxi redzone (:9532)
- **命令**: `systemctl restart lingxi` 或 spawn
- **状态**: UP (100% 拦截) — 备用
- **优先级**: P1

### 2.4 proxy3_py (:8765)
- **命令**: `start_proxy3.sh`
- **状态**: 8765 当前被 llm_proxy 占用 — 拉起会冲突
- **优先级**: P1 (需先决定 proxy3 vs llm_proxy)

---

## 3. 防 restart loop 机制

```python
# lingos_tier3.py 硬约束
if restart_count[daemon] >= MAX_RESTARTS_PER_HOUR:
    send_lingbus_critical(daemon)
    notify_government(daemon)  # @灵通+
    notify_user(daemon)         # @用户
    return  # 停止自动
```

---

## 4. 排除清单 (永不 Tier 3)

- 4 业务服务 (:8001/:8002/:8785/:8787) — 非灵族, 外部协调
- lingflow_plus daemon (PID 4967) — 治理引擎不能自动重启
- 所有 postgres — 状态复杂
- 所有 AI session 进程 — 越权

---

## 5. 实施步骤

1. **WSB 授权** (已申请 2771937b-4c12-47c6-b0fd-dd5add658ceb, 等批准)
2. **写 lingos_tier3.py** (allowlist + cooldown + LingBus)
3. **写 lingos_config.yaml** (静态配置)
4. **部署到 /home/ai/lingos/** (cp + chmod +x)
5. **systemd timer** lingos-tier3.timer (5min)
6. **dry-run 验证** 不破坏现有 probe
7. **会议 #2 议程 4 正式表决**

---

**正式生效**: 会议 #2 (7/11) 议程 4 通过
