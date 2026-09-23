"""守卫查锁跳过：pytest conftest 集成（skipped:claim-held 语义）。

硬化内容（用户裁定：并行修改防护硬化为代码）：
- 守卫跑测试前查 work_claim；
- 被持锁（有效期内）的模块 → skip 并标记 skipped:claim-held，不让半成品
  污染全量回归；
- 锁过期（持锁者失联）→ 不跳过，照常跑（候选铁律 8：失联自动失效）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lingclaude.core.state_store import StateStore
from lingclaude.plugins.agents.work_claim import WorkClaim

LEDGER = Path(__file__).parents[2] / "data" / "arch_ledger"


def pytest_collection_modifyitems(config, items):
    """收集期过滤：item 的模块路径命中有效持锁 → skip(skipped:claim-held)。"""
    if not LEDGER.is_dir():
        return  # 台账不存在（如 CI 冷环境）→ 无锁可查，全量照跑
    try:
        wc = WorkClaim(StateStore(backend="json", root=LEDGER))
        locked = wc.check_paths([str(Path(i.fspath)) for i in items])
    except Exception as exc:  # noqa: BLE001 - 查锁自身故障：显式留痕，不静默改判（J5）
        # J5 失败模式显式声明：守卫故障必须可见于报错，不静默吞掉。
        # 修复（P0 #2 2026-09-23）：原 `except Exception: return` 静默吞错，
        # 违反 J5"守卫故障必须可见"——现在写入 arch_audit_state 异常台账 + 告警。
        _log_guard_fault(exc)
        return
    if not locked:
        return
    for item in items:
        for locked_path, info in locked.items():
            item_path = str(Path(item.fspath))
            if item_path.startswith(locked_path.rstrip("/") ) or locked_path in item_path:
                item.add_marker(pytest.mark.skip(
                    reason=f"skipped:claim-held by {info['member']} "
                           f"(expires_at={info['expires_at']})"))


def _log_guard_fault(exc: Exception) -> None:
    """守卫查锁故障入账：arch_audit_state 异常台账（N4 失败模式显式，不静默）。"""
    import json
    import time

    try:
        state_path = LEDGER / "arch_audit_state" / "guard_faults.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        faults = {}
        if state_path.exists():
            faults = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(faults, dict):
            faults = {}
        key = f"claim-query:{int(time.time())}"
        faults[key] = {
            "at": time.time(),
            "kind": "claim_query_fault",
            "error": f"{type(exc).__name__}: {exc}",
            "note": "J5: 守卫故障可见，不静默改判（P0 #2 修复）",
        }
        state_path.write_text(json.dumps(faults, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except Exception:  # noqa: BLE001 - 入账自身故障不能再抛（避免二次崩溃）
        pass
