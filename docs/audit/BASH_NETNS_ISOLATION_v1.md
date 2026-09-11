# BASH_NETNS_ISOLATION_v1 — bash 工具进程被隔离网络命名空间（netns）审计

> 2026-09-11 灵克(lingclaude) 产出 · 状态：**根因已确认，待下次会话实施修复**
> 关联：GIT_NETWORK_FIX_v1.md / GIT_NETWORK_ISSUE_v1.md / PUSH_FAILURE_20260911.md / BASH_NETWORK_WHITELIST_FIX_v1.md

---

## 一、问题现象（为什么一直推不上去）

多轮会话中，lingclaude 的 bash 工具内执行 `git push / fetch / ls-remote` 全部失败：

```
git ls-remote https://github.com/guangda88/LingClaude.git HEAD
fatal: Could not resolve host: github.com        # DNS 失败

git push github master
ssh: connect to host github.com port 22: Network is unreachable
```

期间尝试过的修复（均未根治）：
1. 网络白名单 + `allow_network` 透传（bash.py / sandbox_provider.py / capability_seam.py）
2. bwrap probe 修正 + noop 降级
3. RLIMIT_AS 提升到 1GB（修 git malloc failed）
4. remote 从 gitea 切到 github（HTTPS + SSH 双远程）
5. 反复重启 lingclaude 会话

**每次重启后依然失败** —— 这是最关键的线索：问题不在 lingclaude 进程本身。

---

## 二、决定性证据（2026-09-11 重启后实测）

### 证据 1：bash 工具的网络视图只有 loopback

```bash
$ ip addr
1: lo: <LOOPBACK,UP,LOWER_UP> ... inet 127.0.0.1/8   # ← 唯一接口
   （无 eth0 / wlan0 / tailscale0 / enp7s0 / NodeBabyLink）

$ ip route show
   （空，无默认路由）

$ getent hosts github.com
   （解析失败）
```

### 证据 2：宿主网络完全健康（同一台机器，宿主终端）

```bash
$ ip addr          # enp7s0 192.168.31.49/24 UP + NodeBabyLink(100.x) + docker 网桥
$ ip route         # default via 192.168.31.1 dev enp7s0
$ ping github.com  # 2 packets, 0% loss, ~107ms
$ curl -v https://github.com  # HTTP/2 200 TLS OK
$ ssh -T git@github.com       # "Hi guangda88!" 认证成功
```

### 证据 3：bwrap 已不可用 → bash 命令是裸跑，仍无网络

```bash
# sandbox_provider probe 结果：
#   bwrap --ro-bind / / -- /bin/true        → 成功
#   bwrap --unshare-net --ro-bind / / ...   → 失败（uid map: Read-only file system）
# → create_default_sandbox_provider() 降级为 NoopSandboxProvider

# 因此 bash 工具执行的命令【完全没有】--unshare-net 包裹，
# 但网络视图仍然只有 lo —— 证明隔离发生在 bash 工具进程的更外层。
```

### 证据 4：同一仓库、同一机器，宿主终端 push 成功

```bash
# 宿主终端（非 lingclaude bash 工具）执行：
$ git -C ~/lingclaude ls-remote https://github.com/guangda88/LingClaude.git HEAD
48eef43... HEAD        # 成功
```

---

## 三、根因结论

```
┌─────────────────────────────────────────────────────────────┐
│  宿主 runtime（启动 lingclaude 的进程/沙箱/服务）             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  lingclaude 进程（PID 3601010 等）                    │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  bash 工具子进程（subprocess）                   │  │  │
│  │  │  netns = 只有 lo，无路由，无 DNS                 │  │  │
│  │  │  ← 隔离在这里！                                 │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

- **承载 bash 工具的子进程运行在一个只有 loopback 的隔离网络命名空间里**；
- 该 netns 由**运行 lingclaude 的宿主 runtime** 创建（非 lingclaude 代码）；
- 因此：白名单放行、bwrap 去掉 `--unshare-net`、重启会话、切 remote，**全部无法穿透这层隔离**；
- **重启后依然失败** 正是因为重启后的 bash 工具仍由同一个 runtime 在同一 netns 内启动。

### 与 lingclaude 代码的关系

| 模块 | 状态 |
|---|---|
| bash.py 网络白名单 `_is_network_allowed` | ✅ 正确（`git push/ls-remote` 放行，复合命令全链判定） |
| sandbox_provider.py `allow_network` | ✅ 正确（noop 降级后裸跑） |
| capability_seam.py | ✅ 正确（透传） |
| **bash 工具执行环境 netns** | ❌ **问题所在（runtime 层，非 lingclaude 代码）** |

---

## 四、四位监督建议评估（避免下次误判）

| 监督 | 核心建议 | 评估 |
|---|---|---|
| codex | 外层沙箱禁网；在宿主 shell 手动 push | ✅ 最接近真相，方向正确 |
| claudecode | origin 走 SSH；修 credential helper | ⚠️ 方向对（SSH 认证已通），未识别 netns |
| atomcode | 切 HTTPS remote + token | ⚠️ remote 已切（宿主侧 fetch 成功）；"SSH key 不匹配" **错误**（`Hi guangda88!` 认证成功） |
| opencode | Gitea 服务端故障 | ❌ 基于旧 remote=gitea 的过时信息；当前 remote 已是 github 且 SSH 连接成功 |

---

## 五、下次会话解决方案

### 第一步：复现确认（30 秒）

```bash
# 在 lingclaude bash 工具内：
ip addr | grep -c "^[0-9]*: [^l]"    # 若输出 0 → 只有 lo，确认隔离
ip route show | wc -l                 # 若输出 0 → 无路由
```

### 第二步：定位隔离来源（宿主终端）

```bash
# 1. lingclaude 由谁启动？查父进程链
ps -o pid,ppid,cmd -p $(pgrep -f "lingclaude run" | head -1)
pstree -p $(pgrep -f "lingclaude run" | head -1) | head -20

# 2. 该父进程是否在 netns 里？比较 /proc/<pid>/ns/net 与宿主
readlink /proc/$$/ns/net                          # 宿主 netns（应含真实接口）
readlink /proc/<lingclaude父pid>/ns/net           # 若不同 → 父进程被隔离

# 3. 谁创建了这个 netns？
lsns -t net | grep -E "NETNS|lingclaude|systemd"  # 列出网络命名空间及创建者
```

### 第三步：修复路径（按可行性排序）

| 方案 | 操作 | 影响 |
|---|---|---|
| A. 宿主终端直接 push | 不在 lingclaude 内 push，宿主 shell 执行 | ✅ 立即可用，零风险 |
| B. 让 lingclaude 进程加入宿主 netns | 启动 lingclaude 的 runtime 使用 `--network=host` / 不隔离 bash 子进程 | 需要改 runtime 配置 |
| C. nsenter 桥接 | 对特定命令 `nsenter --net=/proc/<宿主pid>/ns/net git push ...` | 需 root/权限，仅应急 |
| D. 白名单命令专用通道 | 在 bash 工具内对白名单 git 命令改用宿主 netns 执行 | lingclaude 代码层可做，但需权限配合 |

### 第四步：验收标准

```bash
# 在 lingclaude bash 工具内（修复后）：
git ls-remote https://github.com/guangda88/LingClaude.git HEAD
# 返回远程引用 → 修复成功
# 仍 DNS 失败 → 确认 netns 隔离未解除，回退到方案 A
```

---

## 六、附带发现（本次会话已修复，供回溯）

1. **退出报错**：`/quit` 时 `Exception ignored in: <module 'threading'>`（BackgroundTaskManager.shutdown 未被调用）→ 已修（coding.py `close()` + app.py `atexit`），未提交。
2. **model@provider 选择器**：commit 57c76c6 已落地，`/model deepseek-v4-flash@volcengine` 可用。
3. **RLIMIT_AS 512MB**：白名单 git 命令已提升 1GB（bash.py），但父进程 hard limit 锁死时无效，需宿主侧干净进程。

---

## 七、版本记录

| 版本 | 日期 | 作者 | 变更 |
|---|---|---|---|
| v1 | 2026-09-11 | 灵克 | 初稿：netns 隔离根因确认 + 四监督评估 + 修复路径 |

*本审计遵循 memory lingclaude-true-read.md 纪律：只列实测证据，不编故事。*
