# 自优化 daemon watch 服务运维手册（P1-卫版）

> 2026-10-17 随三条卫生规则（commit `8de22e5`）落地。取代首航报告中的
> 「watch 服务重启决策项」——本手册生效后服务已恢复值守。

## 服务现状

- 单元：`~/.config/systemd/user/lingclaude-daemon-watch.service`（源=仓库
  `deploy/systemd/user/lingclaude-daemon-watch.service`，改动须两边同步）
- 节奏：每 300s 一周期；`Restart=on-failure` + 5min 内崩 3 次熔断重拉
- 守卫：应用层三规则（详见 `tests/test_rsi_hygiene_rules.py` 头注）
  - Rule A：owner 文件（`daemon_state.json.owner`）登记写者身份，存活
    他者持有磁盘时 save 拒写；他人直改盘后陈旧内存拒写
  - Rule B：flock 锁 + 原子写（tmp+os.replace）
  - Rule C：启动一致性校验（活 daemon 拒二启/坏状态文件拒启）+
    watch 每周期前从盘重载（人工 reset/rollback 对长驻进程可见）

## 日常操作

```bash
systemctl --user status lingclaude-daemon-watch    # 看状态
journalctl --user -u lingclaude-daemon-watch -f    # 跟日志
# 人工改状态前先停服务（Rule C 会让 CLI 互斥，但改盘文件绕过应用层）：
systemctl --user stop lingclaude-daemon-watch
# 改完重启即可，新进程会从盘接管：
systemctl --user start lingclaude-daemon-watch
```

## 验证单写者守卫（健康自检）

```bash
# 服务运行时，第二个 daemon 应 0.5s 内被拒启：
lingclaude daemon watch --interval 999   # 预期：立即退出
python3 -c "from lingclaude.self_optimizer.daemon import OptimizationDaemon; \
print(OptimizationDaemon().check_startup_consistency())"  # 预期：False + ERROR 日志点名 pid
```

## 首航事故档案（为何有这三条规则）

10-17 首航实证：10-12 启动的旧 watch 进程（旧代码+史前内存态）每 5 分钟
把内存写回磁盘，踩掉当日手术解锁与新挂单（frozen_skipped 一路涨到 6300
即铁证）。三规则从「写前验权 / 写时互斥 / 启动与周期校验」三面封死该事故
类。旧 watch 复活后带新代码跑通的 1131-1134（含观察期自动回滚×2）是
守卫逻辑最早的跨进程实战验证。
