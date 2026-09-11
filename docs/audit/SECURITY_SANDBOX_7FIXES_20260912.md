# codex 安全沙箱审计 7 项修复 — 实施归档（2026-09-12）

commit: `420d260`（8 文件 +527/-44）
前置: 4f44a78（输出修饰段误伤修复）

## 审计来源
codex 对 lingclaude 安全沙箱的深度审计。逐条源码核验后 7 项全部成立并修复。

## 修复清单

### P0-1: SensitivePathGate 复合命令绕过（sensitive_path_gate.py）
- **问题**: `check_sensitive_path` 只看 `cmd.split()[0]` 首 token，
  `test -f ~/.ssh/config && cat ~/.ssh/config` 首 token 是 metadata 命令 → 放行
- **修复**: 新增 `_split_command_chain()`（按 &&/||/;/|/$() 拆子命令）
  + `_cmd_intent()`（metadata/read/unknown 意图分级）
  逐子命令独立判定，任一子命令 read/unknown → 拦截
- **验证**: `test -f x && cat x` → 拦截；`test -f x` / `ls` → 放行（不误伤）

### P0-2: bash_lingxi 正则写坏（tool_handlers/bash_tools.py）
- **问题**: `_bash_lingxi_handler` 用了非 raw string 的正则副本（`\\\\`），
  二次转义后只匹配出 `~/.` 而漏掉 `/ssh/config` → gate 形同虚设
- **修复**: 抽公共 `_PATH_TOKEN_RE` + `_check_sensitive_in_command()`，
  两 handler 完全同源，禁止二次分叉
- **验证**: `re.findall(_PATH_TOKEN_RE, 'cat ~/.ssh/config')` → `['~/.ssh/config']`

### P0-3: bwrap 探测不一致 + 静默降级（bash.py + sandbox_provider.py）
- **问题**: bash.py 旧探测不带 `--unshare-net`（与真实 wrap 不一致）→
  「探测通过、执行失败」；无 policy 时降级只 WARN 后静默执行
- **修复**: bash.py `_bwrap_probe` 委托 sandbox_provider（带 --unshare-net）；
  BashResult 新增 `degraded: bool = False` 字段，降级时置 True 供上层可见

### P0-4: 网络白名单参数级校验（bash.py）
- **问题**: 字符串前缀匹配挡不住 `git push --upload-pack=evil` / `-c` 参数注入
- **修复**: 新增 `_git_network_safe()` — git 命中段做参数级校验：
  - 危险参数（--upload-pack/--receive-pack/--exec/-c/--config-env）拒绝
  - 命令替换（$()/``）拒绝
  - remote 白名单（origin/github/upstream/gh + URL 形）拒绝未登记 remote
  - 选项参数/重定向（2>&1 / >log / 1>>log）跳过
- **验证**: 10 用例矩阵全过（含注入拒绝 + 输出修饰放行 + 透明前缀放行）

### P1-1: bash 超时真正取消（bash.py）
- **问题**: 超时后 daemon 线程/子进程残留，副作用继续发生
- **修复**: 三处 subprocess 加 `start_new_session=True`；TimeoutExpired 分支
  `os.killpg(pgid, SIGKILL)` 杀整组
- **验证**: `sleep 30` 超时后无残留进程

### P1-2: 写工具 post-write 失败自动回滚（tool_pipeline.py + coding.py）
- **问题**: post-write 验证失败返回错误但文件已写入（脏文件残留）
- **修复**: pipeline.execute 新增 `rollback_callback` 参数；coding.py 传入
  `_auto_rollback_write()`（基于 file_edit.undo 的 .bak 回滚）
  失败时自动 undo，回滚结果附在错误里（「已回滚」/「回滚失败: ...」）

### P1-3: MCP 工具假可用（mcp_proxy.py）
- **问题**: stdio server 二进制不存在仍注册，死工具暴露给模型
- **修复**: register_server 时 `shutil.which(command[0])`；缺失 → status=unavailable；
  list_all_tools 跳过 unavailable；call_tool 拒绝（SERVER_UNAVAILABLE）
- **验证**: lingflow-mcp 缺失时正确标记 unavailable，工具不暴露

## 测试
- 新增 `tests/test_security_sandbox_fixes.py`: 30 用例（P0-1×6 / P0-2×3 / P0-3×2 / P0-4×13 / P1-1×2 / P1-2×2 / P1-3×4）
- 更新 `tests/test_mcp_proxy.py`: 断言按 P1-3 语义修正（unavailable 工具不暴露）
- 回归: test_bash_lingxi_security + test_bash_network_fallback + test_bash_output_modifier
  + test_tool_pipeline + test_cleanup_sandbox = 68 passed
- MCP: test_mcp_proxy + test_mcp_provider_manager = 35 passed
- 合计 133 用例全绿

## 遗留（需独立提案）
- ToolPipeline 超时线程无法强杀（bash 已 killpg，但非 bash handler 仍受限）
- 专用 git_remote 工具（codex 建议的终极形态：结构化 verb/remote/refspec，替代字符串白名单）
- CommandPolicyEngine（声明式策略引擎替代正则启发式）
