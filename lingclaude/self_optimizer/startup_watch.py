"""启动耗时自优化哨兵（2026-10-07）。

自优化目标：``lc`` 冷启动 < 3s（``TriggerConfig.startup_time_threshold_s``）。
启动实测耗时超过阈值时，追加一条 ``startup_slow`` 任务到
``data/selfopt/failure_backlog.jsonl``，交给 backlog_executor / 自优化闭环消费。

设计约束（对齐 backlog_executor 的铁律 + L1 fail-soft 语义）：

- **零依赖、零重活**：只用 stdlib + 一处 json 追加写。绝不走
  OptimizationDaemon（其构造含 AST 全仓扫描——把 daemon 塞进启动路径会
  让「测启动耗时」本身拖慢启动，适得其反）。
- **fail-soft**：backlog 目录不可写 / json 损坏 / 任何异常一律只
  log.warning，绝不影响 ``lc`` 正常启动。
- **幂等**：进程内只判定一次；backlog 用 ``cluster_key``（启动日期）去重，
  同日多次慢启动只追加一次，避免刷爆 backlog。
- **可旁路**：``LINGCLAUDE_DISABLE_STARTUP_WATCH=1`` 时整个哨兵变 no-op
  （基准测试 / 调试 / 复现慢启动场景时用）。

打点约定：``cli/app.py::main()`` 在进程最早期调 ``mark()``
（import app 模块即视为进程起点，无需包一层 wrapper——console_scripts
生成的 shim 第一时间 import 本模块，误差 = shim 本身，可忽略）。
``run`` / ``optimize`` 子命令完成初始化、即将进入交互/执行前调
``maybe_record_run_start()`` 收 run-phase 起点，进程退出前
``atexit`` 钩子自动判定并登记。
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROCESS_START = time.monotonic()
_RUN_START: float | None = None
_RECORDED = False  # 进程内幂等：至多登记一次

DEFAULT_BACKLOG = Path("data/selfopt/failure_backlog.jsonl")
# cluster_key 同日去重粒度（%Y-%m-%d）：同一天无论慢启动几次，backlog 只留一条
_DEDUP_DATE_FMT = "%Y-%m-%d"


def mark() -> None:
    """显式标记进程起点。import 本模块时已自动取一次；调用方需要
    排除 shim import 之前的误差时可手动重标（无条件覆盖）。"""
    global _PROCESS_START
    _PROCESS_START = time.monotonic()


def maybe_record_run_start() -> None:
    """run 子命令完成初始化、即将进入交互/执行前调用，记录 run-phase 起点。"""
    global _RUN_START
    _RUN_START = time.monotonic()


def _disabled() -> bool:
    return os.environ.get("LINGCLAUDE_DISABLE_STARTUP_WATCH", "") == "1"


def _load_threshold() -> float:
    """读 ``TriggerConfig.startup_time_threshold_s``；失败退回默认 3.0s。"""
    try:
        from lingclaude.core.config import TriggerConfig
        return float(TriggerConfig().startup_time_threshold_s)
    except Exception as e:  # noqa: BLE001 —— 阈值读取失败不阻断哨兵
        logger.debug("startup threshold 读取失败，用默认 3.0s: %s", e)
        return 3.0


def _startup_seconds() -> float:
    """取判定口径：优先 run-phase（run 子命令已打点），否则退进程启动。"""
    base = _RUN_START if _RUN_START is not None else _PROCESS_START
    return time.monotonic() - base


def _append_backlog(entry: dict[str, Any], backlog_path: Path) -> None:
    """幂等追加：同 cluster_key（启动日期）已存在则跳过。"""
    backlog_path.parent.mkdir(parents=True, exist_ok=True)
    dedup_key = entry["cluster_key"]
    if backlog_path.exists():
        try:
            with backlog_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        existing = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # 坏行不阻断（与 backlog_executor 读取语义一致）
                    if existing.get("cluster_key") == dedup_key:
                        logger.info(
                            "startup_watch: 同日起点已登记（%s），跳过重复追加", dedup_key
                        )
                        return
        except OSError as e:
            logger.warning("startup_watch: backlog 读取失败（去重跳过）: %s", e)
    try:
        with backlog_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("startup_watch: backlog 追加失败: %s", e)


def _record() -> None:
    """判定 + 登记。全程 fail-soft。可由 atexit 调用，也可由 run 子命令
    初始化完成后显式调用（覆盖 os._exit 硬退出路径）。未超阈值时不置
    _RECORDED——留给后续更大耗时的判定机会。"""
    global _RECORDED
    if _RECORDED or _disabled():
        return
    try:
        elapsed = _startup_seconds()
        threshold = _load_threshold()
        if threshold <= 0:
            return  # 0 = 停用该触发器
        if elapsed <= threshold:
            logger.debug("startup_watch: 启动 %.2fs ≤ 阈值 %.2fs，不登记", elapsed, threshold)
            return
        _RECORDED = True  # 只有真写入 backlog 才幂等，留给 _cmd_run 后判机会
        today = datetime.now().strftime(_DEDUP_DATE_FMT)
        entry = {
            "at": datetime.now().isoformat(),
            "type": "startup_slow",
            "status": "pending",
            "cluster_key": f"startup::{today}",
            "tool": "startup_watch",
            "occurrences": 1,
            "sessions": 1,
            "current_value_s": round(elapsed, 3),
            "threshold_s": threshold,
            "hypothesis": (
                f"lc 启动耗时 {elapsed:.2f}s 超过自优化目标 {threshold:.2f}s"
                "——需 profile 定位 import / 初始化热点并压缩"
            ),
            "fix_suggestion": (
                "1) python -X importtime -m lingclaude.cli.app --help 抓 import 分布；"
                "2) cProfile 下钻 _cmd_run 初始化段（config/provider/QueryEngine）；"
                "3) 重复 I/O 加进程内缓存（参考 2026-10-07 vault._load_root_key 修复）"
            ),
        }
        _append_backlog(entry, DEFAULT_BACKLOG)
        logger.warning(
            "startup_watch: 启动 %.2fs 超阈值 %.2fs → 已登记自优化任务（%s）",
            elapsed, threshold, DEFAULT_BACKLOG,
        )
    except Exception as e:  # noqa: BLE001 —— 哨兵自身故障绝不影响启动/退出
        logger.warning("startup_watch: 判定/登记异常（已忽略）: %s", e)


_INSTALLED = False


def install() -> None:
    """装配 atexit 钩子。幂等（重复调用只装一次）。"""
    global _INSTALLED
    if _INSTALLED or _disabled():
        return
    atexit.register(_record)
    _INSTALLED = True


def _reset_for_tests() -> None:
    """测试专用：清空进程内状态。生产代码不得调用。"""
    global _PROCESS_START, _RUN_START, _RECORDED
    _PROCESS_START = time.monotonic()
    _RUN_START = None
    _RECORDED = False
