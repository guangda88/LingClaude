"""Long-task observability sink (JSONL).

This is intentionally local and best-effort: metrics must never make an agent
turn fail. The CLI appends one record per completed/recovered turn.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_WRITE_LOCK = threading.Lock()
DEFAULT_METRICS_PATH = Path(".lingclaude/long_task_metrics.jsonl")


def append_long_task_metrics(
    data: dict[str, Any],
    *,
    path: str | Path = DEFAULT_METRICS_PATH,
) -> bool:
    """Append one JSONL metric record; return success without raising."""
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **data,
    }
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        with _WRITE_LOCK, target.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        return True
    except (OSError, TypeError, ValueError):
        return False
