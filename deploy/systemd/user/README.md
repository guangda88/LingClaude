# lingclaude systemd 用户单元

无需触碰系统服务数据库的用户级部署单元（`systemd --user`）。

## 单元清单（P4.3 D2 债收口）

| 单元 | 用途 | 入口 |
|------|------|------|
| `lingclaude-bus-poll.service` | LingBus 轮询快照（5min → /var/tmp） | `scripts/lingbus_poll_daemon.py` |
| `lingclaude-daemon-watch.service` | 自优化 daemon 看护（300s 循环） | `lingclaude daemon watch --interval 300` |
| `lingclaude-token-monitor.service` | token 遥测本地服务（13470 端口） | `token_monitor_service.py` |
| `lingclaude-refactor-ledger.service` + `.timer` | P5 重构指标入册（10min 周期） | `scripts/refactor_metrics_daemon.py` |

## 安装

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/user/lingclaude-*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now lingclaude-daemon-watch.service
systemctl --user status lingclaude-daemon-watch.service --no-pager
```

## 设计约定

- `Restart=always` + `RestartSec=30s`：避免历史"即时重启风暴"模式。
- `StartLimitBurst=5` / `StartLimitIntervalSec=300`：5 分钟内最多 5 次重启。
- `OOMScoreAdjust=-500`：刻意保守（配合 N6 RSS 看门狗在应用层先告警）。
- `Nice=10`：后台服务不与交互会话抢 CPU。
