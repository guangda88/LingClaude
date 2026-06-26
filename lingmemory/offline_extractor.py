# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵码 V1.0 插片：离线飞轮（crush.db历史挖掘）

出入：crush.jsonl的events in → code_trace records out
流转：从events中提取coding_rule的pattern
"""
import json
import logging
import os
import sqlite3

logger = logging.getLogger(__name__)
from pathlib import Path
from typing import Optional

from lingmemory.api import LingMemoryAPI
from lingmemory.rule_matcher import RuleMatcher


MEMBER_DB_PATHS = {
    "lingflow": "~/.crush/../lingflow/.crush/crush.db",
    "lingminopt": "~/.crush/../lingminopt/.crush/crush.db",
    "lingxi": "~/.crush/../lingxi/.crush/crush.db",
    "lingcreate": "~/.crush/../lingcreate/.crush/crush.db",
    "lingclaude": "~/.crush/crush.db",
    "lingmessage": "~/.crush/../lingmessage/.crush/crush.db",
    "lingzhi": "~/.crush/../lingzhi/.crush/crush.db",
    "lingtongask": "~/.crush/../lingtongask/.crush/crush.db",
    "lingresearch": "~/.crush/../lingresearch/.crush/crush.db",
    "zhibridge": "~/.crush/../zhibridge/.crush/crush.db",
    "lingyang": "~/.crush/../lingyang/.crush/crush.db",
    "lingweb": "~/.crush/../lingweb/.crush/crush.db",
}


def parse_parts(parts_str: str) -> list[dict]:
    try:
        return json.loads(parts_str)
    except (json.JSONDecodeError, TypeError):
        return []


def extract_error(content: str) -> Optional[str]:
    if not content:
        return None
    key = ["error", "traceback", "exception", "failed", "errno", "错误", "失败"]
    if not any(k in content.lower() for k in key):
        return None
    lines = [l.strip() for l in content.split("\n")
             if any(k in l.lower() for k in key) and len(l.strip()) < 200]
    return "\n".join(lines[:3]) if lines else content[:200]


class OfflineExtractor:
    """V1.0：从crush.db离线提取code_trace+coding_rule"""

    def __init__(self, member: str, db_path: str):
        self.member = member
        self.db_path = os.path.expanduser(db_path)
        from lingmemory.core import DB_PATH
        self.api = LingMemoryAPI(str(DB_PATH), member=member)
        self._matcher = RuleMatcher()
        self.stats = dict(sessions=0, msgs=0, errors=0, fixes=0, traces=0, rules=0)

    def close(self):
        self.api.close()

    def process(self) -> dict:
        if not os.path.exists(self.db_path):
            return self.stats
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        for s in conn.execute("SELECT DISTINCT session_id FROM messages"):
            self._process_session(conn, s["session_id"])
        conn.close()
        return self.stats

    def _process_session(self, conn, sid):
        self.stats["sessions"] += 1
        msgs = conn.execute(
            "SELECT id, role, parts, created_at FROM messages WHERE session_id=? ORDER BY created_at",
            (sid,)).fetchall()

        pending_err = None
        for msg in msgs:
            self.stats["msgs"] += 1
            for p in parse_parts(msg["parts"]):
                t, data = p.get("type"), p.get("data", {})
                if t == "tool_result":
                    name = data.get("name", "")
                    content = str(data.get("content", ""))
                    err = extract_error(content)
                    if err and name in ("bash", "edit", "multiedit", "write", "view"):
                        self.stats["errors"] += 1
                        pending_err = dict(tool=name, error=err, content=content[:500])
                    elif pending_err and name in ("edit", "multiedit", "write"):
                        self.stats["fixes"] += 1
                        self._create_trace_and_rule(sid, pending_err, name, data)
                        pending_err = None

    def _create_trace_and_rule(self, sid, err_info, fix_tool, fix_data):
        try:
            err_content = err_info["error"]
            self.api.lm.create(type="code_trace", data={
                "prompt": f"session {sid[:8]} error→fix",
                "language": "bash",
                "generated_code": err_info["content"][:200],
                "test_result": "fail",
                "fix": str(fix_data.get("content", ""))[:200] if fix_data else "",
                "member": self.member, "project": sid[:8],
                "stderr_snippet": err_content[:500],
            }, created_by=self.member)
            self.stats["traces"] += 1
        except Exception as e:
            logger.error("提取会话失败 (session_id=%s): %s", session_id, e)


def run_extraction():
    """全族离线提取入口"""
    print("灵码数据飞轮 — 离线提取")
    total = dict(sessions=0, msgs=0, errors=0, fixes=0, traces=0, rules=0)
    for member, db_path in MEMBER_DB_PATHS.items():
        p = os.path.expanduser(db_path)
        if not os.path.exists(p): continue
        e = OfflineExtractor(member, db_path)
        s = e.process()
        for k in total: total[k] += s[k]
        print(f"  [{member}] sessions={s['sessions']} msgs={s['msgs']} errors={s['errors']} fixes={s['fixes']} traces={s['traces']}")
        e.close()
    print(f"  TOTAL: sessions={total['sessions']} msgs={total['msgs']} errors={total['errors']} fixes={total['fixes']} traces={total['traces']}")
    return total


if __name__ == "__main__":
    run_extraction()
