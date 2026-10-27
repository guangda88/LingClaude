# PUSH SOP —— push/网络型反复踩坑终结手册（v1，2026-09-24）

> 来源事故：09-11/09-12 push 失败 ×2（`docs/audit/PUSH_FAILURE_20260911.md`、
> `PUSH_BLOCKED_SESSION_ENV_20260912.md`）+ 09-24 三次 push 事故
> （`docs/audit/20260924_push_hang_debug.md`：3600s 杀壳→孤儿管道死、沙箱网络伪影、
> 误杀健康跑、陈旧跟踪引用误判积压）。
> 配套脚本：`scripts/safe_push.sh`（把本手册的默认值与禁止项编码进执行路径）。

## 零、五条铁律（违反任何一条即复踩）

1. **沙箱内无网络话语权**：会话 bash 运行在 `bwrap --unshare-net` 且 `ulimit -v=1GB`。
   DNS / 连通性 / 带宽 / 内存容量判据一律无效；网络与重负载操作走主进程
   （`git_push` 工具 / 宿主 shell）。沙箱内跑 `pytest -n8` 的"复现"不作为门禁缺陷证据。
   ⚠ 09-24 首航教训：**共享宿主网卡 ≠ 共享 DNS**——网卡检查通过的沙箱仍会死于
   `Could not resolve host`。传输类操作前必须过 `getent hosts <远端主机>` 实测
   （已固化进 `safe_push.sh` L1b 拒跑线；ls-remote 不可达时 L6b 降级用 reflog 自写记录取证）。
2. **timeout 必须显式给足**：`git_push` 工具默认 120s，全量门禁 20-40min——
   push 一律 `timeout >= 7200`。僵尸 push 第一死因就是默认值杀壳留下孤儿钩子链。
3. **输出直写文件，不走管道**：统一 `/tmp/push_<remote>_<时间戳>.log`。
   管道读端死亡 + 64KB 缓冲灌满 = 门禁 pytest 阻塞在 `write()` 假死（实测 2h+ 零进展）。
4. **判定 worker 健康用 `ps --ppid <主pid>`**：execnet worker 命令行是裸
   `python3 -u -c ...`，`ps -ef | grep pytest` 永远匹配不到；主协调进程 CPU 天然极低，
   不代表挂死。09-24 曾据此误杀健康跑。
5. **远端状态以 `git ls-remote` 实测为准**：本地跟踪引用会陈旧
   （"gitea: 162 积压"实为假象）；origin 与 gitea 同 URL，推 origin 即清两名。

## 一、通道拓扑（2026-09-24 实测）

| 通道 | URL | 传输 | 依赖与注意 |
|------|-----|------|-----------|
| origin / gitea | `https://zhinenggitea.iepose.cn/guangda/LingClaude.git` | https | 宿主网络直连可达（钩子阶段多次走到为证）|
| github | `git@github.com:guangda88/LingClaude.git` | ssh | **仓库级 `core.sshCommand` 走 clash**：`socat - PROXY:127.0.0.1:%h:%p,proxyport=7890` |

**github 通道新依赖警示**：代理配置后 github push 依赖 clash(7890) 存活。
clash 不在时的直连兜底（仅当宿主 DNS 恢复时可用，DNS 间歇故障是环境波动而非常态）：

```sh
git -c core.sshCommand="ssh -o StrictHostKeyChecking=accept-new" push github master
```

## 二、标准流程

```
0. git_push_preflight（工具）→ 未推清单 / dirty / gate 状态
   ※ 门禁 v2.0（10-28）已有已知失败基线豁免（§三.5）：「历史失败挡门」不再
     构成旁路理由——先后台跑一次 GATE_NO_CACHE=1 全量门禁，豁免即落缓存。
1. origin 腿：
   a. 门禁健康 且 MemAvailable >= 4G  → 钩子开启 + timeout 7200 正常推
   b. 门禁本体缺陷 / 超时环境病 / 资源紧张 → §三 旁路纪律
2. github 腿：单独推；失败先查 clash 7890 存活，再查宿主 DNS，勿在沙箱内诊断
3. 每次推送后：git ls-remote 比对 本地 HEAD == 远端头，回填台账清债记录
```

## 三、门禁旁路纪律（必须留痕，不得裸奔）

- **前提**：轻门禁手动补跑留证——pre-push-security + exempt-review-gate 双绿
- **步骤**：`chmod -x .git/hooks/pre-push` → `git_push`（timeout≥7200）→
  **立即** `chmod +x` 并验证 `-rwxr-xr-x`（旁路窗口控制在分钟级）
- **记录**：旁路原因 + 轻门禁证据 + 恢复确认，写进当次提交说明/台账
- **现状（2026-09-24）**：`push-gate-xdist-spin` 已改判（真根因=宿主资源峰值 +
  取证沙箱 1GB 污染，非门禁本体缺陷）；门禁补一套"资源预检 + 大 timeout"缓解并
  回归之前，正式 push 按本节执行。

## 三.5、已知失败基线豁免（2026-10-28 门禁 v2.0，本节为机制权威说明）

**背景**：10-09 后 HEAD 积累历史测试债（10-27 W3 甄别闭环：16 条确定性红，全部
HEAD worktree 实测同挂），v1 门禁「全绿才放行+缓存按 HEAD 键」结构性必红 →
缓存从未绿过 → push 被挡 18 天，10-27 被迫走 §三 旁路（摘钩子推 71 提交）。
v2.0 把「已甄别、有诊断的历史失败」机制化为门禁内豁免，旁路降为极端兜底。

**机制**（四件套）：
- 判定器 `scripts/gate_exemption_check.py`：RC=1 时以 pytest cacheprovider
  `lastfailed`（门禁专属 `--cache-dir=.audit/pytest_gate_cache`，与开发态隔离）
  与基线比对。退出码：0=子集豁免放行 / 1=新红拒绝 / 2=数据异常回退严格 /
  3=RC=1 无失败记录（疑收集错误）不可豁免。test_id 精确全等，无模糊匹配。
- 基线 `data/arch_ledger/test_baselines/push_gate_known_failures.json`：
  **入库跟踪**（worktree/其他会话同视），每条必带 reason（含实测断言行）+
  verified_head。**棘轮只缩不放**：修好一条必须删一条；新红不得无诊断入册。
- 门禁 `scripts/pre_push_gate.sh` v2.0：仅 RC=1 参与豁免；**124/137（预算超时
  被杀）不豁免**（超时=环境病，无失败集可判）。豁免放行按 HEAD 缓存
  （exempted=true），同 HEAD 第二远程秒过（双远程不再跑两次全量）。
  `GATE_BASELINE=""` 可显式禁用豁免（回 v1 严格语义，含拒绝豁免缓存复用）。
- 单测 `tests/test_gate_exemption_check.py`（9 用例：子集/新红/数据异常/
  精确匹配/入库基线健全性）。

**基线维护纪律**：
1. 修复基线内任一失败 → 同 PR 删除对应 entry（只缩不放）；
2. 出现新红 → 先修复或诊断后带 reason+verified_head 入册，禁止"跑不绿就塞基线"；
3. 每条 entry 的 verified_head 记录实测 HEAD，批量大清理时刷新 verified_method；
4. 基线归零（entries=[]）是理想态——届时 v2.0 自动退化为 v1 行为。

**与 §三 的关系**：本节生效后，§三 旁路仅适用于：门禁本体缺陷 / 超时环境病
（预算内跑不完）/ 数据面异常三类；「历史失败挡门」不再构成旁路理由。

## 五、git_push 工具已知缺陷（2026-09-24 实测补注）

- **timeout 参数不生效**：底层硬顶 120s，传 ≥7200 也会被提前杀壳（铁律 2 在
  工具通道上失效）→ 缓解：费时 push 改走宿主 shell 直推；工具通道仅用于
  轻量 push（全量门禁必超时）。超时后**进程树不清理**（孤儿钩子链存活），
  推后必查：`pgrep -f pre-push` / `pgrep -f pytest`，有残留立即清树。
- **实锤案例**：09-24 晚 L2 立项单推送，timeout=2400/7200 均于 120s 触发
  timeout+ok 双报，孤儿树带假红全量 pytest 运行，手动清树后按 §三 旁路完成。

## 六、事故历史 → 条款映射

| 日期 | 事故 | 对应条款 |
|------|------|---------|
| 09-11/09-12 | push 连续失败，会话环境误判 | 铁律 1 |
| 09-24 上午 | git_push 默认 120s 杀壳 → 孤儿钩子链 | 铁律 2 |
| 09-24 中午 | 孤儿 push 管道读端死亡，pytest 阻塞 write() 假死 2h+ | 铁律 3 |
| 09-24 上午 | grep 找不到 worker → 误判全灭 → 手动击杀健康跑 | 铁律 4 |
| 09-24 下午 | 陈旧跟踪引用 → 误判 github 积压 161 条 | 铁律 5 |
| 10-07/10-08 | 推送轮番超时被误诊"lefthook 不退出 bug"；实为 full-pytest（5591 用例，实测 ~26-35min）被各通道 90~880s timeout 反复掐死，孤儿 worker 越积越多越推越慢 | §七 |

## 七、门禁耗时真相与网关（2026-10-08 复盘，推翻 10-08 晨"钩子不退出"结论）

**教训**：观察窗口短于被测对象真实耗时 → 把「慢」误诊为「死」。
`.git/hooks/pre-push </dev/null` 25s 不退出的真相是 full-pytest 正在长跑
（5591 tests × 8 workers，机器有 3 天+ 的 101% CPU 邻居进程时更慢），不是挂死。

| 铁证 | 说明 |
|------|------|
| 快门三命令实测 0.007~0.088s 全部秒退 | 钩子链本体无任何挂死 |
| 全量实测 07:39 起 26min+，正常出进度条 | 门禁在干活，不是死锁 |
| 被掐轮次的 16 个 pytest worker 滞留互抢 CPU（各 37%） | "越推越慢"恶性循环的机制 |

**结构性矛盾**：lefthook.yml 的前置闸注释 09-26 就写着"避免白跑 **30 分钟**
full-pytest"，而 push_double_remote.sh 给 push 只留 `timeout 90`、
git_push 工具默认 120s——设计上必然互相绞杀（对应铁律 2）。

**修复（当日落地）**：full-pytest 改经 `scripts/pre_push_gate.sh` 网关，三性质：
1. **总预算自收割**：默认 `GATE_TIMEOUT_BUDGET=2700s`（26min 基线 ×1.7），
   SIGTERM→30s 后 SIGKILL，孤儿不再外溢到钩子链外；
2. **flock 串行**（`.audit/full_pytest_gate.json.lock`）：多路 push/探针排队，
   不再 8+8 worker 互杀；
3. **HEAD 级结果复用**：绿色结果缓存于 `.audit/full_pytest_gate.json`（键=HEAD8），
   同一 HEAD 二次推送（含第二个远端）秒过——"先跑门禁后推送"工作流因此成立。
   `GATE_NO_CACHE=1` 强制重跑。
配套：push_double_remote.sh 每远端 timeout 90→2700；push 前跑一次
`GATE_NO_CACHE=1 bash scripts/pre_push_gate.sh`（后台、给足窗口）是
高负载时段的推荐工作流。
