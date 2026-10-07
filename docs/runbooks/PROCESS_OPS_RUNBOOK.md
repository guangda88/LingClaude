# 进程操作铁律 Runbook

> 来源：2026-10-07 browse_agg 会话复盘。当晚共发生：`pkill -f` 自杀式自匹配 2 次、
> 双实例抢浏览器 profile 2 次（一次险些重演 SingletonLock 服务瘫痪）、误杀 systemd
> 正统实例 1 次（依赖 Restart=always 兜底才没出事）。本文是防复发锁。

## 铁律 P1：杀进程只准 `kill <精确PID>`，动进程前先查 PPID 归属

**先鉴定，后动手**——目标进程的三问：

```bash
ps -o pid,ppid,lstart,cmd -p <pid>   # 谁生的？什么时候生的？跑的是什么？
```

- PPID=1（systemd）且属某 service → 是 systemd 正统实例，**不要 kill -9**，
  优雅路径是 `systemctl restart <name>`；确需直杀用 SIGTERM，让 systemd 重生
-  PPID=服务主进程 → 子进程/worker，随主进程走，一般不用单独动
- 手滑事故还原：曾把 systemd 刚重生的正统实例与上轮被 SIGKILL 连坐的孤儿
  搞反，误杀正统 → 靠 `Restart=always` 兜底。**鉴定错一次的成本可能是一次服务中断。**

## 铁律 P2：`pkill/pgrep -f` 必须防自匹配

`-f` 匹配**完整命令行**——后台作业自身的命令行里若含同样字符串（
`curl .../login/browser_agg`、`bash -c "pkill -f chrome"`），会被自己的探测/清理命令杀死。

标准姿势（方括号技巧）：

```bash
pkill -f '[c]hrome'        # 正则 [c]hrome 匹配 chrome 字面量，
                            # 但作业自身命令行里是 [c]hrome 字面文本，不命中自身
```

更稳的替代：优先按端口/pid 文件/注册中心定位真身 PID，再精确 kill。

```bash
ss -tlnp | grep 13461                    # 从监听 socket 找真身
cat /tmp/<app>.pid 2>/dev/null           # 应用自写的 pid 文件
```

## 铁律 P3：重启守护进程，一次只允许一个实例

Playwright/Chrome 系应用有 **profile 单占锁**（SingletonLock）——双实例并发
= 后启动的直接崩溃（当日实测 `ProcessSingleton: 文件已存在`）。

规程：

1. 重启前确认旧进程**完全退场**（`pgrep` + 端口释放双确认），不要凭 TERM 发出就假设死了
   （当日旧实例优雅退出卡了 5 分钟才被 systemd 记 FAILURE）
2. systemd 服务**永远走 systemd 重启**，不要手动拉起副本「救急」——手动实例
   会与 systemd 正统抢 profile/端口（当日 2 次双实例皆因手动救援脚本）
3. 判定「服务挂了」前先区分：**启动恢复期**（会话逐个恢复需 ~200s，端口未开属正常）
   vs **真死**（进程消失+端口无人听+日志静止三证齐）。当日探针全空实为恢复期误判

## 铁律 P4：探测命令的「空结果」要先自证通道，再下结论

当日 8 个探针全空 → 误报 FATAL。真相：探针跑在沙箱内（unshare-net）+ 服务在恢复期。
**空结果的三种成因要逐一排除：通道不通 / 时序未到 / 真的空。**
自证方法：同命令跑一份已知应有结果的对照（如 `curl 127.0.0.1:22` 是否至少 refused）。

## 快速自检清单

```
[ ] 动进程前查过 PPID 和启动时间？
[ ] pkill/pgrep 模式做了自匹配防护（[x]技巧 或精确 PID）？
[ ] 重启守护进程是否走 systemd？确认旧实例完全退场了吗？
[ ] 判定死亡前，恢复期/通道问题排除了吗？
```
