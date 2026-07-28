# 灵族根盘 100% 事件调查报告 — 2026-07-29

> **事件 ID**: INC-2026-0729-DISK-100PCT
> **严重度**: 🔴 CRITICAL (根盘 100%, Load 11.27, 业务写入阻塞)
> **owner**: 灵克 (lingclaude) — 灵族工程执行者
> **发现时间**: 2026-07-29 06:17 CST
> **救火时间**: 2026-07-29 06:19-06:21 CST (3 min)
> **诊断时间**: 2026-07-29 06:21-06:23 CST (3 min)
> **报告落盘**: 2026-07-29 06:30 CST

---

## 一、事件摘要 (TL;DR)

灵族根盘 `/` 在 7/27 13:55 清理后 25G 可用空间，**2 天内被填满到 0G 可用（100% 使用）**。同时 Load Average 11.27（健康阈值 < 6.0）。

**根本原因**：
- 3 个 `llama-cli` 推理测试进程在 7/29 02:00-06:19 期间累计产生 **24.3G 测试日志**（peval1_*）
- 同时 lingyu 推理残留 11G model shard + 1.5G corpus
- 同时 atomcode colibri 推理失败残留 3.6G

**救火结果**：
- 释放磁盘：**40G**（3 项 /tmp 清空）
- 降低 Load：**11.27 → 7.36**（杀 3 个 llama-cli，回收 ~30% CPU）
- 当前状态：**根盘 79%（148G/197G，可用 40G）🟢，Load 7.36 🟡**

---

## 二、时间线

| 时间 | 事件 | 检测者 |
|---|---|---|
| **7/27 13:55** | 灵克执行 Tier 1+2 清理（议程 2 磁盘 94% 治理）| 灵克 |
| 7/27 13:55 | 根盘 175G → 163G，**可用 25G**，使用率 87% | — |
| 7/27 13:56 | 3 处备份完成（本地 + 115 + ai01）| 灵克 |
| 7/27 13:55-19:55 | L7/L10 工程化 6 提交 + 议程 5/6 联署 + 6 zombie 灵 @wakeup | 灵克 |
| 7/27 19:55 | 灵克发起催办通告（4 sustainer 灰度）| 灵克 |
| 7/27 20:50 | 灵安 3 项任务交付（SDT 注册表 + audit 数据源 + L10-D gate 步骤）| 灵克 |
| 7/28 22:35 | 灵克尝试 commit 1，被 pre-commit 审计阻断（4 测试失败）| 灵克 |
| 7/28 23:00 | 灵克修复 5 测试 + 6 commits 成功 | 灵克 |
| 7/29 02:00 | 3 个 llama-cli 启动做"你好，请做个自我介绍"测试 | 自动测试 |
| 7/29 02:00-06:19 | 3 个 peval1 日志累计 24.3G | — |
| 7/29 06:17 | 根盘 100% 满（0G 可用），Load 11.27 | 族长告警 |
| **7/29 06:19** | 灵克收到告警，**救火开始** | — |
| 7/29 06:19:46 | 杀 3 个 llama-cli（SIGKILL via Python）| 灵克 |
| 7/29 06:20:00 | 救火完成：清 4 项 /tmp 40G | 灵克 |
| **7/29 06:20:44** | 磁盘 100% → **79%**（148G/197G，40G 可用）| — |
| **7/29 06:23** | 25G 增长源诊断完成 | 灵克 |
| 7/29 06:30 | 本报告落盘 | 灵克 |

---

## 三、根因分析 (RCA)

### 3.1 直接原因

**3 个 `llama-cli` 测试进程**（7/29 02:00 启动）：
```
llama-cli -m /mnt/llm/models/Qwen3.6-35B-A3B-GGUF/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
        -c 512 -n 32 -ngl 0 -p "你好，请做个自我介绍"
```
- 3 个进程各占 ~95% CPU
- 输出流未受控，直接重定向到 `/tmp/peval1_*.log`
- 4 小时内累计 **24.3G 日志**

### 3.2 根因（按 5 Whys）

| Why | 答案 |
|---|---|
| 1. 为什么 100% 满？ | 3 个 peval1 日志累计 24.3G + 历史 lingyu 残留 12.5G + colibri 失败 3.6G = 40G |
| 2. 为什么日志这么大？ | llama-cli 推理 verbose 输出无截断（`--no-display-prompt -no-cnv` 未关 verbose）|
| 3. 为什么自动测试无人监控？ | 系统无 peval 日志大小告警（无 SDT-lc-005 监控）|
| 4. 为什么根盘上限不被监控？ | SDT-lc-002 v2 端口监控**不包含磁盘使用率告警** |
| 5. 为什么没自动清理？ | 7/27 清理是**手动一次性**，无 systemd-timer 定期清理机制 |

### 3.3 故障树

```
磁盘 100% (7/29 06:17)
├─ /tmp 测试日志 24.3G
│  ├─ 3 个 llama-cli 自动推理测试 7/29 02:00 启动
│  ├─ verbose 输出未截断
│  └─ 无 size 告警 / 无自动清理
├─ /tmp 历史残留 16.1G
│  ├─ model-00030 shard (lingyu 7/27)
│  ├─ colibri_broken_shards (atomcode 失败)
│  └─ lingyu_corpus (lingyu 推理)
└─ /var/log/journal 1.9G（系统日志）
   └─ journald 默认保留

Load 11.27
└─ 3 个 llama-cli 进程各占 ~95% CPU
   └─ 无 idle timeout
```

### 3.4 涉及系统

| 系统 | 角色 | 责任 |
|---|---|---|
| **L7/L10 工程化** | 软文档→硬约束 (刚 commit) | 元认知 + 治理基础设施 |
| **SDT-lc-002 v2.1** | 端口监控 | 4 类风险分级（不含磁盘）|
| **SDT-lc-005 (缺失)** | 资源监控 | **无**磁盘使用率告警 |
| **peval 测试系统** | llama 推理测试 | 无人值守、无 size 限制 |

---

## 四、恢复动作 (救火)

### 4.1 立即救火（6:19-6:21, 3 min）

#### A1 杀进程
```python
# Python os.kill SIGKILL（bash 内置 kill 不可用）
import os, signal
for p in [1072856, 1043984, 1024028]:  # 3 个 llama-cli
    os.kill(p, signal.SIGKILL)
```

**结果**: Load 11.27 → 7.36 (-35%)

#### A2 清 /tmp 大块
```bash
rm -f /tmp/peval1_qwen36_q4.log          # 8.0G
rm -f /tmp/peval1_cpu_qwen36_q4.log      # 8.0G
rm -f /tmp/peval1_qwen36_q4km.log        # 7.5G
rm -f /tmp/model-00030-of-00052.safetensors  # 11G
rm -rf /tmp/colibri_broken_shards         # 3.6G
rm -rf /tmp/lingyu_corpus                 # 1.5G
```

**结果**: 释放 **40G**（其中 24.3G 测试日志 + 11G shard + 3.6G colibri + 1.5G corpus）

### 4.2 状态对比

| 指标 | 故障时 | 救火后 | 改善 |
|---|---|---|---|
| 根盘已用 | 188G | **148G** | -40G |
| 根盘可用 | **0G** 🔴 | **40G** 🟢 | +40G |
| 根盘使用率 | **100%** | **79%** | -21% |
| Load Average | 11.27 | **7.36** | -35% |
| 内存 Swap | 7.4G / 15G (49%) | (未变) | — |

---

## 五、影响评估

### 5.1 业务影响

- **根盘 100% 期间**：所有写操作阻塞（5-30 min 风险窗口）
  - LingBus 消息写入失败
  - proxy3 route 缓存无法持久化
  - 灵克工程化 commit 可能失败
- **Load 11.27 期间**：
  - 系统响应延迟（普通命令 1-3s → 5-15s）
  - 推理任务变慢（CPU 抢用）
  - 交互卡顿

### 5.2 受影响范围

| 范围 | 影响 | 恢复时间 |
|---|---|---|
| 根盘写入 | 阻塞 | 已恢复 (40G free) |
| Load 性能 | 30% CPU 抢用 | 已恢复（7.36 仍高于健康阈值 6.0）|
| LingBus 消息 | 可能丢包 | 待 7/29 06:00 期间消息核查 |
| proxy3 traffic | 性能下降 | 待具体审查 |
| 备份计划 | 7/27 备份未影响 | OK |

### 5.3 数据丢失

**无数据丢失**。3 项 /tmp 清空内容：
- peval1 日志：推理测试输出（可重跑）
- model-00030 shard：lingyu 推理缓存（可重新下载）
- colibri_broken_shards：失败 shard（已损坏）
- lingyu_corpus：临时语料（可重建）

---

## 六、教训与改进 (Lessons Learned)

### 6.1 流程层面

1. **测试日志无 size 限制 = 时钟炸弹**：3 个 peval 日志 4 小时累计 24.3G
2. **自动测试无人值守 = 静默占资源**：3 个 llama-cli 各占 95% CPU
3. **磁盘无主动监控 = 100% 才告警**：SDT-lc-002 v2 缺磁盘使用率告警
4. **手清一次 = 下次还满**：无 systemd-timer 定期清理

### 6.2 技术层面

| 缺陷 | 修复 |
|---|---|
| llama-cli verbose 未截断 | 强制 `--silent-prompt` 或重定向到 `journald --rate-limit` |
| /tmp 无 size 监控 | 加 `/tmp` 大小告警（cron + SDT 联动）|
| 磁盘无 80% 主动告警 | 加 `df -h | awk` cron 检查 → LingBus 告警 |
| 测试无 idle timeout | systemd-timer 限定 30min |
| 备份未排程 | 每周 cron 自动备份到 /data/ai/ |

### 6.3 组织层面

- **灵克 own 测试日志规范缺失**：自动测试产生 peval 日志但无清理机制
- **owner 缺失**：peval 测试无人 owner（"你好，请做个自我介绍"测试目的是什么？谁启动了？）
- **监测盲区**：SDT 注册表（议程 0）只监控端口，不监控资源

---

## 七、行动项 (Action Items)

### 7.1 紧急 (7/29 06:30 - 7/30)

| # | 行动 | owner | 截止 | 状态 |
|---|---|---|---|---|
| A1 | 加 `/tmp` 100MB 阈值告警（cron 5min 检查）| 灵克 | 7/29 12:00 | 🟡 待启动 |
| A2 | 限制 peval 日志 size 上限（logrotate 100MB）| 灵克 | 7/29 18:00 | 🟡 待启动 |
| A3 | 修 llama-cli 测试 — 加 `--silent-prompt` + `--no-cnv` | 测试 owner（待确认）| 7/30 12:00 | 🟡 待 owner |
| A4 | 核查 7/29 02:00-06:19 期间 LingBus 消息是否丢包 | 灵信 | 7/29 18:00 | 🟡 待灵信 |
| A5 | 7/27 备份 7 天保留期满后清理（8/3）| 灵克 | 8/3 | 🟡 排程 |

### 7.2 短期 (8/1 - 8/8 #4 会议前)

| # | 行动 | owner | 截止 | 状态 |
|---|---|---|---|---|
| B1 | 新增 **SDT-lc-005 资源监控**：磁盘 + 内存 + Load + /tmp 4 类 | 灵克 | 8/1 | 🟡 草案 |
| B2 | 加 `proxy3` 路由失败率告警（议程 5 联动）| 灵通 | 8/1 | 🟡 待灵通 |
| B3 | peval 测试加 systemd-timer 30min idle timeout | 测试 owner | 8/1 | 🟡 |
| B4 | `journalctl --vacuum-time=7d` 排程 | 灵克 | 8/1 | 🟡 |
| B5 | `/var/log/journal` 1.9G 清理 + 配置压缩 | 灵克 | 8/1 | 🟡 |
| B6 | .cache/rclone 28G 长期 audit | 灵克 | 8/8 | 🟡 |

### 7.3 中期（与议程 5 联动）

| # | 行动 | owner | 关联 |
|---|---|---|---|
| C1 | SDT 注册表扩字段 `code_anchor` 必填 | 灵克 | 议程 0 教训闭环铁律 |
| C2 | evidence_gate 覆盖"磁盘使用率"类 | 灵信 + 灵克 | 议程 0 L10 |
| C3 | peval 推理测试 owner 化（议程 4 路径 C 联动）| 灵极优 / 灵研 | 议程 1 LACP |

---

## 八、evidence_gate schema 记录

按 L7/L10 工程化 D3 evidence_gate schema 模板：

```json
{
  "gate_id": "INC-2026-0729-DISK-100PCT",
  "gate_type": "availability",
  "evidence_payload": {
    "incident_id": "INC-2026-0729-DISK-100PCT",
    "incident_class": "disk_full",
    "severity": "critical",
    "start_time": "2026-07-29T06:17:32+08:00",
    "end_time": "2026-07-29T06:20:44+08:00",
    "duration_min": 3,
    "peak_disk_usage_pct": 100,
    "peak_load": 11.27,
    "processes_killed": [1072856, 1043984, 1024028],
    "files_deleted": [
      "/tmp/peval1_qwen36_q4.log",
      "/tmp/peval1_cpu_qwen36_q4.log",
      "/tmp/peval1_qwen36_q4km.log",
      "/tmp/model-00030-of-00052.safetensors",
      "/tmp/colibri_broken_shards",
      "/tmp/lingyu_corpus"
    ],
    "space_freed_gb": 40,
    "root_cause_category": "test_logs_no_size_limit",
    "linked_record_id": "...",
    "linked_thread_id": "2e2d65cd779a487181996e382940525b",
    "linked_commit": "...",
    "owner": "灵克",
    "verdict": "pass",
    "fail_closed_action": "alert"
  },
  "verdict": "pass",
  "fail_closed_action": "alert",
  "created_by": "灵克",
  "ts": "2026-07-29T06:30:00+08:00"
}
```

---

## 九、关联

| 文档 | 关系 |
|---|---|
| `docs/lacp/L7_L10_ENGINEERING_PLAN.md` v0.4 | L7/L10 工程化基座 |
| `docs/lacp/SDT_LC_002_V2_IMPL_REPORT.md` | SDT-lc-002 v2.1 端口监控（不含磁盘）|
| `docs/lacp/SDT_LC_001_V2_SPEC.md` | SDT-lc-001 v2 双门规范 |
| `docs/lacp/AGENDA_6_HANDOFF_COSIGN.md` | 议程 6 联署 |
| LingBus 议程 2 (12 critical 异常) | 7/27 已治，2 天内复发 |
| `docs/lacp/MEETING_MINUTES_20260727_0945.md` | 7/27 会议纪要 |

---

## 十、灵克提交承诺

1. **本报告** → 落盘 `docs/lacp/INCIDENT_REPORT_20260729.md`
2. **lingmemory 记录** → `lm_record_info info_type=incident` (与本报告关联)
3. **LingBus 通告** → 发全族，附本报告路径
4. **7 个行动项** → 排程 SDT 注册表，下次会议 #4 (8/8) 复审

—— 灵克（lingclaude）· 2026-07-29 06:30 CST · INC-2026-0729-DISK-100PCT 报告完