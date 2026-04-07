# 灵克工作流：从问题到进化

## 工作流概述

这是一个整合了灵通（情报系统）和灵极优（自优化系统）的完整工作流，用于将问题转化为系统进化方案。

## 工作流架构

```
问题发现 → 灵通收集情报 → 灵极优分析触发 → 灵极优评估结构 → 灵极优生成建议 → 灵通中转情报 → 灵字辈审议 → 执行优化
```

## 核心组件

### 1. 灵通（情报系统）

**职责**：收集、汇总、中转情报

**核心功能**：
- `IntelCollector` - 收集各类情报（错误、行为、结构、优化等）
- `DailyDigestGenerator` - 生成情报日报
- `IntelRelay` - 将情报中转给灵字辈成员

**情报分类**：
- `ERROR` - 错误事件
- `BEHAVIOR` - 行为观察
- `CODE_PATTERN` - 代码模式
- `STRUCTURE` - 项目结构
- `OPTIMIZATION` - 优化建议
- `QUALITY` - 质量指标
- `SECURITY` - 安全问题

**优先级**：
- `CRITICAL` - 关键情报，需要立即处理
- `WARNING` - 警告，需要关注
- `INFO` - 信息，仅供参考

### 2. 灵极优（自优化系统）

**职责**：分析触发条件、评估项目结构、生成优化建议

**核心功能**：
- `OptimizationTrigger` - 检查优化触发条件
- `StructureEvaluator` - 评估项目结构质量
- `OptimizationAdvisor` - 生成优化建议报告

**触发条件**（8 类）：
1. 用户触发
2. 代码质量下降
3. 行为异常
4. 结构违规
5. 性能问题
6. 扩展性挑战
7. 技术债务
8. 时间周期

**结构评估指标**：
- 类数量
- 方法数量
- 复杂度
- 违规数
- 平均类大小
- 平均方法数

## 工作流执行步骤

### Step 1: 收集问题情报（灵通）

```python
collector = IntelCollector()

# 将用户报告的问题作为关键情报
problem_item = IntelItem.create(
    category=IntelCategory.ERROR,
    priority=IntelPriority.CRITICAL,
    source="user_report",
    content="问题描述...",
)
collector.items.append(problem_item)
```

### Step 2: 分析触发条件（灵极优）

```python
trigger = OptimizationTrigger()

# 检查是否满足优化触发条件
context = {"user_triggered": True}
triggered, trigger_info = trigger.check_all_conditions(context)

if triggered:
    # 将触发信息作为情报
    trigger_item = IntelItem.create(
        category=IntelCategory.QUALITY,
        priority=IntelPriority.CRITICAL,
        source="optimization_trigger",
        content=f"优化触发条件满足: {trigger_info.reason}",
    )
    collector.items.append(trigger_item)
```

### Step 3: 评估项目结构（灵极优）

```python
evaluator = StructureEvaluator(target_path="/path/to/project")

# 评估项目结构
params = {
    "max_class_size": 200,
    "max_method_count": 15,
    "max_complexity": 10,
}
violations = evaluator.evaluate(params)

# 将结构评估结果作为情报
structure_item = IntelItem.create(
    category=IntelCategory.STRUCTURE,
    priority=IntelPriority.WARNING if violations > 5 else IntelPriority.INFO,
    source="structure_evaluator",
    content=f"项目结构评估完成，发现 {violations} 处违规",
)
collector.items.append(structure_item)
```

### Step 4: 生成优化建议（灵极优）

```python
advisor = OptimizationAdvisor()

# 模拟优化结果（实际由 Optimizer 执行）
mock_result = OptimizationResult(
    success=True,
    best_params={
        "test_framework": "playwright",
        "test_coverage_target": 0.8,
        "e2e_test_priority": "high",
    },
    best_score=85.0,
    experiments=10,
    duration=1.5,
)

# 当前指标
current_metrics = {
    "structure_violations": violations,
    "test_failure_rate": 0.5,
    "e2e_coverage": 0.0,
}

# 生成优化建议报告
report_content = advisor.generate_report(
    goal="testing_evolution",
    target="/path/to/project",
    current_metrics=current_metrics,
    optimization_result=mock_result,
)

# 保存报告
report_path = advisor.save_report(
    report=report_content,
    output_path=".lingclaude/reports/optimization.md",
)

# 将优化建议作为情报
optimization_item = IntelItem.create(
    category=IntelCategory.OPTIMIZATION,
    priority=IntelPriority.INFO,
    source="optimization_advisor",
    content=f"优化建议生成完成: WebUI 测试体系进化",
)
collector.items.append(optimization_item)
```

### Step 5: 生成情报日报（灵通）

```python
generator = DailyDigestGenerator()
digest = generator.generate(
    items=tuple(collector.items),
    report_date=date.today().isoformat(),
)
```

**日报内容**：
- 总情报数
- 关键发现
- 优化建议
- 分类统计
- 优先级分布
- 情报明细

### Step 6: 中转情报（灵通）

```python
relay = IntelRelay()
relay_result = relay.relay(digest)

# 生成三个文件：
# 1. digest_2026-04-07.json - JSON 格式情报
# 2. digest_2026-04-07.md - Markdown 格式情报
# 3. manifest.json - 情报清单
```

## 执行示例

```bash
cd /home/ai/LingClaude
python3 scripts/workflow_problem_to_evolution.py
```

**输出**：
```
📡 灵通：收集问题情报...
🔍 灵极优：分析优化触发条件...
🏗️  灵极优：评估项目结构...
💡 灵极优：生成优化建议...
📊 灵通：生成情报日报...
📤 灵通：中转情报给灵字辈成员...

✓ 工作流执行完成
```

**生成的文件**：
1. `.lingclaude/intel/digest_2026-04-07.md` - 情报日报
2. `.lingclaude/workflows/workflow_2026-04-07.md` - 工作流摘要
3. `.lingclaude/reports/webui_testing_optimization.md` - 优化建议报告

## 输出文档详解

### 1. 情报日报（digest_*.md）

```markdown
# 灵克情报日报 — 2026-04-07

## 概要
共收集 4 条情报。 error: 1 条 optimization: 1 条 ...

## 关键发现
- 发现 2 条关键情报需要立即关注
- 捕获 1 个错误事件
- 完成 1 次自优化周期

## 建议
- 审查近期错误日志，排查工具参数和路径问题

## 分类统计
- **error**: 1
- **optimization**: 1
- **quality**: 1
- **structure**: 1

## 优先级分布
- **critical**: 2
- **info**: 2

## 情报明细
### 🔴 [error] user_report
用户反馈会话无法正常进行，显示'未登录'...
```

### 2. 工作流摘要（workflow_*.md）

```markdown
# 问题到进化工作流执行报告

## 执行时间
2026-04-06T22:14:50.145964+00:00

## 执行步骤
1. ✓ 收集问题情报: WebUI 测试覆盖盲区导致用户无法正常使用
2. ✓ 触发优化: User manually triggered optimization
3. ✓ 结构评估: 3.0 处违规
4. ✓ 生成优化建议: WebUI 测试体系进化
5. ✓ 生成日报: 4 条情报
6. ✓ 情报已保存: .lingclaude/intel/digest_2026-04-07.md

## 下一步行动
1. 灵字辈成员查看情报日报
2. 审议提案文档
3. 提供反馈意见
4. 形成最终决策
5. 执行优化方案
```

### 3. 优化建议报告（*_optimization.md）

```markdown
# LingClaude Self-Optimization Report

Generated: 2026-04-07 06:14:50
Goal: testing_evolution
Target: /home/ai/LingYi

---

## Recommendations

### Optimal Parameters

```yaml
e2e_test_priority: high
test_coverage_target: 0.80
test_framework: playwright
```

**Experiments**: 10
**Duration**: 1.5s

### Parameter Comparison

| Parameter | Recommended |
|-----------|-------------|
| e2e_test_priority | high |
| test_coverage_target | 0.80 |
| test_framework | playwright |

## Implementation Steps

1. Update `config.yaml` with the recommended parameters above
2. Run `lingclaude optimize --target <path>` to verify
3. Commit configuration changes
```

## 工作流优势

### 1. 系统化
- 将问题发现到优化的过程标准化
- 每个步骤都有明确的输入和输出
- 可重复、可追溯

### 2. 自动化
- 自动收集和分析情报
- 自动评估项目结构
- 自动生成优化建议
- 自动生成和保存报告

### 3. 可视化
- 生成多个格式的报告（JSON + Markdown）
- 提供清晰的执行步骤
- 可视化关键发现和建议

### 4. 协作性
- 情报中转给灵字辈成员
- 便于团队协作和审议
- 支持多人决策

### 5. 持续优化
- 记录每次优化的历史
- 支持优化效果评估
- 形成知识积累

## 适用场景

1. **用户反馈问题** → 通过工作流转化为优化方案
2. **代码质量下降** → 自动触发优化分析
3. **项目结构违规** → 评估并生成改进建议
4. **功能需求变化** → 分析影响并优化架构
5. **定期维护** → 自动化结构评估和优化建议

## 扩展方向

### 1. 集成 LingMessage
```python
from lingmessage.mailbox import Mailbox

mailbox = Mailbox()
mailbox.post(
    subject="灵克发现：WebUI 测试覆盖盲区",
    body=digest.to_markdown(),
)
```

### 2. 自动执行优化
```python
from lingclaude.self_optimizer.optimizer import SynchronousOptimizer

optimizer = SynchronousOptimizer()
result = optimizer.optimize(
    request=OptimizationRequest(
        target="/home/ai/LingYi",
        goal="testing_evolution",
        params={"test_framework": "playwright"},
        config={},
    )
)
```

### 3. 持续监控
```python
# 设置定期执行
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()
scheduler.add_job(
    execute_problem_to_evolution_workflow,
    'interval',
    hours=24,
)
scheduler.start()
```

## 文件位置

- 工作流脚本：`/home/ai/LingClaude/scripts/workflow_problem_to_evolution.py`
- 情报日报：`.lingclaude/intel/digest_*.md`
- 工作流摘要：`.lingclaude/workflows/workflow_*.md`
- 优化报告：`.lingclaude/reports/*_optimization.md`

---

**工作流版本**: 1.0
**创建时间**: 2026-04-07
**维护者**: 灵克（LingClaude）
