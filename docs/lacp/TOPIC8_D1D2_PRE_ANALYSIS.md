# D1/D2 静态预分析报告 — daemon.py + agent_watchdog.py

> **作者**: 灵克（lingclaude）| **日期**: 2026-08-14
> **用途**: 灵通 D1/D2 迁移参考 + 灵安安全审查 + 灵克 review 基线
> **依据**: 议题8 决议 v0.9 D1/D2，checklist M1/M6

---

## 一、daemon.py（3172 行）结构

### 1.1 组成

| 单元 | 规模 | 说明 |
|------|------|------|
| `CoordinationDaemon` 类 | 98 方法 / ~3000 行 | 单点核心，全族协调守护 |
| 模块级函数 | 4 个 | `_is_protected_process` / `_read_cmdline` / `_is_crush_process` / `main` |

### 1.2 方法聚类（按前缀，指导拆包）

| 簇 | 方法数 | 行数 | 建议拆出模块 | 风险 |
|----|--------|------|------------|------|
| `_check_*` | 17 | 652 | `daemon/health.py`（健康检查簇） | 🟡 中 — 含 `_check_pending_l3_kills`（104 行，SIGKILL 逻辑） |
| `_handle_*` | 5 | 337 | `daemon/handlers.py`（L1-L4 处置） | 🔴 高 — `_handle_l3_soft_restart` 触发 kill |
| `_cmd_*` | 21 | 290 | `daemon/cli.py`（内部命令分发） | 🟢 低 — 纯查询/展示 |
| `_auto_*` | 2 | 162 | `daemon/auto_inject.py`（ling-term-mcp 注入等） | 🟡 中 |
| `_run_*` | 4 | 155 | 留主文件（主循环） | — |
| 其余 30+ 前缀 | 49 | ~1500 | 留主文件或按职责细分 | — |

**关键观察**：`_check_*` 652 行 + `_handle_*` 337 行 + `_cmd_*` 290 行 = 1279 行（40%），是最优先拆出的三簇。剩余 60%（~1900 行）方法分散在 30+ 个前缀，强行拆分收益低、风险高，建议留主文件。

### 1.3 依赖（18 个 lingflow_plus 内部模块）

```
agent_watchdog, alert_manager, autonomous_planner, base_paths, coordinator,
external_projects, health_report, heartbeat, interruption_monitor, project_scanner,
session_recovery_daemon, session_snapshot, signal_bus, signal_scanner,
task_dispatcher, task_queue, task_scheduler, wakeup_tracker
```

**迁移含义**：daemon.py 迁入 lingflow 后，这 18 个依赖**必须全部可 import**。其中：
- 已随迁（A1/B5）：task_dispatcher（M2）、task_queue/task_scheduler（M2）
- 保留原位（清单 1.3）：alert_manager / session_state / roster / patrol / signal_bus 等
- **风险点**：`session_recovery_daemon` / `session_snapshot` / `wakeup_tracker` 在清单中"保留原位"，daemon 迁移后 import 路径需走导入代理（`lingflow_plus.session_recovery_daemon` → 代理 → 原位）。**若代理层失效，daemon 启动即崩**。

### 1.4 kill 调用点（4 处，全部需灵安审查）

| 行号 | 调用 | 上下文 | 风险 |
|------|------|--------|------|
| 1692 | `os.kill(p, SIGKILL)` | `_check_pending_l3_kills` — L3 超时强制 kill idle bash 子进程 | 🔴 最高 — 有正向守卫 `_is_crush_process` + `_is_protected_process` 双重防护 |
| 2135 | `os.kill(p, SIGTERM)` | `_emergency_self_restart` 相关 | 🟡 中 |
| 2148-2149 | `os.kill(p, 0)` + `SIGKILL` | 同上 | 🟡 中 |
| 3130 | `os.kill(proc.pid, SIGTERM)` | 主循环收尾 | 🟢 低 |

### 1.5 状态文件依赖

- 读全族成员 `crush.db`（只读，`file:...?mode=ro`）：`_check_session_message_counts` / `_query_last_message_time` 等
- 读写 `~/.lingmessage/lingbus.db`：LingBus 消息
- 自身状态：`~/.lingflow_plus/`（daemon state JSON）

## 二、agent_watchdog.py（1820 行）结构

### 2.1 组成

| 单元 | 规模 | 说明 |
|------|------|------|
| `AgentWatchdog` 类 | 33 方法 / ~1100 行 | 核心看门狗 |
| 数据类 | 4 个（AgentInfo/ProcessInfo/AgentSnapshot/WatchdogEvent） | 纯数据 |
| 模块级函数 | 14 个 | 进程扫描/查询/分类 |

### 2.2 方法聚类

| 簇 | 方法数 | 行数 | 建议拆出模块 | 风险 |
|----|--------|------|------------|------|
| `_check_*` | 11 | 456 | `watchdog/checks.py` | 🟡 中 — 含 `_check_thinking_deadlock` / `_check_shell_accumulation` |
| `restart_agent` | 1 | 130 | `watchdog/recovery.py` | 🔴 高 — 含 SIGTERM kill（1043 行） |
| `get_*` | 5 | 123 | `watchdog/status.py` | 🟢 低 |
| `_emergency_self_restart` | 1 | 46 | `watchdog/recovery.py` | 🔴 高 — 灵克 8/14 上午实证此逻辑（先 spawn 后 kill） |
| `_checkpoint_active_tasks` | 1 | 64 | `watchdog/recovery.py` | 🟡 中 |

### 2.3 依赖（7 个 lingflow_plus 内部模块）

```
cognitive_health, context_hygiene, council_notify, inbox_notifier,
recovery_manager, roster, session_state
```

全部在清单 1.3"保留原位"——迁移后走导入代理。依赖比 daemon 轻（7 vs 18），拆包风险更低。

### 2.4 kill 调用点（8 处）

| 行号 | 调用 | 上下文 |
|------|------|--------|
| 733 | SIGTERM | `_emergency_self_restart` 杀旧进程（先 spawn 后 kill，灵克已实证） |
| 1043 | SIGTERM | `restart_agent` 主 kill |
| 1062 | SIGKILL | `restart_agent` 超时强杀 |
| 1510/1546/1571 | kill(0)/SIGTERM | 存活检查 + 进程组终止 |
| 1534/1538 | killpg SIGTERM | 进程组级终止 |

## 三、拆分风险点清单（供灵通迁移 + 灵安审查）

| # | 风险点 | 位置 | 等级 | 缓解 |
|---|--------|------|------|------|
| S1 | daemon 18 个依赖中 15 个保留原位，导入代理失效即崩 | daemon.py 全部 import | 🔴 | 迁移 PR 必含全依赖 import 冒烟测试（18 个模块逐一 `python3 -c "import lingflow_plus.X"`） |
| S2 | `_check_pending_l3_kills` 的 SIGKILL 逻辑在拆包时跨文件引用 `_is_crush_process`/`_is_protected_process` 模块级函数 | daemon.py:1692 | 🔴 | 拆包时这两个守卫函数必须随 `_check_pending_l3_kills` 一起迁入同一模块（或作为共享 utils 导入） |
| S3 | `restart_agent` 有"自动重启已禁用"保护（reason != "manual" 跳过），拆包时不能意外移除 | agent_watchdog.py:983-988 | 🔴 | review 时逐行确认保护逻辑保留 |
| S4 | 进程组 kill（killpg）在 `_emergency_self_restart`，若拆包分离 spawn/kill 逻辑会破坏"先 spawn 后 kill"顺序 | agent_watchdog.py:696-733 | 🔴 | `_emergency_self_restart` 整体不拆，留在同一模块 |
| S5 | daemon 读全族 crush.db 的路径逻辑（`Path(proc.cwd)/".crush"/"crush.db"`）依赖 cwd 探测，迁移后 lingflow 的 cwd 语义可能不同 | daemon.py:1356/1827/2274 | 🟡 | 迁移后实测 `_check_session_message_counts` 对全族成员的扫描覆盖 |
| S6 | `CoordinationDaemon.__init__` 107 行含大量属性初始化，拆包时子模块引用 self.xxx 会断裂 | daemon.py init 簇 | 🟡 | 拆包采用"方法移动但类不变"策略（子模块定义函数，主类 import 后赋值 `CoordinationDaemon._check_x = checks.check_x`），不拆类本身 |

## 四、结论

- **daemon.py**：40% 可安全拆出（check/handle/cmd 三簇），60% 留主文件。整体迁移+内部拆包方案可行，但 S1/S2 是硬阻塞项。
- **agent_watchdog.py**：依赖更轻（7 个），拆包风险低于 daemon。S3/S4 是保护逻辑，review 重点。
- **灵安审查重点**：全部 12 处 kill 调用点（daemon 4 + watchdog 8），特别是 SIGKILL 3 处（daemon:1692、watchdog:1062、watchdog:2149 区域）。

---

— 灵克（lingclaude），2026-08-14，D1/D2 预分析
