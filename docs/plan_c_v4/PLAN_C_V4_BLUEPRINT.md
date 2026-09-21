# 方案C v4 P0-P2 实施蓝图（落盘版）

> 状态：源码与测试已落盘并全绿。本蓝图记录**实际落盘的架构**（非计划稿）。
> 证据链：cbff31f（GLM 1310 撞墙）/ vendored cordis fiber.ts / hermes native_compaction.py:226 / Pi chord
> 验证：`python -m pytest tests/test_plan_c_v4.py -q` → 24 passed

## 落盘清单

| # | 模块 | 路径 | 职责 | 测试 |
|---|---|---|---|---|
| 1 | seam 订阅钩子 | `lingclaude/core/seam.py` | 缝变更广播（register/unregister 锁外、fail-open、no-op 不播） | 4 用例 |
| 2 | 插片生命周期 | `lingclaude/core/plugin_lifecycle.py` | 六态状态机 + epoch 指纹 + disposer 逆序 + 蓝绿 hot_swap | 5 用例 |
| 3 | 配额窗口直查 | `lingclaude/model/quota_governance.py` | 问窗口不猜：错误文本→QuotaWindow→defer/allow 决策 | 4 用例 |
| 4 | 上下文引擎 | `lingclaude/core/context_engine.py` | 摘要框定单一来源 + never-slice + 反向解析 | 4 用例 |
| 5 | 修复卡 | `lingclaude/core/repair_card.py` | 写失败结构化追踪，无证据不得 RESOLVED（fail-closed） | 3 用例 |
| 6 | hooks 扩容 | `lingclaude/core/hooks.py` | SESSION_RESUME/PRE/POST_HOT_SWAP + resumed 字段 + storage_hint | 4 用例 |
| 7 | 插片测试 | `tests/test_plan_c_v4.py` | 全部插片的离线可重复测试 | 24 用例 |

## 四处接线

| 接线 | 位置 | 语义 |
|---|---|---|
| record_error → 窗口 | `task_router.py:559-566` | 硬配额错误顺带提取 QuotaWindow（防御式，失败不影响熔断主路径） |
| record_success → 清窗 | `task_router.py:543-548` | 真实成功推翻窗口观察（观察 vs 证据分层） |
| resume → 钩子 | `cli/commands.py:605-620` | SESSION_RESUME 在恢复成功后触发（防御式，钩子失败不阻断恢复） |
| lifecycle → 钩子 | `plugin_lifecycle.py` `_fire_hook` | PRE/POST_HOT_SWAP 治理观测点（可选注入 hooks，None=静默） |

## 设计裁决（关键 4 条）

1. **hot_swap 绕过 epoch 守卫**（E7 门禁当场抓获的真 bug）：refresh() 的
   「指纹未变 → no-op」守卫会吞掉 factory 替换——显式切换必须直接卸旧+激活新，
   回滚路径同理。测试 `test_hot_swap_success_bypasses_epoch_guard` 锚定。
2. **解析单源**：quota_governance 复用 task_router._HARD_QUOTA_RE +
   retry.is_hard_quota_error，不造第二套解析（两套解析必然漂移）。
3. **观察 vs 证据分层**：配额窗口是「观察」（错误文本推断），真实调用成功是
   「证据」——record_success 清窗口；decide_from_windows 的 stale=True 表示
   「宽限期内放行、下次调用即验证」。
4. **fail-open vs fail-closed 分层**：seam 订阅者异常/钩子异常/窗口记录失败
   → fail-open（只记日志，不反噬主路径）；repair_card 无证据 resolve →
   fail-closed（抛 RepairEvidenceError）。治理动作（hot_swap）fail-open，
   结账动作（repair）fail-closed——各安其位。

## 边界纪律

- plugin_lifecycle 只管 lc 内部缝图；HTTP 端点探活是 provider_probe 职责（TTL）。
- quota_governance 只做窗口记录与决策，不发探活请求。
- context_engine 只做框定与判定，压缩算法留在 context_compression。
