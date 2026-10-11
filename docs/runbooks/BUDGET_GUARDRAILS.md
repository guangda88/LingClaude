# BUDGET_GUARDRAILS — 预算/预警/硬停统一口径（跨线程对齐版）

> 落盘: 2026-10-08。发起缘由: 巨任务「长/多会话进化规划」线程与面板线程需统一术语。
> 立规依据: 全部阈值/触发点经本轮源码实测（锚点见各条），实证锚 = LONGTASK_SOP 头部
> 「input 22.7M 触发 budget pause」案例。改动任一机制时必须回改本文件（口径漂移事故源）。

## 〇、一页速查卡（TL;DR）

| 口语词 | 唯一规范名 | 阈值 | 计什么 | 谁执行 | 动作 |
|---|---|---|---|---|---|
| "压缩了" | **L1 压缩层** | 窗口 400K（=0.8×500K） | **当前消息窗口** tokens（非累计） | core/query_engine_turn_mixin.py:393 请求前预检 + core/tool_executor.py:246 轮后 | 压缩旧消息继续跑，无感 |
| "预算预警" | **L3 接力预警档** | PAUSE 的 75% / 90% | 见 L2 基数 | core/session_budget_gate.py:106 `warn_injection`（turn 入口注入，每档一次性） | 75%=立即落盘状态；90%=立即收尾交接，禁新探索 |
| "预算暂停" | **L2 PAUSE 闸** | input 20M / output 1M / tool_calls 2000 / model_calls 1200（session_budget_policy.yaml:28-33） | **进程内会话累计**（跨重启清零） | core/session_budget_gate.py:155 `check_pause`（仅"即将发起模型请求"前调） | 挂起模型请求+人读报告+遗言轮资格判定；斜杠命令/本地工具不受闸影响 |
| "硬停" | **L5 STOP 标签** | 累计 in+out 500K（config.yaml:3） | provider 路径真实 usage 累计 | core/submission.py:159 → `StopReason.MAX_BUDGET_REACHED` | **仅打标签，不拦截**（全仓零消费方，2026-10-08 实测）——已定案保留+声明，见 §五-D1 |
| "遗言轮" | **L4 遗言轮** | PAUSE 前一次，用后即焚 | 资格 = 有交接物（relay_marker 或 pending todos）且本进程未用过 | core/submission.py:242 `_attempt_last_rites`（gate.try_last_rites 判资格） | 独立小上下文（无 tools/免重试/≤200K input）写 handover_lastrites.md |
| "行为急停" | **L6 认知闸** | 痴呆指数/连续失败 | 行为信号 | core/submission.py:153-158 | `CONSECUTIVE_FAILURE` 硬中断，唯一真"硬停" |

**一句话**：窗口安全靠 L1（400K 自动压缩）；会话预算靠 L2+L3+L4（8M 预警→20M 挂起→挂起前遗言）；
500K 只是个死标签（L5）；跨会话治理靠 LONGTASK_SOP + 编排器（预算计数**跨重启清零**，见 §四）。

## 一、基数三口径（混用即事故，必分清）

| 口径 | 定义 | 属于 |
|---|---|---|
| **窗口** | `_estimate_message_tokens(self._messages)`——当前上下文里还有多少 | L1 压缩层 |
| **进程累计** | `BudgetTracker` 本进程累加，重启归零（session_budget_gate.py:102 明示） | L2 PAUSE / L3 预警 / policy 全维度 |
| **provider 累计** | `_usage` 真实 usage 累计（add_turn 仅本地 fallback 路径，submission.py:143-150 修复注） | L5 标签 |

## 二、触发矩阵（fail-open 是默认，唯一例外是 L2 PAUSE）

| 机制 | 触发时机 | 拦不拦请求 | 故障语义 |
|---|---|---|---|
| L1 压缩 | 每次模型请求前 + 每轮后 | 不拦（压缩后继续） | 估算失败→不压缩（可能撑大窗口） |
| L3 预警 | turn 入口，每语义轮至多一次（防 submission 层逐请求拼接放大，codex Q1 裁定） | 不拦 | fail-open：异常静默放行 |
| L2 PAUSE | 仅"即将发起模型请求"前 | **拦**（挂起，等用户裁决） | 评估故障 fail-open 放行；记账故障绝不反噬 |
| L4 遗言 | PAUSE 报告生成后 | 不拦（本来就是最后一发） | 失败静默降级，声明"现场仅存 transcript"（submission.py:307） |
| L5 标签 | turn 完成后 | 不拦（无消费方） | — |
| L6 认知 | turn 完成后 | **拦**（中断） | — |

## 三、数值关系总图（单会话生命周期）

```
0 ──── 400K(L1窗口压缩,循环往复) ──── 窗口≈≤400K 恒成立
        累计侧: input ──8M──► L3-75%档 ──► L3-90%档 ──20M──► L2 PAUSE(挂起)
                output ─400K─► warn ─1M──► PAUSE
                tool_calls ─800─► warn ─2000─► PAUSE        model_calls ─400─► warn ─1200─► PAUSE
        累计 in+out ────500K────► L5 标签(死,无消费方) ※比例上先于 8M 档到达但无动作
        PAUSE → L4 遗言轮(一次) → 等用户/编排器接力
```

## 四、跨会话（多会话接力）口径——本文件与 LONGTASK_SOP 的衔接处

1. **预算计数不跨会话**：新会话 = 新 BudgetTracker（gate.reset()，submission 启动时触发）。
   上一会话烧了多少，新会话**不感知、不继承**——"多会话总消费"目前**无全局账户**。
   现有唯一兜底 = 编排器对同一任务最多重试 3 次（LONGTASK_SOP §六）。
2. **跨会话传递的只有交接物**（不是预算）：`waiting_relay` + `resume_hint` +
   遗言轮产物 `handover_lastrites.md`（消费后重命名防重放，submission.py:317）。
   R3-B 恢复引导（c9814f4）在 resume 时自动注入。
3. **给进化规划线程的对接点**：若做"长/多会话总预算"，加法在编排器侧（它掌握队列与
   重启决策），不要动进程内 tracker 语义；全局账户落盘建议走
   `/tmp/<task>_state/audit/`（对账目录已有 todo_log.jsonl 先例）。

## 五、已知口径偏差与既定处置（引用者必读）

- **D1｜"500K 硬停"名不副实（L5）**：全仓 grep 证实 `MAX_BUDGET_REACHED` 零消费方，
  它只是 turn 后的 StopReason 标签。实证佐证：SOP 案例 input 22.7M 正常到达 20M PAUSE
  （若 500K 真拦截，22.7M 不可能出现）。**对外表述一律说"20M PAUSE 是会话硬上限"，
  禁说"500K 硬停"**。
  → **已定案（2026-10-08 用户裁决）：C) 保留标签 + 本文档声明**。标签留作诊断/观测
  （provider 路径 500K 累计可见），不引入拦截语义。若未来重开 D1 改选 A/B，
  须先回改本文档 §〇/§五，并联动 D3 input 档复核。
- **D2｜L3 档位样本量=1**：0.75/0.90 系单案例（22.7M 事故）外推，gate.py:105 自注
  "待长任务 trace 复算后调优"。
- **D3｜policy input 档阈值虚高风险**：session_budget_policy.yaml:31 注释声称管"分项偏斜"，
  但 input 8M/20M 远超 L5 名义 500K——L5 失效后该档反而是**唯一** input 累计防线，
  无虚设问题；若未来重开 D1 改 B（500K 真拦截），则 input 档需整体重调（联动项；
  D1 已定 C，此联动暂不激活）。
- **D4｜"长会话"与"多会话"必须分开表述**：单会话上限 = L2 PAUSE（≈20M input 级）；
  长任务总时长/总量 = 跨会话接力治理（SOP），无单点上限。混称"硬停"是历史事故源。

## 六、行为规范（agent 侧，与 LONGTASK_SOP §四预停协议对接）

| 收到信号 | 规范动作 |
|---|---|
| L3-75% 注入（检查点档） | 无条件落盘：todo 状态 + handover 增量 + 证据文件（不再新开探索） |
| L3-90% 注入（交接档） | 立即收尾：handover 置 waiting_relay + resume_hint，FINAL_REPORT 半成品也写 |
| L2 PAUSE 报告 | 若获遗言轮：只写交接文本，不做任何其他事；无遗言轮：依赖平时已落盘状态（kill-safe 纪律） |
| 看到压缩摘要 | 远期上下文已折叠——重读关键文件，勿凭记忆断言 |

## 七、修订记录

- 2026-10-08 初版（灵克）：六层机制实测口径固化；D1 之前轮误判"500K 硬停导致 8M 死配置"
  已纠正——500K 标签无消费方不拦截，8M 档是活的（SOP 22.7M 实证）。
- 2026-10-08 D1 定案（用户裁决）：**C) 保留+声明**——500K 标签留作诊断观测，不引入拦截；
  D3 联动项随之暂不激活。重开 D1 须先回改本文档（口径漂移事故源纪律）。
