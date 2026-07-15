#!/usr/bin/env python3
'''lingmate迁移工具：handover.yaml → lingmate type=session

用法:
  python3 migrate_handover.py                              # 预览映射 (dry-run)
  python3 migrate_handover.py --apply                       # 写入 lingmemory
  python3 migrate_handover.py --member lingclaude --apply   # 指定成员

模板输出:
  - 每段 handover.yaml → 一条 lingmate session record
  - 迁移后 handover.yaml 加 retired 标记

依赖:
  lingmemory MCP (:9530) / lm_create 工具
'''

import json
import os
import sys
import yaml
from datetime import datetime, timezone

HANDOVER_PATH = os.path.join(os.path.dirname(__file__), "..", ".lingclaude", "handover.yaml")


def _validate_path(path: str) -> str:
    """校验路径在项目目录内，防止路径遍历"""
    base = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
    real = os.path.realpath(path)
    if not real.startswith(base):
        raise ValueError(f"path outside project dir: {path}")
    return real


def parse_handover(path: str) -> dict:
    safe_path = _validate_path(path)
    with open(safe_path) as f:
        return yaml.safe_load(f)

def to_session(handover: dict, member: str) -> dict:
    """将 handover.yaml 映射为 lingmate type=session 记录"""
    meta = handover.get("meta", {})
    return {
        "type": "session",
        "member": member or meta.get("member", "unknown"),
        "data": json.dumps({
            "owner": meta.get("member", member),
            "session_id": meta.get("session"),
            "health": _health_from_handover(handover),
            "security_level": "normal",
            "token_usage": 0,
            "active_tasks": [
                {"id": t.get("id"), "status": t.get("status")}
                for t in handover.get("active_tasks", [])
            ],
            "production_log": handover.get("production_log", []),
            "blockers": handover.get("blockers", []),
        }),
    }

def _health_from_handover(h: dict) -> str:
    blockers = h.get("blockers", [])
    if any("critical" in str(b).lower() for b in blockers):
        return "abnormal"
    if any("high" in str(b).lower() for b in blockers) or len(blockers) > 3:
        return "warning"
    return "normal"

def dry_run(path: str, member: str):
    handover = parse_handover(path)
    record = to_session(handover, member)
    print("=== DRY RUN ===")
    print(f"Member: {record['member']}")
    print(f"Type:   {record['type']}")
    print(f"Data:\n{json.dumps(json.loads(record['data']), indent=2, ensure_ascii=False)}")
    print(f"\n→ 将调用 lm_create(member={record['member']}, type=session, data=...)")

def apply(path: str, member: str):
    handover = parse_handover(path)
    record = to_session(handover, member)
    print(f"创建 session 记录: member={record['member']}")
    # 实际写入通过 MCP 工具 lm_create 调用
    # 此处输出 JSON 供管道消费
    print(json.dumps(record, ensure_ascii=False))

if __name__ == "__main__":
    member = ""
    do_apply = False
    for arg in sys.argv[1:]:
        if arg == "--apply":
            do_apply = True
        elif arg.startswith("--member="):
            member = arg.split("=", 1)[1]

    if do_apply:
        apply(HANDOVER_PATH, member)
    else:
        dry_run(HANDOVER_PATH, member)
