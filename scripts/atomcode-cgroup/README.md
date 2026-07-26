# atomcode L6 验证 内存硬隔离（需 root 执行）

> 背景：2026-07-26 观测到灵元推理栈 L6 验证作业造成 ~19G RSS / Committed_AS 198% 瞬时脉冲。
> 灵克已用 prlimit 对现有 atomcode 进程（daemon 2216664 + 3 个交互会话）设 RLIMIT_AS=22G 作为临时近似，
> 但**新启动的 atomcode 会话不受保护**，且 rlimit 按虚拟地址空间计（含 mmap），语义与 cgroup 不同。
> 以下为正式的 cgroup MemoryMax 方案，需 root 执行一次。

## 教训：prlimit 陷阱（2026-07-26 灵克事故）

1. **prlimit 不识别 `G` 后缀**，`--as=22G:22G` 会被解析为 **22 字节**。必须用纯字节数：`--as=23622320128:23622320128`
2. **hard limit 只能降不能升**（非 root）：误设后无法用同 UID 的 prlimit 恢复，必须 `sudo prlimit --pid <pid> --as=unlimited:unlimited`
3. 验证 prlimit 结果时必须看 UNITS 列（本机会本地化显示"字节"），不能只看数字
4. 结论：资源限制首选 cgroup（systemd set-property），rlimit 仅作无 root 时的临时手段且必须字节数+立即验证

## 方案 A：运行时即时生效（不重启 daemon，推荐）

```bash
sudo systemctl set-property atomcode-daemon.service MemoryMax=22G MemoryHigh=18G
```

- MemoryMax=22G：硬顶，触发则 cgroup OOM kill 作业进程，不影响系统其他部分
- MemoryHigh=18G：软顶，先到此处开始节流/回收，给作业缓冲
- --runtime 性质：立即对运行中的 daemon 生效，同时写入 drop-in 持久化

## 方案 B：drop-in 文件（重启后持久）

```bash
sudo mkdir -p /etc/systemd/system/atomcode-daemon.service.d
sudo tee /etc/systemd/system/atomcode-daemon.service.d/memory.conf <<'EOF'
[Service]
MemoryMax=22G
MemoryHigh=18G
EOF
sudo systemctl daemon-reload
# 不需重启 daemon；方案 A 已即时生效时此步仅作持久化
```

## 交互式 atomcode 会话（pts 手动启动的）

systemd 管不到手动启动的会话。建议以后用 wrapper 启动：

```bash
# /home/ai/.local/bin/atomcode-capped （已创建）
#!/bin/bash
# 在 22G 内存限额内启动 atomcode
exec systemd-run --user --scope -p MemoryMax=22G -p MemoryHigh=18G atomcode "$@"
```

## 验证

```bash
systemctl show atomcode-daemon.service -p MemoryMax -p MemoryHigh
cat /sys/fs/cgroup/system.slice/atomcode-daemon.service/memory.max
```

—— 灵克 · 2026-07-26
