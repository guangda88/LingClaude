# 网络超时排查 Runbook（v1，2026-10-06）

> 来源：2026-10-06 案例「ssh ai@100.66.1.8 Connection timed out」四 agent（cc/codex/atomcode/opencode）对比复盘。
> lc 本轮答错（凭 IP 段猜 Tailscale、拿 TeamViewer 旁证当主证据、时间点全错），正确答案由三家一致收敛得出。
> 本 runbook 把四家的正确动作固化为标准流程。

## 一、标准排查流程（6 步）

### 第 0 步：声明视角，先定位自己
- 排查网络问题**第一条命令**永远是确认"我在哪"：
  ```bash
  hostname -I; ip addr 2>/dev/null | grep -E '^[0-9]+:'; ip route; ls /proc/1/ns/
  ```
- 只有 `lo` / `hostname -I` 为空 ⇒ **沙箱/容器命名空间**，看到的网卡≠宿主机真实状态，后续结论必须显式带此限定。
- 补充探测：`docker ps`、`cat /proc/<pid>/environ | tr '\0' '\n' | grep -i proxy`、挂载表反推日志真身。

### 第 1 步：身份定位——目标 IP 是谁（禁止凭地址段猜）
```bash
grep -rn "100\.66\.1\.8" ~/ling-family-docs/docs ~/.config 2>/dev/null | head -20
```
- 100.64.0.0/10 是 CGNAT 段，Tailscale/ZeroTier/节点小宝/任意 overlay 都可能占用。**没 grep 到归属前，不许在报告里写"这是 XX 的典型地址"。**
- 本案例真相：100.66.1.8 = 本机(zhineng-ai)的节点小宝 VPN 入站地址，单向入站用；本机自身无此地址，ping 不通属正常。

### 第 2 步：本机侧排除（证明服务正常，把锅甩给上游）
| 检查 | 命令 | 期望 |
|---|---|---|
| sshd 在听 | `ss -tlnp \| grep :22` | 0.0.0.0:22 |
| 服务活着 | `systemctl status ssh` | active |
| 无重启 | `uptime`、`last -x \| grep -E 'reboot\|shutdown'` | 无故障时段记录 |
| 无链路抖动 | `grep -c 'Link is \(Up\|Down\)' /var/log/kern.log`（限当天） | 0 |
| 包到底没到 | `grep "SRC=<目标IP>" /var/log/ufw.log`（限时段） | **0 条 ⇒ SYN 没进本机，故障在上游隧道** |
| 资源没满 | `sar -n DEV`、`sar -q`、dmesg \| grep -i oom（限时段） | 正常 |

`timeout`（无 RST）+ UFW 无记录 = 包根本没到；`refused` = 到了但端口关。这组二分是最快的方向判定。

### 第 3 步：找应用层隧道日志（决定性证据通常在这里）
- 已知隧道栈优先：节点小宝 → `~/zhineng-knowledge-system/store/OWData/owjdxb/.ownbsv/NodeBabyLinkClient.log`（容器实例，保留 10 天×5 份）；宿主实例 `/tmp/.ownbsv` 仅 1MB×1，早轮转，**别指望它**。
- 未知隧道栈：`ps aux | grep -iE 'zerotier|tailscale|nblink|wireguard'`、`ip route | grep -v default` 反查路由指到哪个接口，再顺着接口找守护进程和它的日志路径。
- 日志滚成 `.gz` 用 `zcat`/`zgrep`，别因为打不开就停。

### 第 4 步：peer 级时间线统计（定位"断的是哪条路"）
```bash
grep '<peerid>' NodeBabyLinkClient.log | awk '{print substr($1" "$2,1,13)}' | sort | uniq -c
```
- 按小时聚合故障 peer 与健康 peer：故障 peer 在窗口内归零、健康 peer 频率正常 ⇒ 精确到"哪条 peer 链路"而非泛泛"网络断了"。
- 关键日志行：`ConvDestroy EConvClosedByPeer sess.destroyByPeer=true`（对端主动断） / `ConvCreate succ`（会话重建）。
- 顺手挖历史：`zgrep` 前几天的同类空窗，判断偶发 vs 慢性病（本案例 10-04/10-05 也有，结论是"PC 侧会话长期不稳"）。

### 第 5 步：三层恢复判定，禁止合并
1. **通道恢复**：peer `ConvCreate succ` 时间点（本案例 11:05:17）。
2. **服务实测可用**：Client.log `streamDo tcp://127.0.0.1:22` 与 auth.log `Accepted ... port xxxxx` 毫秒级对齐（本案例 12:34:41）。
3. **用户验证**：请用户当场复测；无记录区间显式写"无法区分 A/B"，禁止用乐观解释填空。
- 注意备用路径（ZeroTier 10.113.22.x）的登录**不是**主隧道恢复，分开记。

### 第 6 步：证据可复现性
- 每条结论附「grep 命令 + 文件路径 + 行号/计数」，用户 30 秒可验证。
- 特权盲区显式标注：`sudo -n true` 探测失败 ⇒ 写明"此项未查，需 root，建议宿主机执行 zerotier-cli dump / nft list ruleset"，**禁止用旁证硬填**。

## 二、噪声基线（下结论前必对，防误诊）

本机长期存在的三类噪声，**不是病因**：
| 噪声 | 频率 | 性质 |
|---|---|---|
| `nblinksrv.iepose.com:443` refused | ~10 分钟 | 上游未起服务，一直有 |
| whitelist 设备未绑定 | ~1 分钟 | 配置噪声 |
| `badMapingRoute` 刷屏 | 持续 | 打印噪声 |

判据：**故障期新出现的异常才算证据；一直存在的异常只能进噪声基线表。** 本案例 lc 的错误正是把 TeamViewer keepalive 断流（旁证，55s 间隔）当主证据定了 09:30 恢复点。

## 三、本案例标准答案（基准，2026-10-06）

- **断**：08:25:20 PC 侧 peer `6B4B9CECD326636BOW` 主动关闭（EConvClosedByPeer，destroyByPeer=true）。
- **通道恢复**：11:05:17 peer 重建（ConvCreate succ）。
- **首次实测 ssh 通**：12:34:41（streamDo :22 ↔ auth.log Accepted 对齐）。
- 期间用户 09:00 走不通、09:34 经 ZeroTier 备路（10.113.22.66）进入——备路不影响主隧道判定。
- 当天还有第一次断连 05:24→07:54；历史 10-04/10-05 同类空窗 ⇒ 慢性不稳，非孤例。

## 四、效率参考（谁怎么跑出来的）

| agent | 调用数 | 出结论 | 关键打法 |
|---|---|---|---|
| atomcode | 79 | 15:35（最快最准） | 一次到位，边查边收敛 |
| cc | 85 | 16:07 | 复合命令一次取多证 + peer 小时级统计 |
| opencode | 45 | 18:06（用户追问 2 次） | 证据最完备（毫秒对齐/噪声基线/历史模式/证据保留性） |
| codex | 38 | 未出终稿 | 单侧证据不下根因结论（诚实）+ 探针建议 |
| lc | ~7 | ❌ | 调用太少+凭记忆猜归属+旁证当主证据 |

lc 目标形态：**atomcode 的一次到位节奏 + opencode 的证据完备性**。工具调用量下限参考：此类问题 ≥20 次且必须覆盖第 0/1/3 步。

## 五、附：纪律速查（已同步 memory.md）

1. 先声明命名空间视角（第 0 步），再谈网络。
2. 目标 IP 归属必须 grep 实证，禁止按地址段猜。
3. timeout/refused 二分 + UFW/kern.log 排除法定方向。
4. 决定性证据去隧道守护进程自己的日志找（含 zcat 轮转件）。
5. peer 级时间线统计，精确到"哪条链路"。
6. 通道恢复 / 服务可用 / 用户验证三层分开，缺证据的区间显式说"无法区分"。
7. 噪声基线先行，故障期新异常才作证据。
8. 结论附可复现命令；特权盲区显式标注，不硬填。
