# 双会话改动对账与真实状态清单

- 日期: 2026-09-21
- 作者: 灵克 (lingclaude)
- 性质: 工作区对账——「前缀缓存优化会话」（本会话）与「方案C v4 会话」（commit 1cb8208）产物核实
- 背景: 并行会话各自落地优化后，出现两份互相矛盾的审计清单。本清单以 **git diff / 文件实测** 为唯一依据，逐条对账。

## 1. 核心结论

**两会话是互补关系，不是覆盖关系**：

| | 本会话（前缀缓存优化） | 方案C v4 会话（1cb8208，13:28 提交） |
|---|---|---|
| 关注层 | **provider/请求构造层**（SSE 解析、system prompt 拆分、历史字节稳定） | **插件治理层**（生命周期状态机、配额窗口、修复卡、seam 订阅） |
| epoch 概念 | `_history_epoch`（wiring 槽位，历史字节代数） | `plugin_lifecycle` 的「epoch 依赖指纹」（插件依赖版本） |
| 修的 bug | usage 尾帧丢弃、每轮脱敏重算、动态 system 炸前缀 | E7 门禁抓获的 hot_swap 回滚、配额 defer/stale |

**但两会话在同一批文件上有交叠**（hooks.py / scheduler.py / seam.py / model_call.py），且方案C 会话在 13:28 提交时**覆盖了部分本会话的未提交改动**（model_call.py / system_prompt_builder.py 的中间态被其 commit 带走或冲淡）——这是两份审计清单互相矛盾的根源。

## 2. 逐条对账（以文件实测为准）

### 2.1 前缀缓存优化（本会话）——**全部在树中，已验证**

| 项 | 证据 | 状态 |
|----|------|------|
| usage 尾帧解析（cached_tokens） | `openai_provider.py` L295-309（git diff 在案）；探针实测第二轮 `cached_tokens=1600` | ✅ 已修+端到端验证 |
| `_messages()` 每轮重算 | 修复在 `query_engine_turn_mixin.py`（发送透传）+ `session_persist.py`（恢复路径）+ 写入点脱敏——**不在** `model_call.py:479`（该行实测是 MV-1 校验） | ✅ 已修（审计清单指错文件） |
| system prompt 静态化 + 动态 tail-append | `system_prompt_builder.py`（`build_dynamic_system_suffix` 拆分）+ `query_engine_turn_mixin.py`（`_build_dynamic_suffix`） | ✅ 已修，4 项验证通过 |
| `_history_epoch` | `wiring.py:348-351`（58 项）+ `l5_audit.py`（L1/L2）+ `query_engine.py`（reset）；**不叫 cache_epoch** | ✅ 已修，真实引擎验证 0→1 |
| wiring 断言同步 | `tests/test_p2a_wiring_manifest.py`（57→58，16 passed） | ✅ 已修 |

### 2.2 方案C v4（1cb8208）——**独立模块，24 测试全绿**

| 项 | 文件 | 状态 |
|----|------|------|
| seam 订阅广播 | `lingclaude/core/seam.py` | ✅ 已提交 |
| 生命周期状态机 + epoch 指纹 + 蓝绿 hot_swap | `lingclaude/core/plugin_lifecycle.py` | ✅ 已提交（**与本会话 `_history_epoch` 是不同机制，勿混淆**） |
| 配额窗口治理 | `lingclaude/model/quota_governance.py`（已接 `task_router.record_error/success`） | ✅ 已提交 |
| 上下文引擎（never-slice） | `lingclaude/core/context_engine.py` | ✅ 已提交 |
| 三态修复卡 | `lingclaude/core/repair_card.py` | ✅ 已提交 |
| 测试 | `tests/test_plan_c_v4.py` 24 passed（本机实测） | ✅ |

### 2.3 被引用但**不存在**的产物（审计清单幻觉项）

| 清单引用 | 实测 |
|----------|------|
| `bench/task27_atomcode_bridge.py`（全盘 find） | ❌ 不存在 |
| `bench/plan_c_atomcode_verify.md` | ❌ 不存在 |
| `PLAN_C_V4_BLUEPRINT.md` | ❌ 不存在 |
| `context_engine.py` 的 `cache_epoch`/`_monotonic_epoch()` | ❌ grep 零命中（1cb8208 的 context_engine 是 never-slice，不是 epoch） |
| `system_prompt_builder.py:120` 的 `cache_epoch={...}` | ❌ 不存在（该文件此行是本会话的拆分注释） |
| 全仓 `message_cache` 变量 | ❌ 零命中 |
| `model_call.py:541` 的 `if not content: continue` 在 usage 之前 | ❌ 行号与内容对不上（该区域是 finish 事件消费） |

**判定**：那份「10 项核实清单」的 #1/#2/#4/#5/#6 引用了本树不存在的行号与符号，#7-#10 的文件引用部分为幻觉。其唯一可靠的实情是「两会话改动有交叠」——但结论（“两个 P0 没修”）**错误**，实际两处 P0 都已修且有测试/探针证据。

## 3. 遗留与风险

1. **模型行号漂移**：审计清单的行号像是从**另一个工作树**（或另一会话的中间态）读出来的。两会话若在同一工作目录并行，存在**未提交改动互相覆盖**的真实风险——本会话 model_call.py 曾被同时编辑（探针期间 diff stat 含 `lingclaude/cli/commands.py` 等非本会话改动）。
2. **`_history_epoch` vs 插件 epoch**：两个「epoch」语义不同（历史字节代数 vs 依赖指纹），后续审计文档必须显式区分，防止再次混淆。
3. **配额治理与本会话硬配额熔断的关系**：1cb8208 的 `quota_governance`（QuotaWindowPool）与本会话修的 `_hard_quota_cooldown_seconds`（task_router）目标重叠——**两套配额机制并存**，需要后续收敛为单一事实源（建议：task_router 熔断保持快速路径，quota_governance 做窗口记账，task_router 消费其结论）。
4. **未提交改动堆积**：当前工作树 24 文件 +423/-32 未提交（含两会话产物），建议按「缓存优化」与「方案C 交叠文件」分批提交，避免再次互相冲淡。

## 4. 建议动作

1. 本清单落盘后，立即分批提交工作区（先缓存优化五文件，再交叠文件）。
2. 后续任何「审计清单」必须附 **git diff 哈希或行号+内容双证**，仅行号不作数（本次事故的直接教训）。
3. 将 §2.3 的幻觉项回写进返审触发器（`scripts/self_audit_trigger.py`）的守卫样本——「报告引用不存在的符号」是典型的账实不符。

---

## 5. 第三轮核实（2026-09-21 追记）：外部修正报告的复核对账

用户转来一份修正报告，称「P0-1 半修（`llm_stream_diagnostics` 开启时 usage 仍被 continue 跳过）、
P0-2 三个新函数（`_extract_transient_segments`/`_build_stable_baseline_messages`/`_inject_history_baseline`）、
P1 `_history_epoch` 在 `layered_memory.py:322/325/380`」。逐条实测：

| 报告断言 | 实测 | 判定 |
|----------|------|------|
| `llm_stream_diagnostics` / `_parse_usage` 存在，P0-1「半修」 | 全仓 grep **零命中**（model_call.py 与全仓均无该开关/方法） | ❌ 幻觉 |
| P0-2 三个新函数完整存在 | 全仓 grep **零命中**；P0-2 实际实现是 `build_dynamic_system_suffix`（system_prompt_builder.py）+ `_build_dynamic_suffix`（query_engine_turn_mixin.py） | ❌ 张冠李戴 |
| P1 `_history_epoch` 在 layered_memory.py:322/325/380 | layered_memory.py grep `_history_epoch`/`_monotonic` **零命中**；实际在 wiring.py:348-351（槽位）+ l5_audit.py（L1/L2 递增）+ query_engine.py（reset） | ❌ 行号文件全错 |
| P0-1「bug 在 diagnostics 开启时存在，关闭时不触发」 | 前提（开关存在）不成立，整个命题坍塌 | ❌ 同上 |

**结论**：该修正报告描述的是一个**本树上不存在的代码形态**（diagnostics 开关、三函数、layered_memory epoch），
其「P0-2/P1 已修」的结论碰巧正确，但引用的全部证据（函数名/行号/文件）均无法在本树定位。
真实状态以本文件 §2 为准：

- **P0-1（usage 尾帧）**：完整修复，随 `6f34d5e` 提交（openai_provider.py usage 解析前移至 choices 守卫之前，cached_tokens 可观测），探针端到端实证 `cached_tokens=1600`。不存在「diagnostics 半修」问题——开关本身不存在。
- **P0-2（前缀稳定化）**：完整修复，随 `6f34d5e`（拆分 tail-append + 写入点脱敏 + 发送透传），4 项验证通过。
- **P1（history_epoch）**：完整修复，随 `6f34d5e`（wiring 槽位 + L1/L2/reset 三递增点 + YAML manifest 同步 + 测试断言 57→58 同步），真实引擎 0→1 验证通过。

**教训追加**：两轮外部审计报告（§2.3 与本节）引用了两套**不同的不存在代码形态**——说明有会话在
幻觉代码上做审计，或审计的是另一棵完全不同的树。凡引用行号/符号的核实结论，必须先跑
`grep -rn "符号"` 全仓确认实体存在，再谈行号对错。本条已具备进入 self_audit_trigger 守卫样本的条件。
