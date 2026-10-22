"""跨会话全局预算账本（P3，2026-10-22）。

痛点（BUDGET_GUARDRAILS §四明示边界）：L1-L6 六道闸全部是**单会话**口径；
R9-4 成本总闸只覆盖**经编排器队列**的任务。手动直接开 N 个会话时各会话
预算独立计数，没有「宿主今天所有会话合计上限」。

设计：
- 共享账本文件 ``<project>/.lingclaude/global_budget.json``，按 UTC 日分桶；
- 每会话惰性登记（session_id = pid+启动时刻），模型调用增量原子入账；
- 全局日额度超限 → 所有接入会话的 record_model_call 拒绝增量（计数冻结，
  会话级 L2 PAUSE 语义接管：冻结即超支，gate 会挂起等裁决）；
- flock + tmp 原子替换，多进程并发安全；账本损坏 fail-open（不计入，不反噬）；
- 零配置默认关闭，policy yaml ``global_budget: {daily_token_cap: N}`` 启用。
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_LEDGER_FILENAME = "global_budget.json"
_ledger_lock_env = "LINGCLAUDE_GLOBAL_BUDGET_LEDGER"  # 测试覆盖入口


def ledger_path(project_dir: str | os.PathLike | None = None) -> Path:
    override = os.environ.get(_ledger_lock_env, "").strip()
    if override:
        return Path(override)
    root = Path(project_dir) if project_dir else Path.cwd()
    return root / ".lingclaude" / _LEDGER_FILENAME


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _session_id() -> str:
    boot = int(time.time() - time.clock_gettime(time.CLOCK_PROCESS_CPUTIME_ID))
    return f"pid{os.getpid()}@{boot}"


def load_policy_cap() -> int:
    """全局日额度（tokens）；0/缺省=不限。policy yaml 热载复用。"""
    try:
        from lingclaude.core import policy_loader

        raw = policy_loader.get("session_budget") or {}
        gb = raw.get("global_budget") or {}
        return max(0, int(gb.get("daily_token_cap", 0) or 0))
    except Exception:  # noqa: BLE001 — 策略读取失败按不限（fail-open）
        return 0


def read_day(day: str | None = None) -> dict:
    """读取某 UTC 日桶（缺省今天）；账本缺失/损坏 → 空桶（fail-open）。"""
    day = day or _utc_day()
    try:
        p = ledger_path()
        if not p.is_file():
            return {"day": day, "total": 0, "sessions": {}}
        data = json.loads(p.read_text(encoding="utf-8"))
        bucket = data.get(day) or {}
        if not isinstance(bucket, dict):
            return {"day": day, "total": 0, "sessions": {}}
        return {
            "day": day,
            "total": int(bucket.get("total", 0) or 0),
            "sessions": dict(bucket.get("sessions") or {}),
        }
    except Exception:  # noqa: BLE001
        logger.debug("global_budget 读取失败（fail-open）", exc_info=True)
        return {"day": day, "total": 0, "sessions": {}}


def add_tokens(delta: int, cap: int | None = None) -> tuple[bool, int]:
    """原子入账并判定额度。返回 (allowed, day_total_after)。

    allowed=False 表示当日全局额度已超——调用方应停止再计增量
    （计数冻结；会话级 PAUSE 语义接管挂起）。
    """
    cap = load_policy_cap() if cap is None else cap
    if cap <= 0 or delta <= 0:
        return True, read_day().get("total", 0)
    day = _utc_day()
    p = ledger_path()
    lock_path = p.with_suffix(".lock")
    try:
        import fcntl

        p.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w", encoding="utf-8") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                data: dict = {}
                if p.is_file():
                    try:
                        data = json.loads(p.read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001 — 损坏账本重建（丢当日账，可接受）
                        data = {}
                if not isinstance(data, dict):
                    data = {}
                bucket = data.get(day) or {"total": 0, "sessions": {}}
                total = int(bucket.get("total", 0) or 0)
                allowed = total + delta <= cap
                if allowed:
                    total += delta
                    sid = _session_id()
                    sessions = bucket.get("sessions") or {}
                    sessions[sid] = int(sessions.get(sid, 0) or 0) + delta
                    data[day] = {"total": total, "sessions": sessions}
                    # 只留最近 7 天桶（防无限增长）
                    if len(data) > 9:
                        for k in sorted(data.keys())[: len(data) - 7]:
                            if isinstance(data[k], dict):
                                del data[k]
                    tmp = p.with_suffix(".json.tmp")
                    tmp.write_text(
                        json.dumps(data, ensure_ascii=False), encoding="utf-8"
                    )
                    os.replace(tmp, p)
                return allowed, total
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
    except Exception:  # noqa: BLE001 — 账本故障绝不反噬会话运行
        logger.debug("global_budget 入账失败（fail-open）", exc_info=True)
        return True, read_day().get("total", 0)
