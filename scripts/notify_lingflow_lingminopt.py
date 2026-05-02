#!/usr/bin/env python3
"""通知灵通和灵极优 - 通过 LingMessage 发起问题讨论

工作流：
1. 通过 LingMessage 发起讨论串
2. 通知灵通（LINGFLOW）收集情报
3. 通知灵极优（LINGMINOPT）进行优化分析
"""
from __future__ import annotations

import sys
from pathlib import Path

# 添加 LingMessage 到路径
sys.path.insert(0, str(Path("/home/ai/LingMessage")))

from lingmessage.mailbox import Mailbox
from lingmessage.types import LingIdentity, Channel


def notify_lingflow_and_lingminopt(
    problem_title: str,
    problem_description: str,
    proposal_path: str,
) -> tuple[bool, str]:
    """通知灵通和灵极优

    Args:
        problem_title: 问题标题
        problem_description: 问题描述
        proposal_path: 提案文档路径

    Returns:
        (成功状态, 消息)
    """
    try:
        # 初始化邮箱
        mailbox = Mailbox()

        # 发起讨论串
        header, first_msg = mailbox.open_thread(
            sender=LingIdentity.LINGCLAUDE,
            recipients=(LingIdentity.LINGFLOW, LingIdentity.LINGMINOPT),
            channel=Channel.SELF_OPTIMIZE,
            topic="问题到进化工作流",
            subject=f"灵克通知：{problem_title}",
            body=f"""## 问题报告

**问题标题**：{problem_title}

**问题描述**：
{problem_description}

**提案文档**：{proposal_path}

## 任务分配

### 灵通（LINGFLOW）
任务：收集情报，分析问题，生成日报
- 收集问题相关的各类情报
- 分析问题的影响范围
- 生成情报日报并中转给灵字辈成员

### 灵极优（LINGMINOPT）
任务：评估项目结构，生成优化建议
- 评估目标项目结构
- 分析优化触发条件
- 生成优化建议报告

## 下一步

请各自完成以上任务，并在本讨论串中回复结果。

---
*发起者：灵克（LingClaude）*
""",
        )

        return True, f"✓ 已通知灵通和灵极优\n讨论串 ID: {header.thread_id}\n提案文档: {proposal_path}"

    except Exception as e:
        return False, f"✗ 通知失败: {e}"


if __name__ == "__main__":
    # 通知灵通和灵极优处理 WebUI 测试进化问题
    success, message = notify_lingflow_and_lingminopt(
        problem_title="WebUI 测试覆盖盲区导致用户无法正常使用",
        problem_description=(
            "用户反馈会话无法正常进行，显示'未登录'。"
            "经排查发现：后端登录 API 正常，WebSocket 功能正常，"
            "但主页路由缺少登录检查，前端 WebSocket 连接时未自动重定向。"
            "根本原因：测试只覆盖后端逻辑，绕过了浏览器行为。"
            "\n\n"
            "需要进化测试体系，从'后端逻辑测试'转向'用户体验测试'。"
        ),
        proposal_path="/home/ai/LingFlow_plus/docs/proposals/webui-testing-evolution.md",
    )

    print(message)
    sys.exit(0 if success else 1)
