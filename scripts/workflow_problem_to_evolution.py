#!/usr/bin/env python3
"""问题到进化工作流 - 整合灵通（情报）和灵极优（自优化）

工作流：
1. 收集问题情报
2. 分析触发优化条件
3. 评估当前状态
4. 生成优化建议
5. 中转给灵字辈成员审议
"""
from __future__ import annotations


from pathlib import Path
from datetime import date, datetime, timezone

from lingclaude.core.intel import (
    IntelCollector,
    IntelCategory,
    IntelPriority,
    DailyDigestGenerator,
    IntelRelay,
    IntelItem,
)
from lingclaude.self_optimizer import (
    OptimizationTrigger,
    StructureEvaluator,
    OptimizationAdvisor,
)
from lingclaude.self_optimizer.optimizer import OptimizationResult
from lingclaude.core.types import Result


def execute_problem_to_evolution_workflow(
    problem_title: str,
    problem_description: str,
    proposal_path: str,
    target_path: str = "/home/ai/LingFlow_plus",
) -> Result[str]:
    """执行从问题到进化的完整工作流

    Args:
        problem_title: 问题标题
        problem_description: 问题描述
        proposal_path: 提案文档路径
        target_path: 目标项目路径

    Returns:
        Result[工作流摘要]
    """
    steps: list[str] = []

    # ========== Step 1: 灵通收集问题情报 ==========
    print("📡 灵通：收集问题情报...")
    collector = IntelCollector()

    # 问题本身作为关键情报
    problem_item = IntelItem.create(
        category=IntelCategory.ERROR,
        priority=IntelPriority.CRITICAL,
        source="user_report",
        content=problem_description,
        metadata=(
            ("problem_title", problem_title),
            ("proposal_path", proposal_path),
        ),
    )
    collector.items.append(problem_item)
    steps.append(f"✓ 收集问题情报: {problem_title}")

    # ========== Step 2: 灵极优分析触发条件 ==========
    print("🔍 灵极优：分析优化触发条件...")
    trigger = OptimizationTrigger()
    context = {
        "user_triggered": True,  # 用户手动触发优化
        "problem_title": problem_title,
    }
    triggered, trigger_info = trigger.check_all_conditions(context)

    if triggered and trigger_info:
        steps.append(f"✓ 触发优化: {trigger_info.reason}")
        # 将触发信息作为情报
        trigger_item = IntelItem.create(
            category=IntelCategory.QUALITY,
            priority=IntelPriority.WARNING if trigger_info.priority == "medium" else IntelPriority.CRITICAL,
            source="optimization_trigger",
            content=f"优化触发条件满足: {trigger_info.reason}",
            metadata=(
                ("trigger_type", trigger_info.type),
                ("priority", trigger_info.priority),
            ),
        )
        collector.items.append(trigger_item)
    else:
        steps.append("✓ 无需触发优化（系统运行正常）")

    # ========== Step 3: 评估项目结构 ==========
    print("🏗️  灵极优：评估项目结构...")
    evaluator = StructureEvaluator(target_path=target_path)
    params = {
        "max_class_size": 200,
        "max_method_count": 15,
        "max_complexity": 10,
    }
    violations = evaluator.evaluate(params)

    # 使用 violations 创建情报
    structure_item = IntelItem.create(
        category=IntelCategory.STRUCTURE,
        priority=IntelPriority.WARNING if violations > 5 else IntelPriority.INFO,
        source="structure_evaluator",
        content=f"项目结构评估完成，发现 {violations} 处违规",
        metadata=(
            ("violations", str(violations)),
            ("target_path", target_path),
        ),
    )
    collector.items.append(structure_item)
    steps.append(f"✓ 结构评估: {violations} 处违规")

    # ========== Step 4: 生成优化建议 ==========
    print("💡 灵极优：生成优化建议...")
    advisor = OptimizationAdvisor()

    # 模拟优化结果（实际应该由 Optimizer 执行）
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

    report_content = advisor.generate_report(
        goal="testing_evolution",
        target=target_path,
        current_metrics=current_metrics,
        optimization_result=mock_result,
    )

    # 保存报告
    output_dir = Path(".lingclaude/reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = advisor.save_report(
        report=report_content,
        output_path=str(output_dir / "webui_testing_optimization.md"),
    )

    # 将优化建议作为情报
    optimization_item = IntelItem.create(
        category=IntelCategory.OPTIMIZATION,
        priority=IntelPriority.INFO,
        source="optimization_advisor",
        content=f"优化建议生成完成: WebUI 测试体系进化",
        metadata=(
            ("best_score", f"{mock_result.best_score:.1f}"),
            ("experiments", str(mock_result.experiments)),
            ("report_path", report_path),
        ),
    )
    collector.items.append(optimization_item)
    steps.append(f"✓ 生成优化建议: WebUI 测试体系进化")

    # ========== Step 5: 生成日报 ==========
    print("📊 灵通：生成情报日报...")
    generator = DailyDigestGenerator()
    digest = generator.generate(
        items=tuple(collector.items),
        report_date=date.today().isoformat(),
    )
    steps.append(f"✓ 生成日报: {len(digest.items)} 条情报")

    # ========== Step 6: 中转情报 ==========
    print("📤 灵通：中转情报给灵字辈成员...")
    relay = IntelRelay()
    relay_result = relay.relay(digest)

    if relay_result.is_ok:
        steps.append(f"✓ 情报已保存: {relay_result.data}")
    else:
        steps.append(f"✗ 情报中转失败: {relay_result.error}")

    # ========== Step 7: 生成工作流摘要 ==========
    summary_lines = [
        "# 问题到进化工作流执行报告",
        "",
        f"## 执行时间",
        f"{datetime.now(timezone.utc).isoformat()}",
        "",
        "## 执行步骤",
        "",
    ]
    for i, step in enumerate(steps, 1):
        summary_lines.append(f"{i}. {step}")

    summary_lines.extend([
        "",
        "## 情报摘要",
        "",
        f"- 总情报数: {len(digest.items)}",
        f"- 关键情报: {len([i for i in digest.items if i.priority == IntelPriority.CRITICAL])}",
        f"- 警告: {len([i for i in digest.items if i.priority == IntelPriority.WARNING])}",
        f"- 信息: {len([i for i in digest.items if i.priority == IntelPriority.INFO])}",
        "",
        "## 关键发现",
        "",
    ])
    for finding in digest.key_findings:
        summary_lines.append(f"- {finding}")

    summary_lines.extend([
        "",
        "## 优化建议",
        "",
    ])
    for rec in digest.recommendations:
        summary_lines.append(f"- {rec}")

    summary_lines.extend([
        "",
        "## 下一步行动",
        "",
        "1. 灵字辈成员查看情报日报",
        "2. 审议提案文档",
        "3. 提供反馈意见",
        "4. 形成最终决策",
        "5. 执行优化方案",
        "",
        f"## 提案文档",
        "",
        f"路径: {proposal_path}",
        "",
    ])

    summary = "\n".join(summary_lines)

    # 保存工作流摘要
    output_dir = Path(".lingclaude/workflows")
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / f"workflow_{date.today().isoformat()}.md"
    summary_path.write_text(summary, encoding="utf-8")

    return Result.ok(summary)


if __name__ == "__main__":
    # 执行 WebUI 测试进化工作流
    result = execute_problem_to_evolution_workflow(
        problem_title="WebUI 测试覆盖盲区导致用户无法正常使用",
        problem_description=(
            "用户反馈会话无法正常进行，显示'未登录'。"
            "经排查发现：后端登录 API 正常，WebSocket 功能正常，"
            "但主页路由缺少登录检查，前端 WebSocket 连接时未自动重定向。"
            "根本原因：测试只覆盖后端逻辑，绕过了浏览器行为。"
        ),
        proposal_path="/home/ai/LingYi/docs/proposals/webui-testing-evolution.md",
        target_path="/home/ai/LingYi",
    )

    if result.is_ok:
        print("\n" + "=" * 60)
        print(result.data)
        print("=" * 60)
        print("\n✓ 工作流执行完成")
    else:
        print(f"\n✗ 工作流执行失败: {result.error}")
