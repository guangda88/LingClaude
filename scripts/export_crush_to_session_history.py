from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def export_crush_sessions_to_history(
    crush_db: Path,
    output_path: Path,
    project_filter: str = "LingClaude",
    since_hours: int = 24,
) -> int:
    if not crush_db.exists():
        print(f"Crush DB not found: {crush_db}")
        return 0

    conn = sqlite3.connect(str(crush_db))
    conn.row_factory = sqlite3.Row

    cutoff_ts = int(
        datetime.now(timezone.utc).timestamp() - since_hours * 3600
    )

    rows = conn.execute(
        """
        SELECT m.parts, m.role, m.created_at, s.title
        FROM messages m
        JOIN sessions s ON m.session_id = s.id
        WHERE s.created_at >= ?
          AND m.role = 'user'
          AND s.id NOT LIKE '%call_%'
        ORDER BY m.created_at ASC
        """,
        (cutoff_ts,),
    ).fetchall()

    conn.close()

    history: list[dict[str, str]] = []
    if output_path.exists():
        try:
            raw = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                history = raw
        except (json.JSONDecodeError, KeyError):
            pass

    existing_queries = {h.get("query", "") for h in history}
    added = 0

    for row in rows:
        try:
            parts = json.loads(row["parts"]) if isinstance(row["parts"], str) else row["parts"]
        except (json.JSONDecodeError, TypeError):
            continue

        text = ""
        for part in parts:
            if isinstance(part, dict):
                data = part.get("data", {})
                if isinstance(data, dict):
                    t = data.get("text", "")
                    if t:
                        text = t
                        break

        if not text or text in existing_queries or text.startswith("/"):
            continue

        ts = datetime.fromtimestamp(row["created_at"], tz=timezone.utc).isoformat()
        history.append({
            "query": text[:200],
            "title": row["title"] or text[:80],
            "timestamp": ts,
            "created_at": ts,
            "session_id": f"crush_export_{row['created_at']}",
            "source": "crush",
        })
        existing_queries.add(text)
        added += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return added


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    crush_db = project_root / ".crush" / "crush.db"
    output_path = project_root / "data" / "session_history.json"

    added = export_crush_sessions_to_history(crush_db, output_path)
    print(f"Exported {added} entries to {output_path}")
