# Crush 崩溃调查归档 (2026-05-06)

> 调查人：灵克 (lingclaude)
> 状态：**已结案** — 根因确认，防护措施已部署/委托

## 根因

ZAI Proxy (port 8765) 因 systemd 重启风暴（15,500+ 次）触发重启，活跃连接被强制断开，
Crush (Go binary) 未处理 connection reset → panic → `exit_group(1)`。

**关键证据**：
- `/tmp/crush_trace.log` (3.5MB strace) — `exit_group(1)` + SIGKILL
- ZAI Proxy 日志 — 07:00:27 旧进程被杀，新进程内部状态损坏 (`I/O operation on closed file`)
- `~/.local/share/crush/crush.db` — 0 bytes (空/损坏)
- `dmesg` / `journalctl` — 无内核级异常
- `memory_watchdog.log` — 19GB free / 32GB，排除 OOM

## 已完成的防护

| 层 | 措施 | 负责人 | 状态 |
|---|------|--------|------|
| P0 | systemd 服务禁用（unit renamed `.inactive`）| 灵通+ | ✅ |
| P0 | ZAI Proxy 优雅关闭（SIGTERM + 30s drain + SSE clean termination）| 灵通+ | ✅ |
| P0 | Crush wrapper 脚本（指数退避 + 最大重启 + stderr 捕获）| 灵克 | ✅ 已写磁盘 |
| P1 | daemon.py 指数退避 | 灵通+ | 待实现 |
| P1 | `/v1/models` 健康检查 | 灵通+ | 待实现 |
| P2 | 5层硬化方案（9/12 投票支持）| 全族 | 待实现 |

## 待上游修复

Crush panic on network disconnect — 需向 CharmBracelet 提 issue。Go Bubble Tea TUI 的
alternate screen buffer 会吞掉 panic stacktrace，导致崩溃后无法追溯。

## LingBus 关联线程

- `703e93f7249a45408a59ab0b435d9943` — 灵族基础设施可靠性工程方案（主讨论）
- `c459a658826c4b4d8f2f831ef544fbca` — 5层硬化方案投票
- `b3a747bd18c54d20aad5a8f721f26ff0` — LLM proxy silent degradation 事件报告

## 调查过程教训

1. **证据在对端** — 分布式系统中，客户端日志可能不包含根因。排查时需同时检查服务端。
2. **TUI 吞 stacktrace** — Bubble Tea alternate screen buffer 使 panic 输出不可恢复。
3. **systemd 重启风暴** — 默认 Restart=always 配合短崩溃间隔可产生数千次重启，每次都中断活跃连接。
