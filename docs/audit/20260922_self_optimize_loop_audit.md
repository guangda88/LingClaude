# 审计报告：自优化/自学习闭环断点分析（2026-09-22，灵克）

> 审计对象：lingclaude self_optimizer 飞轮（turn_learner → KnowledgeBase →
> system_prompt 注入 / OptimizationDaemon → benchmark → optimizer）
> 实测证据：.lingclaude/knowledge.db（26546 条规则）、daemon/trigger 接线源码

## 一、结论：飞轮不是"没转"，是三个齿轮各自空转

组件全部存在且**已接线**，但闭环在三个断点处断开——学习在发生、
注入在发生、优化在发生，**但互不咬合，且无质量闸**。

## 二、飞轮现状图（实测）

```
写路径（已通）：turn_learner.record_turn_learnings
  → 每轮无条件写 KnowledgeBase（幻觉/工具错/纠正/里程碑）
  → 实测 26546 条规则（26021 active），"工具错误记录"一条名字重复 20241 次

读路径（已通但失效）：system_prompt_builder.build_dynamic_system_suffix
  → kb.search_rules(keyword=messages[-1][:50], limit=5)
  → 置信度>0.5 过滤 → 注入"已学经验"到动态尾随块

优化路径（已通但悬空）：session 结束 → repl_turn 触发 daemon（24h 节流）
  → run_cycle: trigger 判定 → evaluator 结构分 → benchmark 行为门禁
  → optimizer 选 best_params → 产出 OptimizationCycle（报告落盘）

从未接线：learner/rule_extractor.py（RuleExtractor/SecurityRuleExtractor
  只在 learner/__init__ 导出，全库无消费方——规则提炼层是死代码）
```

## 三、三个断点（按严重度）

**断点 A：学习失禁——写 without 提炼（最重）**
`record_turn_learnings` 每轮机械落一条 `LearnedRule`，置信度硬编码 0.7/0.8，
"工具错误记录"同名规则 2 万条。效果：
- `search_rules` 用 `messages[-1][:50]` 当 keyword 做 SQL LIKE——26021 条
  active 全部过 confidence>0.5 闸，注入内容是噪声堆，**污染 system prompt
  尾随块**（每轮白吃前缀缓存外的 token，还可能误导模型）
- 知识库成为日志仓库，不是知识——规则没有"从经验到可复用规则"的提炼
  （RuleExtractor 写了没人用，正是缺的这层）

**断点 B：注入盲读——读 without 召回质量**
读路径按"当前 prompt 前 50 字符"关键词匹配，无语义召回、无频次/时效
排序、无 usage 反馈（`record_recall` 只在 layered_memory.experience 有，
KnowledgeBase 没有）。注入的"经验"与当前任务相关性无保证。

**断点 C：优化悬空——optimize without 行为闭环回写**
daemon 产出 OptimizationCycle 报告 + best_params（benchmark 门禁确实挡了
回退参数，这层是真的），但 **best_params 应用后没有反馈进下一轮 trigger
阈值/规则置信度**——优化结果与知识库、行为指标三者不通，每轮 cycle
从头再算，学习不累积。

## 四、让飞轮真正转起来的方案（最小改动优先，M0→M3）

**M0 止血（1 天）：写路径加质量闸 + 注入限额**
- `record_turn_learnings` 改"聚合后提炼"：同类信号（同名工具错）只
  UPDATE frequency+1，不新增行；新增规则须过 RuleExtractor 提炼出
  pattern 才落库（正好激活死代码）
- 注入侧：limit=5 → 2，加 `ORDER BY frequency DESC, confidence DESC,
  created_at DESC`，且只取 `confidence>0.7 AND status='active'`
- 存量清洗：同名>10 的规则合并为单条聚合规则（20241 条"工具错误记录"
  → 1 条 + frequency=20241）

**M1 闭环（3-5 天）：给知识库加反馈边**
- KnowledgeBase 加 `usage` 列（注入次数/被后续工具调用采纳的代理信号），
  `record_recall` 语义接入；注入命中的规则 frequency+1，
  连续 N 次注入未被"采纳"（代理：同会话行为指标无改善）→ confidence
  衰减 → 跌破 0.5 自动转 draft（现有 525 draft 的机制复用）
- 这就是"学习和优化飞轮"的**最小咬合点**：行为指标（写）→ 提炼
  （rule_extractor）→ 注入（读）→ 行为变化（代理观测）→ 置信度演化

**M2 优化闭环（1 周）：daemon 产出回写 trigger/benchmark**
- OptimizationCycle 结束时把 `violations_after`/benchmark delta 写入
  knowledge.db（新表 cycles），trigger 判定阈值从表里读历史基线
  （自适应阈值替代硬编码），benchmark 任务集支持从高频错误模式
  （断点 A 提炼出的 pattern）动态生成题目——优化开始针对真实弱点

**M3 跨会话飞轮（2 周，依赖灵忆）**
- verify_ledger 的 record_verify 证据（本项目已在用）作为规则可信度的
  第三方锚：有 verify 证据的规则 confidence 上限提至 0.9，无证据的
  上限 0.6——知识与验证台账挂钩，杜绝"自说自话式学习"

## 五、成本与风险

- M0 纯止血，不改契约，风险最低；**26546 条存量是当前注入污染源，
  优先清洗**
- M1 的"采纳代理信号"是弱观测（行为指标改善≠规则贡献），置信度演化
  要保守（衰减快、恢复慢）
- M2 改 daemon 有 P0 实证门禁（benchmark 不回退才应用）护航，方向安全
- 全程不写 SLA，收益以 benchmark delta 与注入 token 占用实测为准
