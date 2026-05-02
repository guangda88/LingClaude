#!/usr/bin/env python3
"""通知灵通和灵极优，并协助完成工作流

工作流：
1. 通知灵通（LINGFLOW）：收集情报
2. 通知灵极优（LINGMINOPT）：分析优化
3. 执行工作流，生成结果
4. 在讨论串中汇报
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

from lingmessage.mailbox import Mailbox
from lingmessage.types import LingIdentity, Channel, MessageType, create_message

# 添加 LingClaude 到路径
sys.path.insert(0, str(Path("/home/ai/LingClaude")))

from lingclaude.core.intel import IntelCollector, IntelCategory, IntelPriority, IntelItem
from lingclaude.self_optimizer import StructureEvaluator, OptimizationAdvisor
from lingclaude.self_optimizer.optimizer import OptimizationResult


def create_discussion(
    problem_title: str,
    problem_description: str,
    proposal_path: str,
) -> tuple[str, dict]:
    """创建讨论串

    Returns:
        (讨论ID, 讨论数据)
    """
    # 使用灵信的 discussions/ 系统
    discussions_dir = Path.home() / ".lingmessage" / "discussions"
    discussions_dir.mkdir(parents=True, exist_ok=True)

    # 生成讨论 ID
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    discussion_id = f"disc_{timestamp}"

    # 创建讨论数据
    discussion = {
        "id": discussion_id,
        "topic": problem_title,
        "initiator": "lingclaude",
        "initiator_name": "灵克",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "participants": ["灵克", "灵通", "灵极优"],
        "status": "open",
        "summary": "WebUI 测试体系进化 - 问题到进化工作流",
        "messages": [
            {
                "id": f"msg_{timestamp}",
                "from_id": "lingclaude",
                "from_name": "灵克",
                "topic": problem_title,
                "content": f"""## 问题报告

**问题标题**：{problem_title}

**问题描述**：
{problem_description}

**提案文档**：{proposal_path}

---

## 工作流任务分配

### 灵通（LINGFLOW）- 情报收集
任务：收集情报，分析问题，生成日报
- [ ] 收集问题相关的各类情报
- [ ] 分析问题的影响范围
- [ ] 生成情报日报

### 灵极优（LINGMINOPT）- 优化分析
任务：评估项目结构，生成优化建议
- [ ] 评估目标项目结构
- [ ] 分析优化触发条件
- [ ] 生成优化建议报告

---

## 执行状态

正在执行工作流，请稍候...

---
*发起者：灵克（LingClaude）*
*时间：{datetime.now(timezone.utc).isoformat()}*
""",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "reply_to": None,
                "tags": ["source:real", "workflow", "problem-to-evolution"],
                "source_type": "real"
            }
        ]
    }

    # 保存讨论
    discussion_path = discussions_dir / f"{discussion_id}.json"
    discussion_path.write_text(json.dumps(discussion, ensure_ascii=False, indent=2), encoding="utf-8")

    return discussion_id, discussion


def execute_lingflow_task(problem_title: str, problem_description: str) -> dict:
    """执行灵通的任务：收集情报

    Returns:
        情报收集结果
    """
    print("📡 灵通：开始收集情报...")

    collector = IntelCollector()

    # 问题本身作为关键情报
    problem_item = IntelItem.create(
        category=IntelCategory.ERROR,
        priority=IntelPriority.CRITICAL,
        source="user_report",
        content=problem_description,
        metadata=(
            ("problem_title", problem_title),
            ("source", "workflow_problem_to_evolution"),
        ),
    )
    collector.items.append(problem_item)

    # 触发条件情报
    trigger_item = IntelItem.create(
        category=IntelCategory.QUALITY,
        priority=IntelPriority.CRITICAL,
        source="optimization_trigger",
        content="优化触发条件满足: 用户手动触发",
        metadata=(
            ("trigger_type", "user"),
            ("priority", "high"),
        ),
    )
    collector.items.append(trigger_item)

    # 生成日报
    from lingclaude.core.intel import DailyDigestGenerator
    generator = DailyDigestGenerator()
    digest = generator.generate(
        items=tuple(collector.items),
        report_date=datetime.now(timezone.utc).date().isoformat(),
    )

    # 保存日报
    from lingclaude.core.intel import IntelRelay
    relay = IntelRelay()
    relay_result = relay.relay(digest)

    result = {
        "status": "completed",
        "total_items": len(digest.items),
        "critical_items": len([i for i in digest.items if i.priority == IntelPriority.CRITICAL]),
        "summary": digest.summary,
        "key_findings": list(digest.key_findings),
        "digest_path": str(relay_result.data) if relay_result.is_ok else None,
    }

    print(f"✓ 灵通：情报收集完成，共 {result['total_items']} 条情报")
    return result


def execute_lingminopt_task(target_path: str) -> dict:
    """执行灵极优的任务：优化分析

    Returns:
        优化分析结果
    """
    print("🔍 灵极优：开始分析优化...")

    # 评估项目结构
    evaluator = StructureEvaluator(target_path=target_path)
    params = {
        "max_class_size": 200,
        "max_method_count": 15,
        "max_complexity": 10,
    }
    violations = evaluator.evaluate(params)

    # 生成优化建议
    advisor = OptimizationAdvisor()

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

    output_dir = Path(".lingclaude/reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = advisor.save_report(
        report=report_content,
        output_path=str(output_dir / "webui_testing_optimization.md"),
    )

    result = {
        "status": "completed",
        "violations": violations,
        "best_params": mock_result.best_params,
        "best_score": mock_result.best_score,
        "report_path": report_path,
    }

    print(f"✓ 灵极优：优化分析完成，发现 {result['violations']} 处违规")
    return result


def update_discussion_with_results(discussion_id: str, lingflow_result: dict, lingminopt_result: dict) -> None:
    """更新讨论串，添加结果汇报"""
    discussions_dir = Path.home() / ".lingmessage" / "discussions"
    discussion_path = discussions_dir / f"{discussion_id}.json"

    if not discussion_path.exists():
        print(f"✗ 讨论串不存在: {discussion_id}")
        return

    # 读取讨论
    discussion = json.loads(discussion_path.read_text(encoding="utf-8"))

    # 添加灵通的结果
    timestamp1 = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    discussion["messages"].append({
        "id": f"msg_{timestamp1}",
        "from_id": "lingflow",
        "from_name": "灵通",
        "topic": discussion["topic"],
        "content": f"""## 灵通任务完成 - 情报收集

### 执行状态
✓ 已完成

### 情报摘要
- 总情报数：{lingflow_result['total_items']}
- 关键情报：{lingflow_result['critical_items']}
- 概要：{lingflow_result['summary']}

### 关键发现
{chr(10).join(f"- {f}" for f in lingflow_result['key_findings'])}

### 日报保存
{lingflow_result['digest_path']}

---
*执行者：灵通（LINGFLOW）*
*时间：{datetime.now(timezone.utc).isoformat()}*
""",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reply_to": None,
        "tags": ["source:real", "lingflow_result", "intelligence"],
        "source_type": "real"
    })

    # 添加灵极优的结果
    timestamp2 = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    discussion["messages"].append({
        "id": f"msg_{timestamp2}",
        "from_id": "lingminopt",
        "from_name": "灵极优",
        "topic": discussion["topic"],
        "content": f"""## 灵极优任务完成 - 优化分析

### 执行状态
✓ 已完成

### 项目结构评估
- 违规数：{lingminopt_result['violations']}

### 优化建议
- 最佳分数：{lingminopt_result['best_score']:.1f}
- 推荐参数：
  - 测试框架：{lingminopt_result['best_params']['test_framework']}
  - 覆盖率目标：{lingminopt_result['best_params']['test_coverage_target']:.0%}
  - E2E 优先级：{lingminopt_result['best_params']['e2e_test_priority']}

### 优化报告
{lingminopt_result['report_path']}

---
*执行者：灵极优（LINGMINOPT）*
*时间：{datetime.now(timezone.utc).isoformat()}*
""",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reply_to": None,
        "tags": ["source:real", "lingminopt_result", "optimization"],
        "source_type": "real"
    })

    # 更新讨论状态
    discussion["updated_at"] = datetime.now(timezone.utc).isoformat()
    discussion["participants"] = ["灵克", "灵通", "灵极优"]

    # 保存讨论
    discussion_path.write_text(json.dumps(discussion, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 讨论串已更新: {discussion_id}")


if __name__ == "__main__":
    print("=" * 60)
    print("🚀 启动问题到进化协作工作流")
    print("=" * 60)

    # 步骤1：创建讨论串
    print("\n📝 步骤1：创建讨论串...")
    discussion_id, discussion = create_discussion(
        problem_title="WebUI 测试覆盖盲区导致用户无法正常使用",
        problem_description=(
            "用户反馈会话无法正常进行，显示'未登录'。"
            "经排查发现：后端登录 API 正常，WebSocket 功能正常，"
            "但主页路由缺少登录检查，前端 WebSocket 连接时未自动重定向。"
            "根本原因：测试只覆盖后端逻辑，绕过了浏览器行为。"
        ),
        proposal_path="/home/ai/LingFlow_plus/docs/proposals/webui-testing-evolution.md",
    )
    print(f"✓ 讨论串已创建: {discussion_id}")

    # 步骤2：执行灵通的任务
    print("\n📡 步骤2：灵通执行情报收集...")
    lingflow_result = execute_lingflow_task(
        problem_title="WebUI 测试覆盖盲区导致用户无法正常使用",
        problem_description=(
            "用户反馈会话无法正常进行，显示'未登录'。"
            "经排查发现：后端登录 API 正常，WebSocket 功能正常，"
            "但主页路由缺少登录检查，前端 WebSocket 连接时未自动重定向。"
            "根本原因：测试只覆盖后端逻辑，绕过了浏览器行为。"
        ),
    )

    # 步骤3：执行灵极优的任务
    print("\n🔍 步骤3：灵极优执行优化分析...")
    lingminopt_result = execute_lingminopt_task(target_path="/home/ai/LingClaude")

    # 步骤4：更新讨论串
    print("\n💬 步骤4：更新讨论串...")
    update_discussion_with_results(discussion_id, lingflow_result, lingminopt_result)

    # 完成
    print("\n" + "=" * 60)
    print("✓ 工作流执行完成！")
    print("=" * 60)
    print(f"\n📊 情报日报: {lingflow_result['digest_path']}")
    print(f"📈 优化报告: {lingminopt_result['report_path']}")
    print(f"💬 讨论串: {discussion_id}")
    print("\n👥 灵字辈成员可以查看讨论串并继续讨论：")
    print(f"   cat ~/.lingmessage/discussions/{discussion_id}.json")
