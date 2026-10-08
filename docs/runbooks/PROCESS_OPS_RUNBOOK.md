# 进程操作铁律 Runbook

> 来源：2026-10-07 browse_agg 会话复盘 + 2026-10-08 推送门禁事故复盘。前者发生：
> `pkill -f` 自杀式自匹配 2 次、双实例抢浏览器 profile 2 次（一次险些重演
> SingletonLock 服务瘫痪）、误杀 systemd 正统实例 1 次（靠 Restart=always 兜底）。
> 后者发生：短 timeout 一轮轮掐死门禁长跑、孤儿 pytest worker 滚雪球（详见 P5/P6）。

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

## 铁律 P5：超时杀进程树，必须连子进程一起收割

git push 链是 壳 → git → hook → pytest -n 8：外层被 timeout 掐死后，
**worker 是 orphan 而不是死**——孤儿继续满载，形成「越推越慢」滚雪球
（10-08 实测：被掐两轮后 16 个 pytest worker 并存互抢 CPU）。

规程：

1. 掐超时后**必须收割进程组**：`pgrep -g <进程组>` 或按可执行名 `pgrep -x pytest`
   精确列出残留，逐 PID kill（勿用 `pkill -f pytest`，见 P2）
2. **长跑任务的第一问**：这东西正常要跑多久？（先问规模：如 tests/ 有 5591 用例）
   —— timeout 上限必须 ≥ 正常耗时，否则每次超时都在制造孤儿
3. 排查「服务异常」前先 `pgrep -x pytest / lefthook` 查孤儿积压，孤儿乱序输出
   会伪装成「多实例」「神秘占用」

## 铁律 P6：区分「慢」与「死」，观察窗口必须大于被测对象真实耗时

10-08 双重教训（与 SANDBOX_OBSERVATION_RUNBOOK 的假阴性教训同源）：

1. **lefthook「25s 不退出」误诊为钩子挂死**——真相：5591 用例全量正常要 ~26-35min，
   观察窗口短于真实耗时，把「慢」误诊为「死」，差点删掉好的门禁
2. **dushu_graph_merge「日志 16h 不动」误诊为永远跑不完**——真相：print 重定向到文件
   是全缓冲（~8KB 才落盘），日志静止≠进程静止

判别规程（三证才判死）：

```bash
py-spy dump --pid <pid>     # 栈在动吗？卡在哪？（本地有 py-spy）
ls --full-time <产物文件>   # 产物 mtime 在推进吗？
cat /proc/<pid>/status | grep -E 'State|Threads'   # R 状态=在跑
```

- 慢任务确认在推进 → **等**，别掐；先估总时长再定 timeout
- 日志缓冲假象：`python -u` / `PYTHONUNBUFFERED=1` 起服务可根治，判读前先想一层

## 快速自检清单

```
[ ] 动进程前查过 PPID 和启动时间？
[ ] pkill/pgrep 模式做了自匹配防护（[x]技巧 或精确 PID）？
[ ] 重启守护进程是否走 systemd？确认旧实例完全退场了吗？
[ ] 判定死亡前，恢复期/通道问题排除了吗？
[ ] timeout 上限 ≥ 被测对象正常耗时（先问规模）？掐完后收割过 worker？
[ ] 判「挂死」前用过 py-spy/产物 mtime 三证？日志静止想过缓冲假象吗？
```
