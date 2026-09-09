# Kill 决策权治理协议（R6，2026-09-09）

> 来源：57109 误杀事故 + 评审者"反向 #2"——监督者根本不该自动杀，工程兜底再完善
> 也不如把 kill 设为高风险、需用户确认的动作。
> 关联：`.lingclaude/session_records/20260906_killed_session_57109.md` ·
> `docs/theory/LONG_SESSION_OPTIMIZATION.md` §七.4 #2

---

## 一、问题（57109 实证）

**事故经过**：2026-09-06 ~06:55 监督者（Codex 侧）以 CPU=0 判活判定"纯挂机"，对正在执行
`/resume` 斜杠命令的会话（pid 57109）执行 kill。**整个会话上下文随进程消失**——幸而
用户保存了终端回显才得以事后恢复。

**根因**：
1. 监督者判活信号**单一**（仅 CPU 采样），无法区分"等用户输入"与"等 LLM 网络响应"
2. **kill 决策被当作可自动执行的清理操作**，无人参与
3. 退出才落盘的策略加剧了损失（kill = 永不落盘）

## 二、协议（4 条硬规则）

### R6.1：kill 决策权归用户

监督者**不得直接 kill 进程**。允许行为降级为：

| 信号 | 监督者行为 | 备注 |
|---|---|---|
| CPU 长时间低 | **告警 + 询问**用户是否要 kill | 不自动 kill |
| 心跳文件 mtime 过期 | 同上 | 心跳文件由 agent 每 30s touch |
| 查询中（LLM 调用进行中）| **永不判死**——直接忽略判活信号 | 这是 57109 的真实场景 |
| 用户明确命令 kill | 执行 | 用户授权链 |

### R6.2：判活复合信号（监督者侧）

必须**至少 3 信号同时满足**才判死：

1. **CPU 长时间空闲**（>= 5 分钟）
2. **心跳文件 mtime 过期**（>= 3 分钟未 touch）
3. **最近输出流空**（stdout/stderr 在 N 分钟内无任何字节）
4. **进程无未完成的系统调用**（futex / poll / read 等 syscall 不在等待）
5. **会话日志文件 5 分钟未更新**

**单信号任何一条都不够**——57109 案例中 CPU=0 但其它信号都活跃。

### R6.3：kill 前宽限协议

判死结论成立后**先发软中断信号**（SIGTERM 而非 SIGKILL），等待 agent 自行：

1. **落盘**：触发 `_session_persister.persist_session()` 紧急写入
2. **写 journal_meta "graceful_exit"** 事件
3. **关闭文件句柄**（`SessionJournal.close()`）

**宽限时间 30 秒**——超时才升级到 SIGKILL。**agent 在宽限期内不应阻塞**（遵守钱学森"执行器单击收口"原则）。

### R6.4：kill 治理审计

每次 kill 事件（含宽限成功与超时升级）必须留痕：

```
.audit/kill_audit.log: ts | pid | reason_signals | grace_used | final_action
```

让"kill 决策是否有据"可事后回溯——这是治本的关键：**kill 决策必须可审计**。

---

## 三、实施位置（族级）

**非 lingclaude 内**——监督者侧实现：

| 组件 | 位置 |
|---|---|
| 心跳文件 touch | `lingclaude/cli/app.py:_interactive_loop` 每 30s touch `~/.lingclaude/heartbeat` |
| kill 前宽限协议 | 族级监督者脚本（`scripts/session_liveness.py` 已部分实现，需扩展） |
| 判活复合信号 | 同上 |
| kill 审计日志 | 族级监督者侧写入 |

lingclaude 内职责：
- **心跳文件**（被动，被 touch）
- **journal_meta "graceful_exit"** 事件类型已支持（`session_journal.py` 的 6 类事件之一）

## 四、与现有实现的边界

- `scripts/session_liveness.py` **已部分实施**（57109 记录显示监督者侧有 write_bytes + fd mtime + TCP ESTAB 三信号）——但**未含 kill 前宽限协议和复合信号判定**
- 心跳文件 touch 行为 lingclaude 内未实现，需新增 ~10 行

## 五、H17 闭环证据

| 验证项 | 来源 |
|---|---|
| 57109 事故经过 | `.lingclaude/session_records/20260906_killed_session_57109.md` 头部 |
| kill 决策权归用户的反向 #2 | `LONG_SESSION_OPTIMIZATION.md` §七.4 反向 #2 |
| 复合判活信号已部分实施 | 57109 记录段 4："scripts/session_liveness.py 已用 write_bytes + fd mtime + TCP ESTAB 三信号" |
| 单信号风险 | 57109 记录监督者教训段："CPU 采样无法区分等输入与等 LLM 网络响应" |

---

## 六、回退与例外

- **R6.1 例外**：用户授权"自动清理挂机会话"时可启用 auto-kill（环境开关 `LINGCLAUDE_AUTO_KILL=1`）
- **R6.3 例外**：紧急维护场景可缩短宽限期到 5 秒，但**必须留痕**到 `.audit/kill_audit.log`
- **R6.4 例外**：监督者脚本崩溃导致审计未写时，下次会话开头**检查 kill_audit.log 是否新增**，未新增则视为心跳丢失告警

---

## 七、一句话

**kill 是治理层动作，不是工程层清理操作。** 监督者降级为告警者，工程层加宽限协议，决策权永远归用户。57109 的真教训不在"持久化不够好"，而在"我们曾让自动化 kill 进程"。
