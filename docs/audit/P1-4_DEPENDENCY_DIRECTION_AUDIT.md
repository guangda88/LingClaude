# P1-4 审计: core→engine 依赖方向核查（灵元 R1 尺子）

日期: 2026-09-14
状态: 审计完成，结论"无需下沉"

## 背景

文档 `LINGYUAN_AUDIT_LINGCLAUDE_v2.md` 提出 core/ 直接 import engine/ 共 7 处，
判断为"主干倒装插片"，建议下沉 runtime.py。本审计逐处实测性质。

## 7 处 import 实测定性

| # | 文件:行 | import | 真实用途 | 定性 |
|---|---------|--------|----------|------|
| 1 | core/tool_call_executor.py:78 | engine.verification_gate.WRITE_SCOPED_TOOLS | 写工具守卫常量（并行冲突检测） | 守卫常量消费 |
| 2 | core/tool_executor.py:179 | engine.mcp_proxy | MCP 服务代理单例（call_tool） | 服务代理消费 |
| 3 | core/wiring.py:214 | engine.tool_router.create_default_router | 装配工厂（wiring 本就是装配层） | 装配工厂消费 |
| 4 | core/mcp_tools.py:11 | engine.tool_router.ToolRouter | 类型 + MAX_TOOLS_PER_REQUEST 常量 | 类型/常量消费 |
| 5 | core/mcp_tools.py:12 | engine.mcp_proxy | MCP 服务代理（list_all_tools/list_servers） | 服务代理消费 |
| 6 | core/mcp_tools.py:64 | engine.tools.ToolDefinition | 数据类型注解 | 类型消费 |
| 7 | core/mcp_tools.py:122 | engine.mcp_client.discover_and_register | MCP tools/list 发现工厂 | 工厂消费 |

## 关键发现: 双向依赖（非单向叶子）

实测 engine/ 侧 10+ 处反向 import core：

```
engine/git.py        → core.types.Result
engine/file_edit.py  → core.types.Result
engine/mcp_proxy.py  → core.types.Result
engine/verification_gate.py → core.config.VerificationConfig
engine/tool_pipeline.py     → core.types (Result/ToolError/...)
engine/mcp_oauth.py  → core.types.Result
...
```

**结论**: core↔engine 是服务编排层与工具实现层的双向耦合，不是"主干零依赖插片"的单向结构。7 处 import 全部是**服务/常量/类型消费**，不是"主干调用插片实现细节"的倒装。

## 为什么"下沉 runtime.py"是错误方向

1. **会引入循环 import**: mcp_proxy 本身 import core.types.Result，若 core 再 import runtime.py（内含 mcp_proxy），构成 core→engine→core 环
2. **无收益**: 这 7 处是消费者（core 编排时调用 engine 服务），不是"变化点"——`create_default_router`、`mcp_proxy` 的接口稳定，没有"改策略要动代码"的问题
3. **真正该下沉的已下沉**: 文档指出的 `engine/coding.py` 10 mixin 倒装（LLM 直调工具）已在 `058f750` 前修掉（coding.py 542 行单类 CodingRuntime）

## 灵元 R1 的务实落法

- **保留**: 7 处服务/常量/类型消费（合法，非倒装）
- **可迁移（未来）**: `WRITE_SCOPED_TOOLS` 若需热更（如动态增删写工具），可挪入 `core/policies/*.yaml` 走 PolicyLoader——当前无此需求，保留原位
- **真正的主干过厚**在别处: 4 个状态机并存（dementia/cognitive/governance/state_store）、策略硬编码（已由 P0-1~P0-4 外置）、provider if/elif（已由 P1-3 注册表化）

## 验证

- tests/test_tool_router.py + test_tool_pipeline.py + test_tool_result_protocol.py: 92 passed
- tests/test_model.py::TestCreateProvider: 9 passed（P1-3 无破坏）
- tests/test_provider_registry.py: 6 passed（P1-3 新测试）
