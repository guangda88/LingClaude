# 四项管线升级实施报告（2026-09-12）

> 依据: codex 审计 P0-P2 建议 + 本会话源码核验
> 状态: 全部实施 + 测试通过

---

## 1. 工具执行主链统一（P0）

**问题**: read 快路径绕过 ToolPipeline（权限/守卫/敏感路径全跳过，直接查 ContextCache）；
sub_agent 直连 runtime 无统一错误协议。

**改动**:
- `tool_pipeline.py` 新增 `check_permission()` — 纯权限预检（只跑 1-2 段，不执行 handler）
- `coding.py` 提取 `_tool_blocked()`/`_blocks()` — 权限判定唯一真源（原 `_blocks` 闭包外提，供 ToolExecutor 复用）
- `tool_executor.py` read 快路径先过 `check_permission`（含 `_blocks` + guards），放行才读 cache

**效果**: 快路径不再绕过权限；主链（stream/非 stream/MCP/sub_agent/bus_responder）全部经
ToolExecutor._execute_tool_typed → runtime.execute_tool → ToolPipeline。

## 2. 强类型工具结果协议（P1）

**问题**: `'"error"' in tool_output` 字符串判断脆弱（误判/漏判）。

**改动**:
- `types.py` 新增 `ToolError`/`ToolErrorCode`/`ToolResult`/`parse_tool_result`/`is_tool_error`
- `tool_pipeline.py` 各 abort 分支带结构化 error_code；新增 `execute_typed()`
- `tool_executor.py`/`sub_agent.py` 新增 `_execute_tool_typed`，错误语义用 error.code
- 消灭生产代码全部 `'"error"' in` 判断（model_call/tool_call_executor/query_engine）
- MCP fallback 判据收紧: 仅 `TOOL_NOT_FOUND` 才 fallback（堵住权限拒绝被 MCP 绕过）

## 3. session rewind/快照回滚（P1）

**问题**: checkpoint 单文件覆盖，无历史版本，无法回滚。

**改动**:
- `session_store.py` 支持多版本 checkpoint（`{session_id}@{tag}.json`），新增
  `list_checkpoints()` / `load_checkpoint_by_tag()`
- `session_persist.py` 新增 `rewind_to(tag)` — 恢复 messages/conversation/transcript + journal 清空
- `submission.py` `_save_checkpoint` 每轮自动带 `roundN` tag；新增 engine 级 `list_checkpoints()`/`rewind_to()`
- `commands.py` 新增 `/rewind`（无参列出/序号或 tag 回滚）

## 4. MCP Provider Manager（P2）

**问题**: stdio/http 每次调用新建+关闭 client（无连接复用）；工具冲突无检测；schema 无缓存。

**改动**:
- `mcp_client.py` 新增 `MCPClientPool`（按 key 缓存 + TTL + 健康检查 + 失效自动重连）
- `mcp_proxy.py` call_tool 的 stdio/http 分支改走连接池；新增 `find_server_with_conflicts()`
- `mcp_proxy.py` 新增进程级 `_schema_cache`；`get_stats()` 扩展连接池/冲突/schema 统计

---

## 测试

| 测试文件 | 用例 | 结果 |
|---|---|---|
| tests/test_tool_result_protocol.py | 29 | ✅（含主链统一 4 个新用例）|
| tests/test_session_rewind.py | 10 | ✅ |
| tests/test_mcp_provider_manager.py | 13 | ✅ |
| 回归: test_tool_pipeline / test_mcp_proxy / test_session_store / test_session_journal / test_denial_logging / test_guard_wiring / test_bash_lingxi_security | 137 | ✅ |

已知环境噪音（非本次引入）: test_coding 的 git EACCES（/dev/urandom）+ /tmp 路径策略；
test_mcp_server 的 git add 在 /tmp 失败。

## 遗留（未动，需独立提案）

- sub_agent 复用 ToolExecutor 的 retry/MCP-fallback（当前直连 runtime 是隔离设计，allowed_tools 白名单已足够）
- ToolPipeline 超时线程无法强杀（daemon thread 局限）
- MCP server 初始化失败静默（保持容错语义）
