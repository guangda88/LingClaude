#!/usr/bin/env python3
"""更新灵犀讨论串 - 说明发现"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone

# 读取讨论串
discussion_path = Path.home() / ".lingmessage" / "discussions" / "disc_20260406222049.json"
discussion = json.loads(discussion_path.read_text(encoding="utf-8"))

# 准备新消息内容
content_path = Path("/tmp/lingxi_discovery_content.md")
content = content_path.read_text(encoding="utf-8") if content_path.exists() else "Content not found"

# 添加新消息
timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
discussion["messages"].append({
    "id": f"msg_{timestamp}",
    "from_id": "lingclaude",
    "from_name": "灵克",
    "topic": discussion["topic"],
    "content": content,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "reply_to": None,
    "tags": ["source:real", "mcp", "architecture", "solution"],
    "source_type": "real"
})

# 更新讨论状态
discussion["updated_at"] = datetime.now(timezone.utc).isoformat()

# 保存讨论
discussion_path.write_text(json.dumps(discussion, ensure_ascii=False, indent=2), encoding="utf-8")

print("=" * 60)
print("✓ 讨论串已更新")
print("=" * 60)
print(f"\n讨论串 ID: disc_20260406222049")
print(f"消息数: {len(discussion['messages'])}")
print(f"\n查看讨论:")
print(f"  cat ~/.lingmessage/discussions/disc_20260406222049.json")
