"""P1-3 项目级变更留档 — 自优化动作的回滚保障。

写法:任何自优化写动作前调用 record_change(path, source),把原文件快照到
.lingclaude/file_history/<UTC时间戳>_<文件名>,并在 manifest.jsonl 追加一条记录。
回滚:rollback(record) 用快照覆盖回原路径。
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

HISTORY_DIR = Path(".lingclaude") / "file_history"
MANIFEST = HISTORY_DIR / "manifest.jsonl"


def record_change(path: Path | str, source: str = "self_optimizer") -> Path | None:
    """快照 path 当前内容到 file_history,返回备份路径;失败返回 None(fail-open)。

    文件不存在时(新建动作)记录 tombstone(空快照),回滚时表现为删除。
    """
    src = Path(path)
    try:
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
