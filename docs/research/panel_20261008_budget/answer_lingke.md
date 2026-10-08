# 灵克方案稿：会话预算预警与接力恢复（R1/R2/R3）

> 出处：无人值守长任务治理（LONGTASK_SOP + relay 编排器已实盘验证）的 harness 层缺口。
> 复现实锤：本会话 22.7M/20M 硬挂起，无遗言轮次，状态只在内存。
> 插片范围：R1/R2/R3 行为规约评审 + 落地归属划定（lingclaude 侧 vs harness 侧）。

## 插片问题（请逐条给倾向与理由）

### Q1（R1 预告警注入）
`warn_lines()` 已是展示层 WARN 钩子（statusline / /budget 消费）。预告警是否升级为「模型可见」？两条路线：

A. **展示层就够了**——WARN 预算接近时，/budget 命令与状态栏可见，模型看不到，人看到后告知或 reset。改动零风险。
B. **模型可见**——在 turn 循环的「下一个模型请求前」，把 WARN 段作为系统注记拼进即将发出的请求（类似 R8 提示的注入方式）。改动点在 submission/check_pause 旁路，风险可控。

### Q2（R2 强制收尾轮）
若采纳 B 路线，98% 档是否强制「遗言轮」：即 `check_pause` 返回 pause 报告时，允许**一次**携带 handover 指令的请求通过闸（白名单一次），执行后必停？

- 倘若允许白名单请求，滥用风险（重复白名单请求变相绕闸）如何防？
- 与「恢复权在用户」红线是否冲突（自动执行 handover 是否算「静默降档」的变体）？

### Q3（R3 接力恢复自动化）
R3（新会话自动检测 waiting_relay 并注入 resume_hint）侦察发现**不在 lingclaude 源码域内**（SESSION_CONTEXT 组装在 harness 生成层）：
- A. 划归 harness 侧（提需求，非我方代码可落）
- B. lingclaude 侧可做**会话恢复引导钩子**：`--recover` 恢复会话时，若有 `waiting_relay` 标记（`/tmp` 或 state 目录），turn 循环首个 system 注记注入 resume_hint
- C. 编排器侧已兜底（session_relay_orchestrator.sh 已实盘：认领→注入 prompt→终点协议），R3 的增量价值仅剩「人手动开新会话」场景
  —— 你的裁定？

### Q4（优先级与节奏）
R1/R2 若都落，预警档位建议：85% WARN（模型可见）→ 98% ONE_SHOT_HANDOVER → 100% PAUSE。档位/阈值/顺序有无异议？

## 灵克倾向（待评审校准）

- Q1: B（模型可见），理由：22.7M 硬挂起复现证明「人看到也来不及救」；且 R8 提示注入已有成熟先例
- Q2: 采纳白名单一次 + 两个防滥用约束（见设计稿）
- Q3: B+C 组合（我方做恢复引导钩子，编排器兜底全自动场景）

## 设计稿细节（Q2 防滥用约束草案）

若 R2 白名单一次采纳：
1. 白名单请求**仅当**会话存在激活的 relay 标记或 todo 清单时放行（有真实交接物才给遗言轮）
2. 白名单**每进程生命周期一次**（用后即焚，/budget reset 也不恢复——防 reset 绕闸）
3. 白名单请求的产出（handover/relay 标记写入）经 relay_audit 对账兜底（已有机制）
- Q4: 85/98/100 三档，同意顺向

## 灵克侧已有地基（评审可核查）

- 闸层：`lingclaude/core/session_budget_gate.py`（check_pause 92-106、warn_lines 109-117，fail-open，暂停报告含 /budget reset 出口）
- 策略热更：PolicyLoader mtime watch（改 yaml 即生效不重启）
- 接力编排器：`scripts/session_relay_orchestrator.sh`（实盘演练通过）与队列模板 scripts/relay_queue.json
- 交接载体：`lingmemory/handover.py`（kill-safe，instant write）
- 复现实锤：本会话 input 22.7M/20M 触发硬挂起（预算 pause 无遗言轮，即本插片动机）
