# INCIDENT 2026-08-07: 主机 OOM 活锁冻结 6.7 天

**状态：✅ 已关闭（2026-08-14 06:05 族长验收通过，全族通告 thread 见 LingBus）**
**关闭依据**：根因三层全查明；P0-1~5 全部落地并验证；行为规范 v1.1（A-K）三方收敛并入族规（PRO-053 投票中）；回归 1876 tests passed。

**时间线** (全部有日志实证):

| 时间 | 事件 | 证据 |
|------|------|------|
| 08-01 23:21:45 | `ai` 用户手动 `sudo swapoff -a` (PWD=/home/ai)，16GB swap 永久关闭 | auth.log.2.gz:15448 |
| 08-01 23:21:53 | swapoff 自身触发内核 OOM killer ×2（说明当时内存已满） | kern.log.2.gz |
| 08-02 00:07-07:05 | OOM 风暴：glm_asan (RSS 21GB)、glm_final ×3 (22GB)、chrome ×4、exe (9-19GB)、python3 (15GB) 被杀 | kern.log.1 |
| 08-02 06:26 / 07:03 | `ai` 两次 `sudo systemctl stop earlyoom`（earlyoom 在 SIGTERM glm 任务），此后 11 天无用户态 OOM 保护 | auth.log.1:707,775 |
| 08-03 21:00-22:03 | llama-gguf (RSS 17-18GB) 被内核 OOM 连杀 15 次，服务反复重启 | kern.log.1 |
| 08-04 12:35 | NVIDIA 驱动报系统内存分配失败 | kern.log.1 |
| 08-06 17:18 起 | llama-server Qwopus-35B-A3B (PID 3688403, ~300% CPU) 常驻；zhibridge.service 每 ~23 秒崩溃重启（计数 42743）；lingai-cuda-8104 崩溃循环（计数 7600+） | syslog.1 |
| 08-06 20:01 | journald 开始报 "Under memory pressure" | syslog.1:817367 |
| **08-07 00:53:47** | **最后一条用户态日志，此后全部服务冻结** | syslog.1:847520 |
| 08-07 ~ 08-13 | 内核存活（ICMP ping 由内核 softirq 应答），用户态完全活锁，所有端口关闭 | 日志静默 6.7 天 |
| 08-13 19:25 | 物理断电重启，swap 随 fstab 自动激活恢复 | kern.log.1:49939 |

## 根因（三层叠加）

1. **直接原因**：32GB 物理内存 + **0 swap** + 多个 10-22GB RSS 大户（llama-server 35B 模型、chrome、glm 系列）→ 内核直接回收（direct reclaim）**活锁**：页缓存尚有 17GB 可回收，内核 OOM killer 永远不触发，但所有用户态进程卡在内存分配上无法推进。内核网络栈仍应答 ping，表现为"活着但全瘫"。
2. **保护层被人为拆除**：
   - swap 缓冲：`sudo swapoff -a` (8/1) 后再未 swapon
   - earlyoom 用户态兜底：因杀 glm 任务被 `systemctl stop` (8/2)，再未启动
   - 仅剩的 memory-watchdog 每 2 分钟跑，只会 drop_caches + 重启 docker 容器，对宿主进程大户（llama/chrome）无能为力
3. **放大器**：zhibridge / lingai-cuda-8104 两个服务崩溃循环数万次无 StartLimit 熔断，持续制造进程创建开销加剧内存压力。

## 教训

- 手动 `swapoff` / `stop earlyoom` 是临时操作，无恢复机制 = 永久失效。与 CRUSH.md "静默失效检测" 教训一致：配置了但没跑 = 没有。
- "能 ping 通" ≠ 系统健康。ICMP 由内核应答，与用户态死活无关。
- journald 非持久 + cron 每日 vacuum 7d，断电后全部丢失；本次取证只能靠 rsyslog 文本日志。

## 防范措施（按可靠性优先级）

### 代码化（0 认知开销）
1. **swap 守卫**：memory-watchdog 每 2 分钟已跑，加一段：`swapon --show | grep -q . || { swapon -a; 告警; }`。swap 掉了自动恢复。
2. **earlyoom 存活守卫**：同一 watchdog 加 `systemctl is-active earlyoom || { systemctl start earlyoom; 告警; }`。
3. **崩溃循环熔断**：zhibridge.service / lingai-cuda-8104.service 加 `StartLimitBurst=10 StartLimitIntervalSec=600`，10 次/10 分钟后停手告警。
4. **大户 cgroup 限额**：llama-server 类大模型任务必须跑在带 `MemoryMax` 的 systemd unit 里（如 12-16GB），超限 cgroup OOM 只杀它自己。裸 `nohup llama-server` 应禁止。

### 配置层
5. journald 改持久化（`Storage=persistent`, `SystemMaxUse=2G`），删除/调整每日 `journalctl --vacuum-time=7d` cron。
6. 内核参数：`vm.watermark_boost_factor=15000` 缓解 direct reclaim 活锁；`kernel.sysrq=1` 保留远程急救手段。
7. earlyoom 的 avoid 正则 `(systemd|sshd|lingbus|proxy3|omniroute)` 保持不变；把 glm/llama 训练任务列入"允许被杀"白名单语义（即不入 avoid）。

### 外部监控
8. 从另一台主机监控本机服务端口（8765/8900/proxy3），端口全灭即告警——ping 通不算活。

## 当前状态（8/13 重启后已验证）

- swap 16GB 已激活（fstab 自动）
- earlyoom 已运行（19:25:41 启动，阈值 mem<=20%/swap<=10% SIGTERM）
- memory-watchdog.timer 每 2 分钟运行
- 待办：上述 1-8 项落地（等用户确认后执行）
