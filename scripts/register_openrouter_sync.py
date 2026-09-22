#!/usr/bin/env python3
"""注册 OpenRouter 模型同步定时任务"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("OPENROUTER_API_KEY", os.environ.get("OPENROUTER_API_KEY", ""))

from lingclaude.core.scheduler import get_schedule_manager, ScheduleType, TaskPriority

def register_sync() -> None:
    mgr = get_schedule_manager()
    # 注册每天凌晨 00:30 同步
    task_id = mgr.register(
        cron="@daily",
        query="python3 /home/ai/lingclaude/scripts/sync_openrouter_models.py",
        priority=TaskPriority.LOW
    )
    print(f"✅ 已注册 OpenRouter 模型同步任务: {task_id}")
    print(f"   频率: @daily (每天 00:30)")
    print(f"   脚本: scripts/sync_openrouter_models.py")
    # 启动调度器
    mgr.start()
    print("🚀 调度器已启动")

if __name__ == "__main__":
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("❌ 请先设置 OPENROUTER_API_KEY 环境变量")
        sys.exit(1)
    register_sync()
