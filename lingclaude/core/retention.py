"""有界存储原语——防止 oc 式磁盘膨胀（2026-10-07）。

背景（实测 ~/.local/share/opencode 3.7G）：
    oc 的会话文件本身只有 47M/3883 个，膨胀真凶是**无界衍生数据**
    （snapshot/ 2.5G 旧内容快照 + opencode.db+wal+shm 1.1G 消息库）。
    教训：文件型存储比数据库型容易收缩；一切追加面必须有保留策略。

lc 侧三个增长面（本机实测 2026-10-07）：
    - .lingclaude/rollouts/   294M / 9423 文件 / 16 天，每会话一文件
      （唯一消费方 checkpoint 崩溃恢复只读最新流 → 按龄删旧安全）
    - data/selfopt/failure_backlog.jsonl  9333 行中 9331 行是已消费僵尸
      （executor 每轮全量重写 → 行数线性放大 IO）
    - ~/.lingclaude/sessions/  12M / 264 文件 / 1.5 月（--continue 消费
      最近存档 → 按数量留尾部安全）

设计约束：
    - 全部 fail-open：清理失败只 debug 日志，绝不影响主流程
    - 惰性触发：挂在既有写入点（开新 rollout / executor 重写），无后台任务
    - LINGCLAUDE_RETENTION=0 全局停用（基准/调试）
    - 保守默认 + env 可调，防止误删活跃数据
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 全局开关 ────────────────────────────────────────────────────────────


def retention_enabled() -> bool:
    """LINGCLAUDE_RETENTION=0 时全局停用（基准测试/调试逃生口）。"""
    return os.environ.get("LINGCLAUDE_RETENTION", "1") != "0"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


# ── 原语 1：按龄清理目录 ────────────────────────────────────────────────


def prune_dir_by_age(
    directory: Path | str,
    max_age_days: int,
    *,
    pattern: str = "*",
    keep_min: int = 8,
) -> int:
    """删除 directory 下 mtime 早于 max_age_days 的匹配文件。

    keep_min：安全垫——按 mtime 保留最新 K 个文件不删，防止长期闲置
    目录被整目录清空（mtime 全部超龄时仍留下可追溯的最近痕迹）。
    返回删除数；任何失败 fail-open 返回已删数。
    """
    if not retention_enabled() or max_age_days <= 0:
        return 0
    try:
        d = Path(directory)
        if not d.is_dir():
            return 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        files = sorted(
            (p for p in d.glob(pattern) if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        deleted = 0
        for p in files[: max(0, len(files) - keep_min)]:
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if mtime >= cutoff:
                break  # sorted 保证后面只会更新
            try:
                p.unlink()
                deleted += 1
            except OSError as e:
                logger.debug("retention: unlink %s failed: %s", p, e)
        if deleted:
            logger.debug("retention: %s 按 %d 天龄清理 %d 个文件", d, max_age_days, deleted)
        return deleted
    except Exception:  # noqa: BLE001 — fail-open，清理绝不影响主流程
        logger.debug("retention: prune_dir_by_age failed", exc_info=True)
        return 0


# ── 原语 2：按数量清理目录 ──────────────────────────────────────────────


def prune_dir_by_count(
    directory: Path | str,
    max_files: int,
    *,
    pattern: str = "*",
    recursive: bool = False,
) -> int:
    """directory 下匹配文件超过 max_files 时，从最旧开始删到恰好 max_files。

    recursive=True 时 rglob 跨项目子目录（sessions/<项目>/<sid>.json 布局）。
    适用 --continue 只消费最近存档的面（mtime 排序留尾部）。
    """
    if not retention_enabled() or max_files <= 0:
        return 0
    try:
        d = Path(directory)
        if not d.is_dir():
            return 0
        it = d.rglob(pattern) if recursive else d.glob(pattern)
        files = sorted(
            (p for p in it if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        excess = len(files) - max_files
        if excess <= 0:
            return 0  # 关键守卫：负 excess 会变成 Python 负切片静默误删（2026-10-07 修）
        deleted = 0
        for p in files[:excess]:
            try:
                p.unlink()
                deleted += 1
            except OSError as e:
                logger.debug("retention: unlink %s failed: %s", p, e)
        if deleted:
            logger.debug("retention: %s 按数量上限 %d 清理 %d 个文件", d, max_files, deleted)
        return deleted
    except Exception:  # noqa: BLE001
        logger.debug("retention: prune_dir_by_count failed", exc_info=True)
        return 0


# ── 原语 3：jsonl 行级收缩 ──────────────────────────────────────────────


def compact_jsonl(
    path: Path | str,
    keep_days: int,
    *,
    keep_predicate=None,
) -> tuple[int, int]:
    """读入 jsonl，删除「已终态且早于 keep_days」的行后原子重写。

    keep_predicate(entry) -> True 的行无条件保留（如 pending/executing）。
    返回 (保留行数, 删除行数)；文件不存在返回 (0, 0)；失败返回 (-1, -1)
    且不动原文件（先写临时文件再 replace，绝不半写）。
    """
    p = Path(path)
    if not retention_enabled() or not p.is_file():
        return (0, 0)
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, keep_days))
        kept: list[str] = []
        dropped = 0
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = __import__("json").loads(line)
                except Exception:  # noqa: BLE001 — 坏行保留（不吞排查线索）
                    kept.append(line if line.endswith("\n") else line + "\n")
                    continue
                # 时间字段兜底；终态行优先「终态转换时间」（executed_at 等）——
                # 一条 30 天前登记、1 分钟前才执行的行诊断价值是新鲜的，
                # 回收窗口应锚定终态转换时刻而非创建时刻（2026-10-07 修）。
                terminal = str(entry.get("status", "")) in (
                    "executed", "skipped", "completed", "cancelled",
                )
                if terminal:
                    ts_raw = (
                        entry.get("executed_at") or entry.get("resolved_at")
                        or entry.get("completed_at") or entry.get("at")
                        or entry.get("created_at") or entry.get("timestamp") or ""
                    )
                else:
                    ts_raw = (
                        entry.get("at") or entry.get("created_at")
                        or entry.get("timestamp") or ""
                    )
                in_window = False
                if ts_raw:
                    try:
                        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        in_window = ts >= cutoff
                    except ValueError:
                        in_window = True  # 时间不可解析 → 保守保留
                else:
                    in_window = True  # 无时间戳 → 保守保留
                if keep_predicate is not None and keep_predicate(entry):
                    kept.append(line if line.endswith("\n") else line + "\n")
                elif terminal and not in_window:
                    dropped += 1
                else:
                    kept.append(line if line.endswith("\n") else line + "\n")
        if dropped == 0:
            return (len(kept), 0)
        import tempfile

        fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), prefix=".retention_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.writelines(kept)
            os.replace(tmp_name, p)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        logger.info("retention: %s 收缩 %d 行（保留 %d）", p, dropped, len(kept))
        return (len(kept), dropped)
    except Exception:  # noqa: BLE001 — 收缩失败原文件原样保留
        logger.debug("retention: compact_jsonl %s failed", p, exc_info=True)
        return (-1, -1)


# ── 接线点 1：rollouts 开新文件时按龄清理 ────────────────────────────────

ROLLOUT_MAX_AGE_DAYS = _env_int("LINGCLAUDE_ROLLOUT_MAX_AGE_DAYS", 7)
ROLLOUT_KEEP_MIN = _env_int("LINGCLAUDE_ROLLOUT_KEEP_MIN", 8)


def prune_rollouts_on_open(rollout_dir: Path | str) -> int:
    """rollout 开新文件时惰性清理超龄旧流（摊销：每会话一次）。

    安全性：唯一消费方（checkpoint 崩溃恢复）只读最新流；fork/revert
    派生文件 mtime 均为当前时间，不可能被超龄窗口命中。
    """
    return prune_dir_by_age(
        rollout_dir,
        ROLLOUT_MAX_AGE_DAYS,
        pattern="rollout-*.jsonl",
        keep_min=ROLLOUT_KEEP_MIN,
    )


# ── 接线点 2：backlog executor 重写时收缩僵尸行 ──────────────────────────

BACKLOG_KEEP_DAYS = _env_int("LINGCLAUDE_BACKLOG_KEEP_DAYS", 7)


def prune_backlog_on_rewrite(backlog_path: Path | str) -> tuple[int, int]:
    """executor 全量回写后收缩已消费僵尸行（pending/executing 恒保留）。"""
    return compact_jsonl(
        backlog_path,
        BACKLOG_KEEP_DAYS,
        keep_predicate=lambda e: str(e.get("status", "")) in ("pending", "executing"),
    )


# ── 接线点 3：sessions 存档按数量留尾 ────────────────────────────────────

SESSIONS_MAX_FILES = _env_int("LINGCLAUDE_SESSIONS_MAX_FILES", 500)


def prune_sessions_on_persist(sessions_root: Path | str) -> int:
    """会话存档落盘后按数量留尾（--continue 只消费最近存档）。

    recursive：存档布局是 sessions/<项目>/<sid>.json 两级目录。
    """
    return prune_dir_by_count(sessions_root, SESSIONS_MAX_FILES, pattern="*.json", recursive=True)
