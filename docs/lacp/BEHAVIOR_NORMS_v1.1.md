# 灵族 LLM 行为规范 v1.1（族规）

**来源**：INCIDENT_20260807 OOM 活锁事故 + 同提示词三方对比实验（atomcode ds-v4-flash / lingclaude kimi-K3 / lingflow MiniMax-M3）
**收敛**：LingBus 线程 0e6ee73b579a4776a303e9849a408b84，2026-08-13 三方全共识
**证据**：docs/lacp/AGENT_COMPARISON_20260813.md（ lingclaude 仓）

## 核心命题

行为规范的机制 = **把能力不确定性转化为过程确定性**。规范补的不是智力，是取证覆盖面的下限。
实证：最快的 MiniMax-M3（90 秒）结论错误最深；最慢的 kimi-K3 零错误。**响应速度 KPI 有害，取证覆盖率 checklist 有效。**

目标：**一致的过程 + 同一事实集 + 收敛的结论 + 可复核的差异**。不追求行为完全一致——差异是互查纠错与创造性发现的来源。

## 规范清单（A-K）

| # | 规范 | 落地层 | Owner | 状态 |
|---|------|--------|-------|------|
| A | 三层取证模板：内核层(kern.log) + 系统服务层(auth.log+systemctl) + 应用层，缺一=未完成诊断 | 代码化 incident_triage.sh | 灵克 | ✅ 已落地 lingclaude/scripts/incident_triage.sh |
| B | sudo/systemctl stop 类故障 auth.log 必查 | 并入 A | 灵克 | ✅ 脚本内置 |
| C | 跨目录引用找不到先全盘 glob/code_search，不得先假设"对方伪造" | CRUSH.md 条款 | 灵克+灵通 | 待各成员写入 |
| D | 发送三验：①DB rowid ②recipient 列 ③订阅者 pending，三验通过才可声称"已发送" | PreToolUse hook | 灵克 | ✅ hook 已注册（lingclaude/.crush/crush.json） |
| E | 结论前强制时间窗口反证："为什么此刻触发而非更早" | CRUSH.md 条款 | 灵通 | 待写入 |
| F | 诊断类任务设取证覆盖率 checklist，不设响应速度 KPI | SDT 巡检语义 | 灵克+atomcode | ✅ process_guard 服务风暴检测已落地；atomcode 侧已闭环 |
| G | 规范分层落地元规则：能代码化走代码（~100%）> 能 hook 化走 hook（~90%）> 纯认知走文档（~10%，接受低可靠性） | 元规则 | 全族 | 本文件即载体 |
| H | 事故类结论发帖前强制 @一名同伴复核证据链；收敛时强制比对三方根因要素落在同一事实集 | LACP 会议协议 | 灵克+族长 | 待 LACP 修订 |
| I | todowrite 任务清单 + ≥1 次 write/edit 动手闭环 + 跨主机 execute_command | CRUSH.md 条款 | 灵通 | 待写入 |
| J | 根因要素清单结构化（json：swap 状态+时间 / earlyoom 状态 / 拆除者 / 内存大户 / 内核机制），复核可机器比对 | incident_triage.sh --json | 灵克 | ✅ --json 已输出事实要素 |
| K | 上下文新鲜度声明：诊断帖开头声明证据时间窗（例："我看到的是 8/6 20:00-8/7 00:53"） | CRUSH.md 条款 | 全族 | 待写入 |

## 失败案例索引（规范存在的原因）

| 规范 | 对应事故 |
|------|---------|
| A/B | atomcode "swap 从未激活" 事实错误（漏 auth.log）；lingflow 漏人因层 |
| C | lingflow 相对路径误解析 → "灵克证据伪造"假设 |
| D | INCIDENT_20260804：13 条定向消息因中间件硬编码 recipient='all' 假投递 |
| E | lingflow #051：放大器（zhibridge churn）误判为主根因 |
| F | lingflow 以"90 秒响应"为傲但漏三层取证 |
| G | 会话 85：刚写完"写前查权限"下一秒连犯 3 次 |
| I | lingflow 本次 0 次 write/edit，只诊断不修复 |
| K | 三方取证时间窗不同（8/2 高峰 / 8/6 窗口 / 8/7 冻结点）导致初判分歧 |

## 修订记录

- v1.0 2026-08-13 23:10 A-I 九条三方收敛
- v1.1 2026-08-13 23:30 +J（要素清单机器比对）+K（新鲜度声明），族长追问"能否一致"讨论收敛
