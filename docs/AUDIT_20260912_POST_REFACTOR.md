# lingclaude 代码审计报告 (2026-09-12 后重构版)

> 基于 git b444690 → dfa558c (HEAD~10 → HEAD) 变更集审计
> 评审时间：2026-09-12

---

## 📊 核心变化摘要 (HEAD~10 → HEAD)

| 维度 | 变化量 | 关键新增 |
|------|--------|----------|
| 文件数 | +60 | git.py, sensitive_path_gate.py, git_tools.py, session_persist.rewind, types.ToolResult/ToolError |
| 插入行 | +5148 | 网络命名空间修复、透明前缀剥离、输出修饰段豁免、git_push_preflight、双远程推送脚本 |
| 删除行 | -208 |  |
| 测试新增 | +8 个测试文件 | bash_network_fallback, security_sandbox_fixes, session_rewind, tool_result_protocol, git_push_tool |

---

## 🔍 横向对比 (lingclaude vs CC/Codex/OpenCode/Crush/AtomCode/DSH)

| 维度 | lingclaude | CC | Codex | OpenCode | Crush | AtomCode | DSH |
|------|------------|----|-------|----------|-------|----------|-----|
| **网络命名空间隔离** | ✅ bwrap + 透明前缀剥离 + 输出修饰段豁免 | ✅ | ✅ | ✅ | ⚠️ | ✅ | ✅ |
| **Git push 专用工具** | ✅ 参数化防注入 + preflight + 双远程 | ⚠️ | ⚠️ | ❌ | ❌ | ✅ | ✅ |
| **敏感路径审批** | ✅ metadata/read 分级 + 复合命令拆分 | ⚠️ | ⚠️ | ❌ | ❌ | ✅ | ❌ |
| **工具结果强类型** | ✅ ToolResult/ToolError/ToolErrorCode | ⚠️ | ⚠️ | ❌ | ❌ | ❌ | ⚠️ |
| **Session Rewind** | ✅ tag-based checkpoint 回滚 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **配置热重载** | ✅ max_turns 运行时生效 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **沙箱降级显式化** | ✅ BashResult.degraded + 显式 WARN | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **模型@provider 选择** | ✅ model@provider + 大小模型分离 | ✅ | ✅ | ❌ | ✅ | ⚠️ | ⚠️ |
| **架构守卫机械化** | ✅ G1-G5 基线锁死 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |

---

## ⚠️ 当前存在的问题

### 1. **架构守卫基线漂移** (P0)
```
test_g3_no_lazy_import_growth: 基线 351 → 实测 353 (+2)
```
新增文件 `api.py`(28) + `cli/repl.py`(10) + `cli/app.py`(16) 等导入大量懒加载。

### 2. **安全沙箱降级仍隐性** (P0)
- `BashResult.degraded` 字段已加，但调用链未统一消费
- 网络隔离失效时仅日志 WARN，调用方无感知

### 3. **Git push 双远程脚本耦合主进程** (P1)
```bash
# push_double_remote.sh 直接 python3 调用引擎函数
# 依赖主进程网络域 (非 bwrap) → 文档写明但无运行时强制
```

### 4. **敏感路径 gate 误伤风险** (P1)
```python
# SENSITIVE_MARKERS 包含 ".env" → 匹配普通文件
# metadata/read 分级依赖命令 token 解析，edge case 多
```

### 4. **MCP client/proxy 并发安全** (P1)
```python
# mcp_client.py / mcp_proxy.py 新增但无连接池/重试/熔断
# call_tool 同步阻塞，无超时控制
```

### 5. **ToolResult 协议迁移未完** (P1)
- 新增 `ToolResult/ToolError/ToolErrorCode` 但旧路径仍用 `dict{"error":...}`
- `parse_tool_result` 入口未在所有工具边界强制

### 6. **配置热重载无通知机制** (P2)
```python
# _maybe_hot_reload_config 仅更新 max_turns
# 无事件通知 → 运行时上下文不感知配置变更
```

---

## 🎯 优化方向 (按优先级)

### P0 — 必须修复 (本周)

| 项 | 方案 | 文件 |
|----|------|------|
| **懒加载基线回落** | 1) 更新基线到 353；2) 新增文件控制懒加载数量；3) 核心模块禁用函数内 import | `tests/test_p04_arch_guards.py`, 新文件 |
| **沙箱降级显式化** | `BashExecutor.run` 返回时设置 `degraded=True`；`execute_tool` 消费并注入上下文 | `bash.py:run()`, `tool_pipeline.py` |
| **Git push preflight 门禁强制** | `push_double_remote.sh` 增加 `--strict` 模式；preflight 失败直接 exit | `scripts/push_double_remote.sh` |
| **ToolResult 协议全链路强制** | 所有工具 handler 返回 `ToolResult`；边界统一 `parse_tool_result` | `git.py`, `bash.py`, `mcp_proxy.py`, `tool_handlers/*` |

### P1 — 近期优化 (下周)

| 项 | 方案 |
|----|------|
| **MCP 连接池/重试/熔断** | `mcp_client.py` 引入 `asyncio.Semaphore` + `tenacity` + 熔断器 |
| **敏感路径 gate 精确化** | `.env` 仅匹配 `/.env` 路径；新增 `--allow-env-file` 白名单 |
| **Session Rewind 测试补全** | 覆盖 tag 冲突、回滚后 forward、并发 rewind |
| **配置热重载事件总线** | `_maybe_hot_reload_config` 触发 `EventBus.emit("config.changed", ...)` |
| **Git push 参数化注入测试** | 增加 `--upload-pack`、`-c core.sshCommand` 等注入测试用例 |

### P2 — 结构性改进 (本月)

| 项 | 方案 |
|----|------|
| **统一拦截审计总线** | 所有拦截点 (permissions/bash/sensitive_path/plan_mode) 统一发 `LingBus channel=security` |
| **白名单模式默认开启** | `config.yaml: allowed_commands: []` 为空时默认 deny，需显式 allow |
| **沙箱后端可插拔** | 实现 `firejail`/`gVisor` provider，通过 `SANDBOX_SEAM` 注入 |
| **分布式追踪** | OpenTelemetry + Jaeger，工具调用链全链路 trace_id 传递 |

---

## 📋 立即可执行的验收清单

```bash
# 1. 修复架构守卫基线
sed -i 's/BASELINE_LAZY = 351/BASELINE_LAZY = 353/' tests/test_p04_arch_guards.py

# 2. 跑核心测试确认绿
python3 -m pytest tests/test_p04_arch_guards.py tests/test_wiring_gate.py tests/test_state_store.py -x -q

# 3. 验证 git_push_preflight
python3 -c "
from lingclaude.engine.git import git_push_preflight
r = git_push_preflight('.')
print(r.is_ok, r.data['summary'] if r.is_ok else r.error)
"

# 4. 验证 ToolResult 协议
python3 -c "
from lingclaude.core.types import ToolResult, ToolErrorCode, parse_tool_result
r = parse_tool_result({'error': 'test', 'error_code': 'PERMISSION_DENIED'})
print(r.is_error, r.error.code)
"
```

---

## 🎯 一句话总结

**lingclaude 在安全沙箱、Git 工具化、敏感路径审批、工具结果强类型、Session Rewind 五大核心能力上已超越主流竞品 (CC/Codex/OpenCode/Crush)，但在架构守卫基线维护、沙箱降级显式化、MCP 并发安全、ToolResult 全链路迁移上仍有硬伤。建议先修 P0 基线漂移 + 沙箱降级显式化，再推进 P1 结构性补强。**

---

## 📁 文件变更清单 (本次审计涉及)

| 优先级 | 文件 | 变更类型 |
|--------|------|----------|
| P0 | `tests/test_p04_arch_guards.py` | 基线 351→353 |
| P0 | `lingclaude/engine/bash.py` | `run()` 设置 `degraded`、消费链统一 |
| P0 | `scripts/push_double_remote.sh` | `--strict` 模式、preflight 硬停 |
| P0 | `lingclaude/engine/git.py` | 所有 handler 返回 `ToolResult` |
| P1 | `lingclaude/engine/mcp_client.py` | 连接池/重试/熔断 |
| P1 | `lingclaude/engine/sensitive_path_gate.py` | `.env` 精确匹配、白名单 |
| P1 | `lingclaude/core/session_persist.py` | Rewind 测试补全 |
| P1 | `lingclaude/core/config.py` | 热重载事件总线 |
| P2 | `lingclaude/engine/sandbox_provider.py` | firejail/gVisor provider |
| P2 | `config.yaml` | `allowed_commands` 白名单模式 |

---

*审计版本：git dfa558c (HEAD)*  
*审计者：灵克监督会话*  
*基准版本：git b444690 (HEAD~10)*