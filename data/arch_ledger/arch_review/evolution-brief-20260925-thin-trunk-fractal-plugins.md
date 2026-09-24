# 灵克架构演进讨论纪要：极薄主干 × 分形插片

- 日期：2026-09-25
- 整理：灵克（lingclaude）主会话
- 性质：多轮人机架构讨论的蒸馏稿，供外部 Agent（cc / codex / opencode / crush / atomcode）评审
- 关联卷宗：
  - `data/arch_ledger/arch_review/review-20260924-m6-usage-distribution-proposal.json`（M6 审计口径，approved）
  - `data/arch_ledger/arch_review/review-20260925-thin-trunk-2t3a-kernel-proposal.json`（2T3A 内核提案，proposed，HEAD 209392f）

---

## 0. 教义

**除主干，一切皆插片。** 插片生命周期本身也是插片（递归治理，出口收敛于事件账本）。

L1 主干 = **2T3A 状态机主架构**：三原语 **records / events / transition**（解码：2T = 2 张表 records+events，3A = 3 个动作 create/transition/query）。对标 cordis 微内核，但比它更薄——不搬 vendor/service 注册面，只取三原语 + 挂载器。

## 1. 诊断起点：有路无车

M6 卷宗的核心发现（全部实测）：

- 1,010 次工具调用仅 5 工具（read 750 / bash 238 / glob 10 / grep 6 / write 6），注册面 26 实现
- 12 MCP server 仅 2 有流量（ling-term-mcp 246 次 / lc-guard 6 次），10 个零调用
- 26 skill 宿主账 0 触发；tool_events 全量 mcp_* 调用 = 0
- 5 个 core 零调用孤儿模块（l7 三层+bridge/facade ≈1,100-1,250 行、lingmemory_memstore_bridge 137 行、comfort_zone、goal_receipt）
- fan_out_scheduler 337 行，`LINGCLAUDE_FAN_OUT` 门默认关
- 审计盲区 4 条机制性原因：J1-J5/M1-M6 全测结构合规无使用率；插片只有建造期验收缺运营期仪表；M6 只数实现数不数调用分布；激励不对称（修路有台账、开车无人考核）
- 铁律 4（剪刀）落地至今真实回收 0 次

## 2. 三层欠账模型（本轮讨论的完整诊断）

```
建而不通（装配欠账）→ 4 条 L1 空壳缝（TRANSPORT/MEMORY/GOVERNANCE/SELF_OPT：在册、声明可替换、register=0、实体焊死 core/）
                    → 2 个空库（lingbus.db / lingmemory.db 均 0 张表，接线在、写入端从未投产）
                    → 空目录 agent_dispatch/、fan_out 门默认关、message_signer 0 使用点
通而不用（使用欠账）→ 26 工具 / 12 server / 26 skill / sub_agent / R8 喊话零转化
用而不记（记账欠账）→ audit_log.jsonl 2026-08-15 断供、proxy3 request_log 断供、
                    token_monitor 记量不记归属、gateway 17 条 run 16 条 schema 解析失败
```

core/ 27,964 行，按主干标尺二分后可清 ~7,600-7,900 行（删孤儿 ~1,100-1,400 + 迁实体 ~3,800 + 删 .bak 2,743），主干瘦到 ~20K 再进一步到 1,500-1,800 行级。

**三级责任边界**（三级守卫各管一段）：L1 必须连接（永不下放）；L2 可缺席可降级（缺省对象）；L3 可裁撤（铁律 4 领地）。

## 3. L1 主干五件套（实测在仓）

| 组件 | 职责 | 状态 |
|---|---|---|
| `core/state_store.py` | 2T3A 三原语：StateBackend 协议 + JsonFileBackend + LingYiBackend（asyncpg，ly_state_records upsert + ly_state_events 追加）+ 双写三切片迁移协议 + best-effort 主干属性 | ✅ 生产消费中（memory_engine / task_aggregation / lifecycle_mixin 三点） |
| `core/seam.py` | 挂载器：registry + inject 依赖声明 + 六域前缀（gov/agent/cap/hw/tool/model）+ `_seam_epoch` 指纹 + `subscribe_change` 变更广播 | ✅ 在产 |
| `core/plugin_lifecycle.py` | 插片生命周期：fiber/epoch/hot_swap 蓝绿（候选构建→旧代续服→原子换→失败回退，零不可用窗口）/ disposers 逆序清理 / detach 依赖方自动 dispose | ✅ 机制深度在产（seam 钩子/hooks 蓝绿钩子/TUI 渲染对位），LifecycleManager 实例直接装配点未找到——「通电」存疑 |
| `core/types.py` | 词汇表：StopReason 五态 = transition 的 to_state 词表，turn = record | ✅ |
| `core/hooks.py` | 生命周期观测钩子（含 PRE/POST_HOT_SWAP） | ✅ |

三层循环降格为插片：L1 会话循环（cli/repl.py:1354）、L2 轮次生命周期（core/submission.py:45 submit）、L3 工具轮循环（core/model_call.py:193）——都是 transition 的编排，可换装（fan_out「启用 or 回收」二选一困境由此解为双插片并存按任务换装）。

## 4. 热插拔七阶段 × 现状

| 阶段 | 机制 | 状态 |
|---|---|---|
| ① 登记 | manifest 门：先跑插件测试、通过才准入；出生登记五字段（职责类型/待命事件清单/观察期指标/豁免规则/缝 key） | ✅ T0 在产；⚠ 五字段登记表是设计未落地 |
| ② 接缝 | SeamRegistry.register + inject + epoch 指纹 | ✅ 在产 |
| ③ 运行 | 事件驱动 refresh：缝变更只刷受影响 fiber，无轮询；依赖消失自动降级（cordis Gap#1 同构已实现，缺与 ly_state_events 对接） | ✅ 在产 |
| ④ 协调治理 | 递归治理：登记表=record、生命周期变迁=transition（写 ly_state_events）、治理者=唯一自指插片（state_query 重放事件账本自审，不再造一层） | ❌ 全案唯一真空白（自指闭合设计已定，未实施） |
| ⑤ 更改 | hot_swap 蓝绿 + epoch 没变不动作 | ✅ 在产 |
| ⑥ 卸载 | detach → disposers 逆序 → unregister → 依赖方自动 dispose | ✅ 在产 |
| ⑦ 退役 | fiber 终态归档 + 回收裁决入 2T3A 事件账（可回放） | ❌ 断链：退役动作无 2T3A 事件留痕，未与 M6/铁律 4 衔接 |

四道闸（主干完整性保证，全部已实测）：建造期 manifest 门 / 切换期蓝绿回退 / 故障期 L1-L2-L3 降级梯度 / 主干 best-effort。**唯一缺的闸：插片风暴熔断**（refresh_all 大面积 FAILED 的全局停机保护）。

## 5. 插片辨别框架（回答「哪些真有用、哪些该用没用起来、哪些真没用」）

**价值 = 问题发生率 × 无替代程度 × 当前可达性**（调用数据只覆盖第三项）。

职责三型（各型正常态不同，统一调用频次指标本身是误伤）：

| 型 | 定义 | 正常态 | 正确指标 |
|---|---|---|---|
| 常备型 routine | 日常路径 | 高频 | 调用频次 |
| 待命型 standby | 灾备/守卫，事件驱动 | **零调用=健康** | 事件覆盖率（事件发生过时它触发的比例） |
| 条件型 conditional | 特定场景 | 间歇 | 条件触发率 |

四分类决策树：①真常用→keep ②该用没用起来→促活（四病因：不知道存在/知道但选了别人/入口太深没接线/场景稀少；促活优先于回收，观察期 1-2 周期） ③待命正常→keep+豁免调用口径 ④真没用→回收候选。数据缺失→不判（硬闸，缺自报账标 unverified 不标 0）。

最危险一类：守卫失职（事件发生过、守卫没触发）——调用口径想回收它（错）、结构口径放行它（也错），只有事件覆盖率能抓到。

## 6. 增量侧：5+1 计划新插片（批准在案）

L1 coding_wiring 外移（coding_wiring.py 已存在，落地待核）/ L2 per-agent 熔断+5 家 manifest 化 / L4 cap_browser/cap_infer/cap_inspect / L5 HAL 白皮书+网络存储独立登记 / gov rss_watchdog+free_ram_gate（实体已存在，计划状态过时嫌疑）。

**出生判据**：注册即带调用探针 + 登记五字段 + 30d 冷启动观察期按职责型选指标——新插片出生即打点，增量即刻止损。

## 7. 与 M6 的关系（标尺-刻度）

M6 量「用不用」（存量欠账审计口径，三口径合议：静态+运行时+调用分布）；2T3A 定「该是什么」（目标架构标尺）。两线在清账迁移处汇合：**15 个状态模块双写迁移 = MEMORY/GOVERNANCE 实体迁缝 = 同一个动作**——每迁一个，core 薄一分，主干向 1,500 行级收敛。

## 8. 已识别的争议点 / 风险（供外部 Agent 重点评审）

1. **主干质量悖论**：主干越薄，单点瘫痪面越大；状态机需形式化验证级正确性，但验证基础设施要先于收缩存在
2. **热路径过缝开销**：插片间通信全走缝，模型调用→工具执行的热路径需要「宽缝」白名单
3. **Goodhart 侵蚀**：30d 零调用规则滋生防御性调用；探活调用必须不计入
4. **wiring/seam 双装配体系**：度量口径互相打架（cognitive_rhythm 陷阱），接缝自身插片化是终局
5. **plugin_lifecycle 未通电**：机制深度在产但 LifecycleManager 实例装配点未找到——是尚未接线还是已被绕过，需核
6. **多成员分账**：成员本地直连不经宿主账；12 灵字辈家族已是 AGENT 缝插件，插片化完成后自报账问题消解为 fiber 状态查询
7. **递归治理的自指闭合**：治理插片治理插片、治理者也被治理——出口收敛于事件账本重放，此设计是否成立
8. **cordis 对标**：只取三原语+挂载器是否遗漏了微内核必要能力（service 强一致联动已有同构；模型自省工具族 state_query 待建）

## 9. 待外部 Agent 评审的问题

1. 2T3A 三原语（records/events/transition）作为唯一主干原语，表达力是否充分？缺什么？
2. 插片风暴熔断闸的设计要点？
3. 递归治理自指闭合于事件账本，有无更优解？
4. 双写三切片迁移的回滚策略？
5. 形式化验证主干状态机的最小方案？
6. 「一切皆插片」的边界在哪？有没有不该插片化的东西？
