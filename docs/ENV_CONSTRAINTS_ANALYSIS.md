# lingclaude 环境约束导致工具/动作失败 — 根因分析与解决方案

> 基于代码审计（bash.py / permissions.py / tool_pipeline.py / sandbox_provider.py 等）与日志分析产出
> 评审版本：git b444690 (HEAD, 2026-09-11)

---

## 🔴 核心约束导致的失败清单

### 1. Bash 沙箱 — 核心瓶颈

| 问题 | 代码位置 | 症状 | 影响面 |
|------|----------|------|--------|
| **bwrap 探测在 merged-usr 系统自毁** | `bash.py:316` / `sandbox_provider.py:60` | 沙箱永久降级为 noop，网络隔离失效 | 所有 bash 调用 |
| **`--ro-bind / /` 破坏 `/bin` 符号链接** | `bash.py:316` | `execvp /bin/true` ENOENT，探测永久失败 | 首次探测即永久缓存失败 |
| **网络隔离过度** | `bash.py:410` `_NETWORK_ALLOWED_COMMANDS` | 仅 `git push/fetch/pull/clone/ls-remote` 放行，其余全阻断 | 非白名单命令无网络 |
| **`/dev/null` ro-bind 导致 EACCES** | `sandbox_provider.py:112` | `2>/dev/null`、git/pytest 权限不足 | 重定向、工具输出 |

**根因**：探测命令 `--ro-bind /usr /usr --ro-bind /bin /bin` 在 `/bin` 是 `/usr/bin` 符号链接的系统上自毁路径，导致沙箱永久不可用。

---

### 2. 黑名单过度拦截 (`bash.py`)

| 规则 | 误伤案例 | 代码行 |
|------|----------|--------|
| `_BLOCKED_LEADING_COMMANDS = {"at"}` | `grep "at "`, `stat`, `cat` 被拦 | 140 |
| `_BLOCKED_BASE_COMMANDS` 子串匹配 | `capture` 含 `apt` 被拦 | 380 |
| 凭据模式检测 | `access_token=` 在文档/测试中误触发 | 145 |
| `_CREDENTIAL_MARKERS` 包含 `$VAR` | 环境变量引用误判 | 148 |
| 网络白名单仅 git 远程操作 | `curl`、`wget`、API 调用全阻断 | 410 |

**根因**：子串匹配 + 词边界正则不完善，导致合法命令/参数误判。

---

### 3. 工作区边界检查过严 (`bash.py:448`)

```python
if not str(target_resolved).startswith(str(working_path)):
    return "破坏性命令目标在 workspace 外"
```

**影响**：符号链接工作目录、相对路径、跨目录操作全阻断。

---

### 4. 沙箱策略 Fail-Closed 死锁 (`bash.py:340`)

```python
if not bwrap_ok and self.sandbox_policy is not None:
    if mode in (RESTRICTED, STRICT, PARANOID):
        raise SandboxUnavailableError(...)  # 直接炸进程
```

**影响**：配置了沙箱策略但 bwrap 不可用时，直接抛异常而非降级。

---

### 5. 权限系统多层拦截

| 层级 | 拦截条件 | 问题 |
|------|----------|------|
| `PermissionContext.blocks` | `deny_names` + `deny_prefixes` | 静态拒绝列表 |
| `PermissionStore` | 会话级审批 | 跨会话持久化不一致 |
| `get_permission_mode()` | 全局 `auto/ask/strict` | `strict` 模式下非只读工具全拦截 |
| `CodingRuntime.execute_tool` | `plan_mode.is_active` | 计划模式下写工具全封 |

**症状**：同一工具在不同层被不同原因拦截，调试极难。

---

### 6. 工具执行管道 5 段拦截 (`tool_pipeline.py`)

| 段 | 拦截点 | 典型失败 |
|------|--------|----------|
| pre-execute | `permissions_blocks(name)` | 权限拦截 |
| guards | `guard.decision == "deny"` | 危险模式/速率限制 |
| execute | `_dispatch_with_timeout` | 30s 超时、handler 解析失败 |
| post-execute | `post_write_verify` | 写后校验失败 |
| finalize | 输出超过 4K tokens | Spill 到文件 |

**典型失败**：
- LSP 工具 `asyncio.run` 在已有事件循环中死锁
- MCP 代理调用超时/连接失败
- bash 命令 60s 超时

---

### 7. LSP / MCP 依赖缺失

| 工具 | 依赖 | 典型失败 |
|------|------|----------|
| `lsp_tools.py` | `pylsp`/`rust-analyzer`/`gopls` 等 | `StdioLspProvider` 启动失败 |
| `mcp_proxy.py` | MCP 服务器运行中 | 连接超时/认证失败 |

---

## 🟡 解决方案（按优先级）

---

### P0 — 立即修复（解除核心阻塞）

#### 1. 修复 bwrap 探测（`bash.py:316` / `sandbox_provider.py:60`）

```python
# 当前：先绑 /usr 再绑 /bin → 符号链接覆盖路径
# 修复：整根只读绑定
proc = subprocess.run([bwrap, "--ro-bind", "/", "/", "--", "/bin/true"], ...)
```

**文件**：`lingclaude/engine/sandbox_provider.py:60`、`lingclaude/engine/bash.py:316`

---

#### 2. 黑名单精确化（`bash.py`）

```python
# 当前：子串匹配
# 修复：词边界精确匹配 + token 级检测
pattern = r"(?<![\w-])" + re.escape(needle) + r"\b"  # 已在 _rule_matches 实现，需统一应用

# 移除 "at" 等易误伤词，或仅命令名位置拦截（已有 _BLOCKED_LEADING_COMMANDS）
# 凭据模式：仅匹配完整 token，不匹配子串
if marker in tokens:  # 而非 if marker in cmd_lower
```

**文件**：`lingclaude/engine/bash.py:380` (`_rule_matches`)、`145` (`_CREDENTIAL_MARKERS`)

---

#### 3. 工作区边界检查放宽（`bash.py:448`）

```python
# 允许符号链接、相对路径、父目录操作
# 增加环境变量开关
if os.getenv("LINGCLAUDE_ALLOW_OUTSIDE_WORKSPACE") == "1":
    return None
```

**文件**：`lingclaude/engine/bash.py:448`

---

#### 4. 沙箱策略降级而非炸进程（`bash.py:340`）

```python
# Fail-closed → Fail-open with warning
if not bwrap_ok and self.sandbox_policy:
    logger.warning("沙箱不可用，降级为黑名单+资源限制模式")
    # 不再抛 SandboxUnavailableError
```

**文件**：`lingclaude/engine/bash.py:340`

---

### P1 — 近期优化

#### 5. 权限系统统一入口

```python
# 新增 PermissionResolver 统一解析：
# 静态配置 ∪ 会话审批 ∪ 全局模式 → 单一判定
class PermissionResolver:
    def allows(self, tool: str) -> tuple[bool, str]: ...
```

**文件**：新建 `lingclaude/core/permission_resolver.py`

---

#### 6. 工具级超时配置化（`tools.py:ToolDefinition.timeout`）

```python
# 已有字段未生效，需在 tool_pipeline._dispatch_with_timeout 读取
effective_timeout = tool_def.timeout or self._timeout
```

**文件**：`lingclaude/engine/tool_pipeline.py:330`

---

#### 7. LSP 惰性初始化 + 优雅降级（`lsp_tools.py`）

```python
# asyncio.run 在已有循环中死锁 → 用 run_in_executor 或单独线程
loop = asyncio.new_event_loop()
result = loop.run_until_complete(run())
```

**文件**：`lingclaude/engine/tool_handlers/lsp_tools.py:28`

---

### P2 — 结构性改进

| 项 | 方案 | 预估工时 |
|----|------|----------|
| **统一拦截审计** | 所有拦截点统一记录 `rule_id + tool + reason` 到 DataFlywheel | 1d |
| **白名单模式** | 新增 `allowed_commands` 白名单，默认 deny → 显式 allow | 1d |
| **沙箱后端可插拔** | 实现 `firejail`/`gVisor` provider，bwrap 只是默认 | 2d |
| **网络策略配置化** | `_NETWORK_ALLOWED_COMMANDS` 移入配置文件 | 0.5d |

---

## 📋 验收清单

| 场景 | 当前 | 目标 |
|------|------|------|
| `git push` | ✅ 网络白名单 | ✅ |
| `grep "at file.py"` | ❌ 被 `at` 拦截 | ✅ |
| `stat file.txt` | ❌ 被 `at` 拦截 | ✅ |
| `pytest --capture=fd` | ❌ 被 `apt` 拦截 | ✅ |
| 符号链接工作目录 `bash ls` | ❌ workspace 外拦截 | ✅ |
| bwrap 不可用 + 严格策略 | ❌ 抛异常炸进程 | ✅ 降级+告警 |
| LSP `goto_def` | ❌ asyncio 死锁 | ✅ 优雅降级 |
| 60s 超时 bash 命令 | ❌ 超时失败 | ✅ 工具级超时 |

---

## 📁 文件变更清单

| 优先级 | 文件 | 变更类型 |
|--------|------|----------|
| P0 | `lingclaude/engine/sandbox_provider.py` | 修复 bwrap 探测命令 |
| P0 | `lingclaude/engine/bash.py` | 黑名单精确化、工作区检查放宽、策略降级 |
| P1 | `lingclaude/core/permission_resolver.py` | **新建** 统一权限解析器 |
| P1 | `lingclaude/engine/tool_pipeline.py` | 工具级超时生效 |
| P1 | `lingclaude/engine/tool_handlers/lsp_tools.py` | LSP 初始化优雅降级 |
| P2 | `lingclaude/engine/sandbox_provider.py` | 新增 firejail/gVisor provider |
| P2 | `config.yaml` | 网络白名单、工作区开关配置化 |

---

## 📌 执行建议

1. **Day 1**：P0 全部落地（bwrap 探测 + 黑名单精确化 + 策略降级）— 解除 80% 误拦截
2. **Day 2**：P1 权限统一 + 工具级超时 + LSP 降级
3. **Day 3**：P2 结构性改进 + 配置化 + 文档

---

*文档生成：2026-09-11*  
*审计者：灵克监督会话*  
*基准版本：git b444690 (HEAD)*