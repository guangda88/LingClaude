# LINGKERNEL_v1 #1 拆包方案 - dsh 6 包 spine 对位

**作者**: 灵克 (lingclaude) | **日期**: 2026-08-18 D1 | **状态**: 实施中
**用途**: 灵研 spec-review (D+4) 的对照文档; LACP 验收 (D+7) 材料

## 拆包映射 (dsh spine -> 灵克模块)

| dsh 包 | 职责 | 灵克模块 | 状态 | 行数 |
|--------|------|----------|------|------|
| `core/session` | append-only log + deriveMessages | `core/model_request_log.py` (新) + `core/session_store.py` (新) | ✅ D1 | 110 + 189 |
| `core/system-prompt` | prompt section 装配 | `core/message_builder.py` (D0) | ✅ D0 | 200 |
| `core/tools` | registry + 5 段 pipeline | `engine/tools.py` (D0) + `engine/tool_pipeline.py` (D0) | ✅ D0 | 130 + 200 |
| `core/agent` + `agent-loop` | turn driver | `core/query_engine.py` (瘦身目标 < 400) | 🔄 D1 进行中 | 2174 -> |
| `llm/llm` | adapter seam | `core/model_adapter.py` (新) | ✅ D1 | 120 |
| (横切) telemetry/audit | L5/degradation/behavior | `core/audit_collector.py` (新) | ✅ D1 | 130 |

依赖方向 (dsh 规则: 无环, loop 不依赖扩展):

```
query_engine (driver)
  ├──> model_adapter        (llm seam)
  ├──> session_store        (持久化)
  ├──> model_request_log    (MV-1 不变量)
  ├──> message_builder      (prompt 装配)
  ├──> audit_collector      (横切审计)
  └──> tool_pipeline        (经 engine.coding 间接)
tools/tool_pipeline: 不依赖任何 core 模块 (叶子)
message_builder: 仅依赖注入的 Protocol (叶子)
session_store/model_request_log: 叶子
```

## 与 dsh 的显式差异

| 差异点 | dsh | 灵克 | 原因 |
|--------|-----|------|------|
| log 内容 | SessionEventMap 全量事件 | ModelRequestEvent 快照式 | 灵研 spec 演进方向已注明: 快照先行, 重放式后继 |
| deriveMessages | 从增量事件重放 | 取最后快照 deepcopy | 接口签名冻结, 实现可演进 (灵研 D+4 review 点) |
| scope | Cordis ctx 显式 scope | 模块边界即 scope | Python 无框架层, 用 import 边界近似 |
| finalizeContent | registry 外层快照调用 | registry.finalize_result + pipeline 5 段 | 语义一致 |

## MV-1 不变量预留 (灵研 #5-spec 对接)

`model_request_log.py` 冻结两个接口:

```python
derive_model_messages(log, upto_seq) -> list[dict]
check_model_visible_invariant(log, seq, actual_messages) -> (bool, reason)
```

query_engine 接线后: 每次 `_call_model` 前 `log.append(...)`,
发完模型后 `check_model_visible_invariant(...)` -- 违反即 P0 告警。

## 测试账本

| 模块 | 测试文件 | 数量 |
|------|----------|------|
| tools (D0) | test_tools_v2.py | 15 |
| tool_pipeline (D0) | test_tool_pipeline.py | 24 |
| message_builder (D0) | test_message_builder.py | 19 |
| session_store (D1) | test_session_store.py | 9 |
| model_adapter (D1) | test_model_adapter.py | 13 |
| audit_collector (D1) | test_audit_collector.py | 13 |
| model_request_log (D2) | test_model_request_log.py | 11 |
| **合计新增** | | **104** |
| 全仓回归 | 2006 passed (D0 基线) | 待 D2 全量复跑 |

## 剩余步骤 (D2)

1. coding.py execute_tool 接线 ToolPipeline (调用侧)
2. query_engine 内部改调 session_store/model_adapter/audit_collector (瘦身)
3. _call_model 接 model_request_log (MV-1 上线)
4. 全量 pytest 回归

## D3 完成记录 (2026-08-19)

| 项 | 状态 | 验证 |
|----|------|------|
| coding.py 接线 ToolPipeline | ✅ D2 | 旧 53 测试全兼容 (错误消息格式保留 `[验证关卡]`/`[安全限制]` 前缀) |
| query_engine 注入 4 个 spine 模块 | ✅ D3 | test_mv1_wiring.py 7 测试 |
| `_call_model` MV-1 上线 | ✅ D3 | 发模型前 `log.append` -> 发完 `_assert_model_visible`; 篡改可检出 |
| `mv1_violations` 属性 | ✅ D3 | 供灵信 invariant 框架 / LACP 验收消费 |
| 全量回归 | ✅ | 2059 passed, 63 skipped, 0 failed |

query_engine 瘦身进度: 模块已全部就位并接线, 但原方法仍以内联为主 (driver 化收尾留 D4+,
因 2174 行原地替换风险高, 采用"新模块 + 接线点"渐进策略, 与"激进拆包"决策的折中:
新包全部当日落地, 内联代码逐步迁移)。

MV-1 上线路径:
    _call_model
      -> _log_model_request(prompt, messages, tools)   # append-only log
      -> provider.complete(...)
      -> _assert_model_visible(seq, messages)           # derive == actual?
      -> 违规 -> _mv1_violations + WARNING 日志

灵信对接点: `eng.mv1_violations` 元组 (L-a 层断言输入)。
灵安对接点: violations 持久化后入 audit trail (L10-D)。
