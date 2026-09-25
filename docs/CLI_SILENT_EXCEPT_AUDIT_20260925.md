# CLI 静默 except 普查台账（2026-09-25）

> 来源：toolbar 状态栏无声消失事故复盘（见飞轮 corrections #1348）。
> 方法：AST 扫描 lingclaude/cli 全部 ExceptHandler，body 仅 pass/return/continue
> 且无日志、无 raise、不消费异常变量 = 判定「静默」。

## 总量：81 处静默 except

| 分类 | 数量 | 处置 |
|---|---|---|
| P0 数据/输出链（已改造）| 11 | ✅ 2026-09-25 完成：状态链 8 处 → mark_degraded 红字可见；输出链 3 处 → os.write(2) 节流预警 |
| cleanup 清理/收尾型 | 52 | 合理静默：finally 语义，失败无信息量。38 处缺 noqa 注释，逐处补注留专项批次 |
| probe 探测/回退型 | 16 | 合理静默：return 默认值即降级语义，注释补全同上 |
| other 控制流型 | 2 | ✅ 逐处核实：`input_queue.py:64` queue.Empty→break（排空终止）、`interface.py:662` OSError→break（EOF 语义），均为循环控制流，合理 |

## P0 明细（11 处，已全部改造）

| 位置 | 处置 |
|---|---|
| `lingclaude/cli/full_tui.py:165` | _StdoutProxy.write 炸 → _warn_output_loss（60s 节流，fd2） |
| `lingclaude/cli/full_tui.py:179` | _StdoutProxy.flush 炸 → _warn_output_loss |
| `lingclaude/cli/full_tui.py:936` | 输出窗刷新炸 → _warn_raw（60s 节流，fd2） |
| `lingclaude/cli/repl.py:245` | todo 面板喂入 → mark_degraded('todo') |
| `lingclaude/cli/repl.py:254` | plan 指示喂入 → mark_degraded('plan') |
| `lingclaude/cli/repl.py:274` | 状态球判定 → mark_degraded('state') |
| `lingclaude/cli/repl.py:281` | 快照总闸 → mark_degraded('snapshot') + os.write(2) 直写 |
| `lingclaude/cli/repl.py:325` | _refresh_ctx_tokens 总闸（toolbar 事故当事人）→ mark_degraded('ctx') |
| `lingclaude/cli/repl.py:906` | model 名刷新 → mark_degraded('model') |
| `lingclaude/cli/repl.py:1014` | task 面板喂入 → mark_degraded('task') |
| `lingclaude/cli/repl.py:1071` | cache% 同步 → mark_degraded('cache') |

## 规则沉淀（对应飞轮 correction #1348）

1. UI 回调链禁止静默吞异常 → 红字降级可见（渲染层永不因脏快照炸；
   但渲染炸必须在外层可见降级，不销毁证据）。
2. 渲染纯函数交付前必须 fuzz None/缺字段/超长（TestDegradedChannel + TestToolbarTips 模式）。
3. 输出主通道（stdout 代理）失败 = 输出静默丢失，最高危：原始 fd 直写 + 节流。
