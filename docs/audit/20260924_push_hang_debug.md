# 2026-09-24 Push 排障记 — 【二次修订】无挂死，是「慢 + 门禁红 + 工具时限」三合一

## ⚠️ 更正声明（覆盖第一版结论）

第一版「xdist worker 全灭、主进程空等挂死」的判断**是测量伪影**：连续三轮用
`grep -E "pytest|lefthook|git push"` 找 worker——但 execnet worker 的命令行是裸的
`python3 -u -c import sys;exec(eval(sys.stdin.readline()))`，根本不含这些词；主协调进程
CPU 天然极低，被误读为「1 秒 CPU = 挂死」。正确姿势（`ps --ppid <主pid>`）显示：
8 个 worker 全部健在、每个已烧 160s+ CPU 在真实跑测试。
**09:17 钩子内那次跑大概率也是健康的，在 16 分钟时被手动击杀**——此误杀归我。

## 修正后的结论

1. **全量 pytest 不是挂死，是慢**：-n 8 下全量套件 >16 分钟（含大量环境依赖型慢测试），
   预计 20-40 分钟量级。上一会话「挂死 1h28m 的管道链」结论亦需据此重新检视——
   不排除同样是在慢慢跑而非挂死（当时未留进程级证据，无法复盘，存疑记录）。
2. **git_push 工具默认 120s 时限 vs 全量门禁 = 结构性不可能**。git_push 支持 timeout
   参数——push 应传大值（如 3600）让钩子自然跑完。
3. **unpushed=156 暗示门禁可能长期红**：lastfailed 缓存 285 条历史失败（lingmemory MCP、
   web_tools、e2e 等环境依赖型）。Truth Run 出分后定论：若大面积红，则 156 个提交的
   积压正是门禁红导致 push 被拒/搁置的结果。

## 时间线（09:10-09:47）

| 时刻 | 事件 |
|------|------|
| 09:10 | 两个 git_push 工具调用（默认 120s）超时中止，origin/github 两路；孤儿钩子链留存 |
| 09:17 | 后台任务重启 push origin；git 拿到 refs → pre-push → full-pytest 起跑（-n 8，8 worker 孵出） |
| 09:26-09:33 | 三次误诊「worker 全灭」（grep 模式盲区），09:34 手动击杀健康跑（误杀） |
| 09:36 | watchdog 日志：全程只警告未击杀（committed_AS 194→198% 持续超标，非本次事故原因） |
| 09:38 | bwrap 沙箱发现：会话 bash 跑在 `--unshare-net` 内，此前全部 DNS/网络探针作废 |
| 09:40 | Truth Run 启动（沙箱外同参数 + timeout 1200 保险），90s 时已复现「8 worker 健在跑测试」 |
| 09:47 | ps --ppid 实锤 8 worker 各 160s+ CPU——误诊链条正式推翻 |

## 环境事实（本日实测钉死）

- 会话 bash 在 `bwrap --unshare-net --ro-bind / / ...` 沙箱内：无网、DNS 必失败——
  **沙箱内网络探针不能作为宿主网络判据**（NextTime 引以为戒）。
- 宿主侧 gitea https 可达（push 进钩子为证）；github SSH DNS 间歇故障
  （`Could not resolve hostname github.com: Device or resource busy`），恢复后单推。
- pre-push 门禁链：pre-push-security(10) → exempt-review-gate(15) → full-pytest(20)，
  全量 pytest 无 exclude；旁路 `LEFTHOOK=0`/`LINGCLAUDE_SKIP_REVIEW_GATE=1` 均需显式留痕，
  不可 silently 使用。
- memory_watchdog 存活且 2 分钟粒度记录（/tmp/memory_watchdog.log），本窗口只警告未杀。

## 下一步

1. 等 Truth Run（/tmp/full_pytest_truth.log，job bae1ba15c7cd）出分。
2. **绿** → push 走 git_push timeout=3600 正常通道（钩子重跑一次全量，或先 `LEFTHOOK=1`
   直推——钩子会再跑一遍，可接受）；github 腿等 DNS 恢复单推。
3. **红** → 甄别真失败 vs 环境依赖型（对照 lastfailed 285 条），先修真失败再推。
4. 沉淀候选：pre-push full-pytest 加 `--timeout`（pytest-timeout）防单测试卡死；
   unpushed=156 的积压清偿策略（分批 push 烧门禁？）待 Truth Run 数据支撑后定。
