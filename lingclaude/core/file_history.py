"""P1-3 项目级变更留档 — 自优化动作的回滚保障。

写法:任何自优化写动作前调用 record_change(path, source),把原文件快照到
.lingclaude/file_history/<UTC时间戳>_<文件名>,并在 manifest.jsonl 追加一条记录。
回滚:rollback(record) 用快照覆盖回原路径。

M1-a (2026-10-01) 文件级 rewind 扩展 — 工具写入面快照:
- source="tool_write" 由 ToolPipeline 在 write_scoped 工具执行前自动调用
  （见 engine/tool_pipeline.py snapshot_callback 接线）；
- MAX_SNAPSHOT_BYTES 单文件上限 / MAX_TOTAL_BYTES 总量预算（超限修剪最旧），
  env LINGCLAUDE_SNAPSHOT_MAX_BYTES 可覆盖单文件上限；
- list_changes(source) / rollback_source(path, source) 面向 /undo 命令；
  source 隔离保证工具写入回滚与 self_optimizer 留档互不踩踏。
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

HISTORY_DIR = Path(".lingclaude") / "file_history"
MANIFEST = HISTORY_DIR / "manifest.jsonl"

# M1-a: 快照体积治理。单文件超过上限不快照（fail-open，写工具照常执行）；
# 总量超预算时按 manifest 从最旧开始删快照文件（manifest 行保留 + pruned 标记）。
MAX_SNAPSHOT_BYTES = 5 * 1024 * 1024  # 5MB
MAX_TOTAL_BYTES = 200 * 1024 * 1024  # 200MB


def _snapshot_limit_bytes() -> int:
    """单文件快照上限，env LINGCLAUDE_SNAPSHOT_MAX_BYTES 可覆盖（解析失败回落默认）。"""
    raw = os.environ.get("LINGCLAUDE_SNAPSHOT_MAX_BYTES", "")
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return MAX_SNAPSHOT_BYTES


def _prune_over_budget() -> None:
    """总量超预算时从最旧快照开始删除文件（manifest 行保留 + pruned 标记）。"""
    try:
        if not MANIFEST.exists():
            return
        records: list[dict] = []
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        def _alive_total() -> int:
            return sum(
                p.stat().st_size
                for p in HISTORY_DIR.iterdir()
                if p.is_file() and p.name != MANIFEST.name
            )
        for rec in records:  # manifest 顺序即时间序，最旧在前
            if _alive_total() <= MAX_TOTAL_BYTES:
                break
            if rec.get("pruned"):
                continue
            bp = Path(rec.get("backup", ""))
            if bp.exists():
                bp.unlink()
            rec["pruned"] = True
        MANIFEST.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8",
        )
    except OSError:
        pass  # 修剪失败不影响快照主流程（fail-open）


def record_change(path: Path | str, source: str = "self_optimizer") -> Path | None:
    """快照 path 当前内容到 file_history,返回备份路径;失败返回 None(fail-open)。

    文件不存在时(新建动作)记录 tombstone(空快照),回滚时表现为删除。
    M1-a: 单文件超过 _snapshot_limit_bytes() 时跳过快照返回 None（不留记录，
    与 tombstone 语义区分——跳过 ≠ 记录为空文件）。
    """
    src = Path(path)
    try:
        if src.exists() and not src.is_dir():
            if src.stat().st_size > _snapshot_limit_bytes():
                return None
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        backup = HISTORY_DIR / f"{ts}_{src.name or 'tombstone'}"
        if src.exists():
            shutil.copy2(src, backup)
        else:
            backup.write_text("", encoding="utf-8")
        record = {
            "ts": ts,
            "source": source,
            "original": str(src),
            "backup": str(backup),
            "existed": src.exists(),
        }
        with MANIFEST.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if source == "tool_write":
            _prune_over_budget()  # 仅工具写入面触发预算检查（self_optimizer 频率低）
        return backup
    except OSError:
        return None


def rollback_last(original: Path | str) -> Path | None:
    """把 original 恢复到最近一次留档的内容;返回所用备份路径或 None。"""
    try:
        if not MANIFEST.exists():
            return None
        target = str(Path(original))
        last: dict | None = None
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("original") == target:
                last = rec
        if last is None:
            return None
        backup = Path(last["backup"])
        src = Path(last["original"])
        if last.get("existed"):
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, src)
        elif src.exists():
            src.unlink()
        return backup
    except OSError:
        return None


def list_changes(source: str | None = None, limit: int = 20) -> list[dict]:
    """M1-a: 按 source 过滤读取 manifest 记录，最新在前。

    pruned 记录仍列出（标记 pruned=True，回滚时跳过），limit 截断返回条数。
    """
    try:
        if not MANIFEST.exists():
            return []
        out: list[dict] = []
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if source is not None and rec.get("source") != source:
                continue
            out.append(rec)
        out.reverse()
        return out[:limit]
    except OSError:
        return []


def rollback_source(original: Path | str, source: str = "tool_write") -> bool:
    """M1-a: 把 original 恢复到 source 域内最近一次留档的内容。

    与 rollback_last 的区别：按 source 隔离（tool_write 不消费 self_optimizer
    的记录），且消费后删除该条 manifest 记录（undo 语义：一次回滚一个动作），
    而非 rollback_last 的「重放最近记录、记录保留」。

    返回 True=回滚执行；False=无可用记录（含记录被 pruned 的情形）。
    """
    try:
        if not MANIFEST.exists():
            return False
        target = str(Path(original))
        lines = MANIFEST.read_text(encoding="utf-8").splitlines()
        records: list[dict] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        # 从最新往回找第一条匹配 source+original 且未被 pruned 的记录
        hit_idx: int | None = None
        for i in range(len(records) - 1, -1, -1):
            rec = records[i]
            if rec.get("source") == source and rec.get("original") == target and not rec.get("pruned"):
                hit_idx = i
                break
        if hit_idx is None:
            return False
        rec = records[hit_idx]
        backup = Path(rec["backup"])
        src = Path(rec["original"])
        if not backup.exists():
            # 快照文件已被修剪/清理 → 记录标记 pruned，返回 False
            rec["pruned"] = True
            MANIFEST.write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                encoding="utf-8",
            )
            return False
        if rec.get("existed"):
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, src)
        elif src.exists():
            src.unlink()  # tombstone：快照时不存在 → 回滚即删除新建文件
        else:
            pass  # 快照时和现在都不存在 → 无事可做，视为成功
        records.pop(hit_idx)  # undo 语义：消费掉该条记录
        MANIFEST.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
            encoding="utf-8",
        )
        try:
            backup.unlink()  # 快照已消费，释放空间
        except OSError:
            pass
        return True
    except OSError:
        return False
