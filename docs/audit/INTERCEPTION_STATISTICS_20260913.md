# 拦截统计报告（2026-09-13）

数据源：`.lingclaude/journals/*.jsonl`（2898 个会话 journal）
tool_result 总数：8329
被拦截命令总数：203（占比 2.4%）

## 拦截分类统计

| 分类 | 次数 | 占比 | 性质 |
|---|---|---|---|
| 其他(真实命令) | 123 | 60.6% | ⚠️ 需人工判断 |
| git 提交消息含敏感词 | 18 | 8.9% | ❌ 误杀（能力绞杀） |
| 磁盘/挂载探测 | 17 | 8.4% | ⚠️ 环境探测（部分误杀） |
| 环境探测(sudo/ulimit) | 10 | 4.9% | ⚠️ 环境探测（部分误杀） |
| 测试构造(验证沙箱) | 8 | 3.9% | ✅ 预期拦截（测试验证） |
| 写文件命令 | 7 | 3.4% | ⚠️ 需人工判断 |
| 测试构造(验证黑名单逻辑) | 4 | 2.0% | ✅ 预期拦截（测试验证） |
| 测试构造(验证脱敏) | 4 | 2.0% | ✅ 预期拦截（测试验证） |
| secret 扫描运行 | 4 | 2.0% | ❌ 误杀（能力绞杀） |
| tmp 读取 | 3 | 1.5% | ⚠️ 需人工判断 |
| 配置读取 | 3 | 1.5% | ⚠️ 需人工判断 |
| 审计脚本 | 1 | 0.5% | ⚠️ 需人工判断 |
| 环境探测(挂载/权限) | 1 | 0.5% | ⚠️ 环境探测（部分误杀） |

## 结论

1. **误杀集中在 3 类**：凭据搜索(grep)、git 提交消息含敏感词、secret 扫描运行——它们都是"检测/清理泄漏"的安全操作本身，被输入侧黑名单误杀
2. **环境探测类**（sudo/mount/ulimit）部分合理（sudo 确实危险），但 mount/df/lsattr 只读探测被连坐
3. **真实危险拦截**（rm -rf /、sudo、网络黑名单）工作正常

## 2026-09-13 细颗粒度优化（实施后）

### 修复的误杀类别（统计实证 → 代码修复）

| 误杀类型 | 修复 |
|---|---|
| `grep -n "ssh"` / `grep -rn "ssh\|SSH"` 被拦 | `_BLOCKED_BASE_COMMANDS` 拆分：命令名位置拦截，不连坐参数 |
| `cat proxy3.service` / `cp proxy3.service` 被拦 | 同上（service 不再全文本连坐） |
| `grep -rn "systemctl" docs/` 被拦 | 同上 |
| `curl -sI` / `curl -o /dev/null` 只读探测被拦 | `_is_readonly_network_probe`：只读形态放行 |
| `grep -rn "ssh\|SSH"` 引号内管道被误拆 | `_split_chain` 引号感知重写 |

### 保留的拦截（安全语义不变）

- `rm -rf /`、`rm -rf .git` 等 → 全量拦截
- `sudo`/`su`/`mkfs` → 命令名位置 + 参数位置（DANGER_ANYWHERE）都拦
- `mount`/`ssh`/`systemctl`/`service`/`crontab` 命令名 → 拦
- curl/wget 下载/执行形态（`-o file`、`| sh`、`-O`）→ 拦
- 凭据写入/导出（`echo sk- > file`、`export KEY=sk-`）→ 拦

### 验证

- 新增 `tests/test_granular_sandbox.py`：58 项细颗粒度回归
- 既有回归：test_exp_s2_regression + test_bash_lingxi_security + test_security_capability_balance = 57 passed
- bash 网络/输出修饰/sandbox fixes/t0/t3 = 110 passed
- test_core.py = 53 passed

### 设计原则（对齐 codex sandbox-mode 思想）

1. **分级而非一刀切**：命令名位置拦截（危险命令）vs 参数位置放行（grep/cat 检索）
2. **只读探测豁免**：健康检查/连通性探测是合法操作，误杀=能力绞杀
3. **危险传递 fail-closed**：sudo/su/mkfs 在参数位置也拦（防管道/命令替换绕过）
4. **引号感知**：grep 模式参数中的 `|`/`;` 不再被误拆成子命令
