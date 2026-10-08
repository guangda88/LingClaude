# LONGTASK_SOP — 巨型长任务会话治理 SOP

> 立规依据：2026-10-08 双实锤——① 巨型会话 `input 22.7M/20M` 触发预算 pause 被直接挂起，无收尾轮次；
> ② 推送门禁事故中「被 timeout 掐死的 worker 变孤儿滚雪球」。本 SOP 与 `scripts/session_relay_orchestrator.sh`
> （接力编排器）配套，构成「会话内治理 + 会话外接力」闭环。

## 〇、适用范围

满足任一即按本 SOP 开局：
- 预计 **>200 工具轮次**（R8 提示会反复触发）
- 涉及**批量循环处理**（全仓审计/迁移/数据修复/逐条核验）
- 上一会话因此类任务**被压缩或挂起**过（继续同一任务面）

典型案例存档：推送门禁收口（40+ commit 批量）、browse_agg 登录态修复（多供应商循环）、dushu 图谱合并（94 CPU 小时长跑）。

## 一、总原则（四条，违者必翻车）

1. **状态外置**：任何「只在内存/对话里」的状态都是定时炸弹。事实写 todo、进展写 handover、证据落 `/tmp/*.txt`。压缩/挂起后，文件是唯一可信现场。
2. **轮次治理**：最好的轮次是不进主会话的轮次。探索/搜索/审查拆 `sub_agent`（R8 钩子），长计算/等待拆 `run_in_background`——主会话轮次保持两位数量级。
3. **blocked_on_human 必停**：扫码、充值、风控申诉、物理操作、sudo 授权——停下来等人或发灵信告警，**绝不空转轮询烧预算**（元宝风控案：自动化流量本身在给风控喂证据）。
4. **交接是持续动作，不是临终遗言**：kill-safe（Handover V2 instant write，每步即写）。预算 pause 是硬掐，没有「最后一轮」——靠的只能是平时已落盘的状态。

## 二、开局检查单（巨型任务第一批轮次做完这些）

```
[ ] todo_write 立清单（每项一个可独立验证的动作，避免「完成XX大任务」式巨型条目）
[ ] mkdir -p /tmp/<task>_state/（证据、中间产物、结果文件统一落盘）
[ ] 初始化 handover：
    python3 -c "
    from pathlib import Path
    from lingmemory.handover import HandoverWriter
    w = HandoverWriter(Path('/tmp/<task>_state'), 'lingclaude')
    h = w.load_or_create(); w.save(h)"
[ ] 规模评估：预计轮次/时长/是否含 blocked_on_human 节点 → 决定是否启动接力编排器
```

## 三、运行中节奏

| 触发 | 动作 |
|---|---|
| 每完成一个 todo | handover 增量更新（complete_task + 结论一行）；**并追加一行到 `/tmp/<task>_state/audit/todo_log.jsonl`**（`{"todo_id":..., "done_at":ts}`），供 relay_audit 对账 |
| 接力运行期 | 可选挂 `scripts/relay_audit.sh --session-state /tmp/<task>_state/audit --daemon`：轮询对比 todo_log 与 handover 增量，发现「todo 已完成但交接无记录」即灵信告警（todo 无原生 hook 的对账兜底；工具侧原生 hook 已提 R4 需求，thread d7ab589d） |
| 每 ~20 轮 | checkpoint：后台作业状态核录 + 证据文件落盘确认 |
| 出现**压缩摘要** | 说明远期上下文已折叠——立即重读关键文件，勿凭记忆断言 |
| R8 轮次提示反复出现 | 把探索类工作拆给 sub_agent，主会话只做决策与组装 |
| **预算/轮次预警信号**（system 提示、用量告警） | **立即进入预停协议**（§四），暂停开启新子任务 |

## 四、预停协议（疑似即将被挂起/中断时，按序执行，缺一不可）

1. handover 置 `waiting_relay` + 写 `resume_hint`（下一会话第一条该读什么、该跑哪条命令）
2. 后台作业逐个核录：job_id、是否可中断、重启命令（长计算宁可留给编排器重启，不要为等它烧轮次）
3. 最终状态写盘 `/tmp/<task>_state/FINAL_REPORT.md`（哪怕只是半成品状态）
4. todo 清单逐项标注 status（新会话第一眼就要能看到断点）

> 若收到的是**硬挂起前最后可见信号**（如预算 85% 预警，需求已提灵通评审），按上述顺序执行，
> 手上有半步未完成的写操作要先完成——半写的状态比没有状态更害人。

## 五、接力恢复（无人值守闭环）

### 自动（编排器）
`scripts/session_relay_orchestrator.sh` 轮询队列，发现 `waiting_relay` 的 handover 即开新会话注入 resume_hint。
部署见该脚本头注释；队列文件 schema 见脚本内注释。

### 手动（人贴一句话的规范姿势）
```bash
lingclaude run --print "请读取 /tmp/<task>_state/handover.yaml 接续任务。
resume_hint: <handover 中的原文>
请继续完成中断的任务。"
```
或交互模式下对新会话说同样的话。**不要**只说「继续」而不给 handover 路径——新会话对旧现场的默认认知为零。

## 六、编排器运维与失败语义

- 编排器对同一任务最多重试 3 次（指数退避），超限置 `blocked` 并发灵信 alert——**宁可停下等人，不可循环烧钱**
- `completed` 的 handover 自动出队；`blocked` 的留给人在 VNC/终端处理后手动重新入队
- 编排器自身崩溃不影响已落盘状态（它只是消费者，事实源永远在 handover 文件里）

## 七、与既有防线的关系

| 防线 | 分工 |
|---|---|
| PUSH_SOP（§三旁路） | 交付物的出门门禁 |
| PROCESS_OPS_RUNBOOK（P1-P6） | 进程/作业操作纪律 |
| 本 SOP | 会话生命线治理：状态外置 + 预停 + 接力 |
