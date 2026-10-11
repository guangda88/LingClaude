# R9 立项：无人值守接力闭环 —— 编排器 systemd 化 + 独立验收闸

> 立项日期: 2026-10-09　| 状态: **IN_PROGRESS（2026-10-10 用户批准，三闸已落地）**
> 落地进度: R9-5 ✅ / R9-3 ✅ / R9-4 ✅（E2E 6/6 stub 实测，见下）/ R9-1 ✅ 宿主装配完成+AC 实测通过 / **R9-2 ✅ 引擎级落地（2026-10-10 19:3x，kill_beacon 三层机制，单测 11/11 + AC 端到端实测）**
> 背景: 10-09 实测确认单巨任务接力已可战（LONGTASK_SOP + session_relay_orchestrator.sh 209 行 + 10-08 实战锁文件），
> 但彻底无人值守缺五块板。本立项拆解为 R9-1..R9-5，每块带验收标准（AC）。
> 方法论锚点: H17 教训（自报≠完成）的同构推广 —— **能力必须有代码锚点，完成必须过独立验收**。

## 五块板拆解

### R9-1 编排器 systemd 化（托管）
- 现状: 无常驻进程，宿主重启即忘，靠手动启动。
- 动作: `ling-relay-orchestrator.service`（Restart=on-failure, After=network.target）；
  队列目录固定 `~/.lingclaude/relay_queue/`；日志 journald + 文件双写。
- AC: 宿主重启后 `systemctl status` 显示 active；dry-run 模式可独立起停；kill -9 后 10s 内自动拉起。

### R9-2 会话终止前强制 flush（引擎层）
- 现状: 预停协议靠模型守纪律（快挂时自觉写盘），无引擎级保证。
- 动作: 引擎在 SIGTERM/超时熔断路径上注入强制钩子：先 flush todo 状态 + handover 文件再退出
  （复用 Handover V2 kill-safe 即写基础，把"自觉"变"强制"）。
- AC: `kill -TERM` 一个进行中会话，重启发现在 handover 文件里留下 ≤10s 前的状态快照；回归测试覆盖。

### R9-3 目标漂移检测（每 N 跳独立复核）
- 现状: 接力 N 跳后无人检查"还在干最初那件事吗"。
- 动作: 每完成 K 跳（建议 K=3），编排器自动起 sub_agent 只读复核：
  对比任务原始目标 vs 当前 handover 产物清单，输出 drift/ok 判定；drift 即暂停队列等决策。
- AC: 注入一个人为偏航的第 4 跳 handover，复核 agent 能判 drift 并阻断接力。

### R9-4 成本总闸（跨会话累计）
- 现状: 预算预警是会话内的，跨会话累计无总闸；3 次重试只是单任务次数闸。
- 动作: 编排器维护 per-任务累计 token/费用台账，超阈值（可配）→ 转 blocked_on_human，不自动接力。
- AC: 阈值调到极小值实测：第 2 跳即触发 blocked + 灵信告警，队列不再拉新会话。

### R9-5 独立验收闸（出队前置条件）
- 现状: relay 会话自报 completed 即出队 —— 自报≠真完成（H17 同构问题）。
- 动作: completed 出队前必须过独立验收：sub_agent 对照 AC 清单核产物（文件存在性 + 可复跑冒烟），
  通过才出队归档；不过则转 blocked 并附验收差异报告。
- AC: 构造一个自报 completed 但产物缺失的接力链，验收闸拦截且差异报告列明缺失项。

## 依赖与顺序
R9-5 与 R9-1 先行（改变出队与托管方式，是其余各板的宿主）；R9-3/R9-4 并行；R9-2 依赖引擎信号路径，可独立推进。

## 明确不做（边界）
- 不做跨任务智能调度（只做单任务接力闭环）；
- blocked_on_human 必停原则不变：扫码/充值/sudo 类一律停下等人，任何闸都不自动代替人决策。

## 落地记录（2026-10-10）

| 板 | 状态 | 锚点 | 实测证据 |
|---|---|---|---|
| R9-5 验收闸 | ✅ | orchestrator `run_acceptance_gate`（completed 出队前置；无 AC 即拒；崩溃兜底差异报告） | E2E 场景1/2/3 |
| R9-3 漂移复核 | ✅ | `run_drift_check`（每 K 跳只读复核 TASK_GOAL.md，drift 即 blocked+灵信） | E2E 场景4 |
| R9-4 成本总闸 | ✅ | `cost_accumulate/cost_exceeded`（per-task 台账随 state_dir 走，超限不再拉会话） | E2E 场景5 |
| R9-1 systemd | ✅ 装配完成 | unit enabled + linger 在位；daemon 空转扫描循环正常 | **AC 实测通过**：kill -9 后 01:08:13 killed→01:08:23 restarted（10s），systemctl is-active=active；journalctl+文件双写日志均验证 |
| R9-2 强制 flush | ✅ 引擎级落地 | `lingclaude/engine/kill_beacon.py` 三层：周期信标(≤10s 原子写) + SIGTERM/SIGHUP 快刷(async-signal-safe: ftruncate+pwrite，预开 fd) + 继承发现(死 pid 扫描→继承提示)；正常退出清 beacon(黑匣子语义)；`LINGCLAUDE_KILL_BEACON=0` 旁路 | **单测 11/11** + AC 端到端：子进程装信标→SIGTERM→快照年龄 **0.9s≤10s**、pid 判死、继承提示可出；接线点 app.py:301(启动)/repl.py:1816(正常退出) |

E2E 测试: `scripts/stub_lingclaude.sh`（自适应角色 stub）+ `/tmp/r9_e2e.sh` 6 场景 **6/6 通过**；
过程中修复真 bug×2（awk BEGIN 块导致台账不累计/超限不触发；漂移检查错位一跳）+ 缺口×1（验收官崩溃无差异报告）。
回归: 缺 RESUME_HINT 拒绝盲接力原防线保持（双 blocked）✓

## 验收闸设计原则（对齐灵研修复教训）
所有"优化/接力/自检"类产出的完成声明，一律要求可独立复核的证据链（重放/产物/日志），
自报完成只记 `unverified` 状态 —— 本规则同样适用于自优化闭环（见同日闭环修复清单）。
- [2026-10-10 01:0x] R9-1 装配首跑排障: ①install 守卫误报修复——user manager 实为 degraded(100+ unit 宿主常态)被旧守卫拒收, 现 degraded 放行+自诊断指引; ②真 bug: ExecStart 管道无 pipefail, 脚本被 kill -9 后退出码取自 tee(0), on-failure 永不重启(AC 静默失效), 已补 set -o pipefail; ③linger 已由用户开启并实测 /var/lib/systemd/linger/ai 在位


---

## 实战首跑记录（2026-10-10 16:04–16:33, E2E 真实验收）

任务 relay_test_20261010：A 会话封存材料+写四件协议文件+入队 → 编排器拉全新 B 会话
（lingclaude run --print）→ 读现场/交付 ANSWER.md/自报 completed → R9-3 漂移复核 →
R9-5 独立验收官 accept（5/5 标准）→ 16:33:24 出队。全链无人干预。

| # | 发现 | 处置 |
|---|---|---|
| 1 | **编排器 PATH 缺 ~/.local/bin**（非交互环境拉起），timeout 找不到 lingclaude，两轮 rc=127 | +bin 路径容错 resolve_bin()（三 bin 统一治）+ 头部 export PATH |
| 2 | **lingmessage 告警静默失效**（同 PATH 根因，#1 连带伤） | PATH 修复覆盖 |
| 3 | **log() 前置调用**（bin 解析 log 写在函数定义前）报「未找到命令」 | 改为内联 printf |
| 4 | **daemon 生在无网 netns**（由沙箱内 bash 调用拉起），拉出的接力会话 ok:false 空 content，且错误被 _headless_turn 吞掉只留 0 返回——任务卡 running 死锁 | daemon 改由 run_in_background（主 netns）托管；netns 判据=health_inspect.py:107 bwrap --unshare-net |
| 5 | R9-3 漂移复核官空裁决「drift/reviewer did not produce verdict」（16:10, PATH 127 时期产物） | 已随 #1 根治；本任务走 allowlist=0 默认路径不受此闸影响 |
| 6 | 可观测改进项（未改）：编排器 stdout 不落日志文件的时段难追查；flock「另一实例」竞态让正确 daemon 退出留错 daemon 值守 | 建议下版：--daemon 主循环每轮 log 心跳+启动时写 pidfile，持锁失败先验 pid 存活 |

**结论：R9 协议三闸+出队机制实战验证通过；R9-2（引擎级强制快照）仍为待办专项；
教训：编排器类长驻进程必须在主 netns（白名单通道）拉起，禁止沙箱内 bash 直接拉起。**

## 实战二跑（2026-10-10 16:49，可重复性验证）

一跑修复后的复跑实验：新任务 `relay_test2_20261010` 入队（材料包数值型任务，B 会话必须读文件才能答对），**零干预零修复**：

| 时刻 | 事件 |
|---|---|
| 16:49:02 | 入队 waiting_relay |
| ~16:50 | daemon 首个扫描周期拉起 B 会话；B 读现场→交付 ANSWER.md（8300/上海2487/RELAY_OK）→翻 completed |
| 16:50–16:52 | R9-5 独立验收官 5/5 AC 逐条实测证据（ls/grep/od/程序化复算/数字集合比对）→ VERDICT=accept |
| 16:53 | 队列 completed 出队，attempts=1 |

**结论**：一跑的 4 项修复（resolve_bin PATH / netns 主网托管 / log 内联 / 吞错路径）实跑生效；「入队→接力→验收→出队」可重复，全程 4 分钟。漂移复核（R9-3）按设计每 3 跳一次，第 1 跳无 drift_run.log 属正常。验收官自曝其冒烟脚本运算符优先级 bug 并修正留痕（非产物缺陷）。
