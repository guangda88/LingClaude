# 六维评测综合优化实施记录（2026-09-11）

> 输入：lingclaude/cc/codex/opencode/atomcode 六份评测报告（同一会话产物）
> 方法：先源码验证再实施，拒绝凭报告转述动手
> 原则：安全第一、小步可测、不碰大重构

---

## 一、评测建议的源码核验结论

| # | 评测建议 | 源码实况 | 判定 |
|---|---------|---------|------|
| 1 | bash_lingxi 是安全旁路（无黑名单/无敏感路径 gate） | coding.py:119-124 初始化 `blocked_commands=[]`+`allowed_commands=None`；bash_tools.py:33 handler 无 gate | ✅ 成立（P0） |
| 2 | guard 异常 fail-open | tool_pipeline.py:224-226 捕获后视为 abstain 放行 | ✅ 成立（P0） |
| 3 | 超时线程无法强杀 | tool_pipeline.py:329 daemon 线程 join 后不 kill | ✅ 成立（已注释局限） |
| 4 | 字符串判断 `"error" in` | model_call.py:525 / query_engine.py:652 / tool_executor.py:111 | ✅ 部分成立 |
| 5 | intelligent_router 是 dead code | 被 query_engine/wiring/behavior_aware_router 等 6 处引用 | ⚠️ 误报，但 GLMModel 枚举缺 glm-5.3-flash 属实 |
| 6 | token_monitor 硬编码 GLM-4.7 | 已收敛 TARGET_MODEL 常量 + config.yaml 动态读取 | ❌ 已修复 |
| 7 | LING_REPOS 缺三仓 | 已补录 lingcode/llm-proxy | ❌ 已修复 |
| 8 | request_user_input 缺失 | tool_registration.py:273 已注册 + coding.py:221 handler | ❌ 已存在 |
| 9 | 工具主链多入口 | read 快路径(tool_executor.py:88)绕过 ToolPipeline | ✅ 成立（P1，本次未动） |
| 10 | LSP 悬空 | StdioLspProvider 已实现但 0 测试 | ✅ 成立（补测试） |

结论：6 份报告是不同时间点快照，3 条已修复、1 条误报（intelligent_router 非死代码）。
本次聚焦真正成立且小而可测的条目。

---

## 二、已实施优化（5 项）

### 1. [P0] bash_lingxi 对齐 bash.py 安全基线
- `bash_lingxi.py`：新增 `_DEFAULT_LINGXI_BLOCKED`，导入 bash.py 同源 `_ALWAYS_BLOCKED` + `_BLOCKED_BASE_COMMANDS` 合并为默认黑名单
- 构造函数：`blocked_commands` 为**追加**而非替换（防调用方清空安全边界）
- `bash_tools.py`：`_bash_lingxi_handler` 补 sensitive_path_gate（与 `_bash_handler` 完全对齐）
- `coding.py`：移除 "no restrictions" 初始化，更新注释

### 2. [P0] guard 异常 fail-closed
- `tool_pipeline.py`：guard 抛异常由"视为 abstain 放行"改为"deny + 记录原因"
- 安全优先：误拦可人工放行，误放不可逆

### 3. [P1] GLMModel 枚举补 glm-5.3-flash
- `intelligent_router.py`：`GLM_5_3_FLASH = "glm-5.3-flash"`，成本倍数 1.5
- 向后兼容，不改变 `_choose_model` 既有路由语义

### 4. [P1] MCP fallback 判据收紧（隐藏旁路）
- `tool_executor.py`：仅当错误确属「本地工具未注册」(`Tool not found:` / `not registered`)才 fallback MCP
- 此前权限拒绝等含 "not found" 文案的错误会走 MCP 通道绕过 ToolPipeline 权限检查

### 5. [P1] LSP 能力补测试（验证非悬空）
- 新增 `tests/test_lsp_tools.py`：4 用例（未知命令/空命令/无 server 降级/有 provider 路由）
- 新增 `tests/test_bash_lingxi_security.py`：4 用例（默认黑名单/危险命令拦截/追加语义/安全命令放行）
- 修改 `tests/test_tool_pipeline.py`：guard 异常测试反转（abstain→fail-closed）
- 修改 `tests/test_intelligent_router.py`：补 glm-5.3-flash 枚举/成本测试

---

## 三、验证结果

| 测试集 | 结果 |
|--------|------|
| tests/test_tool_pipeline.py | 24 passed |
| tests/test_bash_lingxi_security.py | 4 passed（新增） |
| tests/test_lsp_tools.py | 4 passed（新增） |
| tests/test_intelligent_router.py | 全绿（含新增） |
| tests/test_lsp_registry.py | 19 passed |
| tests/test_strict.py | 28 passed |
| tests/test_t0_wiring.py | 26 passed, 1 skipped |
| tests/test_wiring_gate.py | 4 passed |
| tests/test_t3_capability_seam.py | 11 passed |
| tests/test_coding.py | 48 passed, 1 skipped（4 git 环境 error + 1 既有 /tmp 路径策略失败，均与本次无关） |

## 四、未实施（需独立提案的大项）

1. **工具执行主链统一**（P0，codex 建议）：read 快路径/stream/非 stream/MCP/subagent 收敛到唯一 ToolPipeline——改动面大，单独立项
2. **session rewind/快照回滚**（P1）：checkpoint 已有，需加回滚命令
3. **强类型工具结果协议**（P1）：ToolCallResult/ToolError 替代 JSON 字符串判断
4. **MCP Provider Manager**（P2）：连接池/冲突策略/schema cache
5. **benchmark 自证**（建议灵元2.0 首任务）：SWE-bench Lite 实测

## 五、遗留风险说明

- git 相关 4 个测试 error 是沙箱 git 环境问题（netns 隔离 + /dev/urandom EACCES），非代码回归
- `test_edit_with_syntax_error_blocked` 失败是测试用 /tmp 路径超出基础目录（既有策略）
- 超时线程无法强杀仍是已知局限（daemon 线程 + join，bash.py 自身超时互为兜底）
