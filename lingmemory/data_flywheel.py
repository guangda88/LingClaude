# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵码 数据飞轮 — 灵元V1.0薄主干

飞轮本质：
  record（出入：编码信息进code_trace）
  extract（流转：从events提取coding_rule的records）

插片：
  Sanitize（脱敏）
  RuleMatcher（错误模式匹配）
"""
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Optional

from lingmemory.core import DB_PATH
from lingmemory.api import LingMemoryAPI
from lingmemory.sanitize import Sanitize
from lingmemory.rule_matcher import RuleMatcher


class DataFlywheel:
    """灵码数据飞轮 — 编码轨迹采集+规律提取"""

    def __init__(self, db_path=DB_PATH, member: str = "system"):
        self.api = LingMemoryAPI(db_path, member=member)
        self.member = member
        self._pending: dict[str, dict] = {}
        self._sanitize = Sanitize()
        self._matcher = RuleMatcher()

    def close(self):
        self.api.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ============================================================
    # begin — 开始记录一次编码操作
    # ============================================================
    def begin(self, prompt: str, language: str, file_path: str | None = None,
              project: str | None = None, model_used: str | None = None) -> str:
        key = file_path or f"trace_{int(time.time() * 1000)}"
        self._pending[key] = {
            "prompt": self._sanitize.text(prompt),
            "language": language,
            "file_path": file_path,
            "project": project,
            "model_used": model_used,
            "start_time": time.time(),
        }
        return key

    # ============================================================
    # record — 出入：采集一次code_trace
    # ============================================================
    def record(self, prompt: str, language: str, generated_code: str,
               test_result: str, fix: str | None = None,
               fix_strategy: str | None = None,
               stderr_snippet: str | None = None,
               **kwargs) -> str:
        data = {
            "prompt": self._sanitize.text(prompt),
            "language": language,
            "generated_code": self._sanitize.text(generated_code),
            "test_result": test_result,
            "member": self.member,
        }
        if fix:
            data["fix"] = self._sanitize.text(fix)
        if fix_strategy:
            data["fix_strategy"] = fix_strategy
        if stderr_snippet:
            data["stderr_snippet"] = self._sanitize.text(stderr_snippet[:500])

        for key in ("quality_signal", "model_used", "tools_used", "rag_context",
                     "file_path", "project", "exit_code", "duration_ms"):
            val = kwargs.get(key)
            if val is not None:
                data[key] = val

        return self.api.lm.create(type="code_trace", data=data, created_by=self.member)

    # ============================================================
    # extract_rule — 流转：从code_trace提取coding_rule
    # ============================================================
    def extract_rule(self, trace_id: str) -> Optional[str]:
        trace = self.api.lm.get(trace_id)
        if not trace or not trace["data"].get("fix"):
            return None

        stderr = trace["data"].get("stderr_snippet", "") or ""
        rule_data = self._matcher.match(stderr)
        if not rule_data:
            return None

        # 查重
        existing = self.api.lm.query(type="coding_rule", limit=1000)
        for item in existing["items"]:
            if item["data"].get("rule") == rule_data["rule"]:
                ev = item["data"].get("evidence", [])
                if trace_id not in ev:
                    ev.append(trace_id)
                    item["data"]["evidence"] = ev
                    self.api.lm.conn.execute(
                        "UPDATE records SET data=? WHERE id=?",
                        (json.dumps(item["data"], ensure_ascii=False), item["id"]))
                    self.api.lm.conn.commit()
                    if len(ev) >= 3 and item["state"] == "hypothesized":
                        self.api.lm.transition(item["id"], "evidence_sufficient", actor=self.member)
                return item["id"]

        return self.api.lm.create(type="coding_rule", data={
            "rule": rule_data["rule"], "evidence": [trace_id],
            "category": rule_data["category"], "confidence": 0.3,
        }, created_by=self.member)

    # ============================================================
    # record_and_extract — 飞轮主入口
    # ============================================================
    def record_and_extract(self, prompt: str, language: str, generated_code: str,
                           test_result: str, fix: str | None = None, **kwargs) -> dict:
        trace_id = self.record(prompt, language, generated_code, test_result, fix, **kwargs)
        rule_id = self.extract_rule(trace_id) if (fix and test_result in ("fail", "error")) else None
        return {"trace_id": trace_id, "rule_id": rule_id}

    # ============================================================
    # 便捷方法
    # ============================================================
    def record_edit_test(self, prompt: str, file_path: str, language: str,
                         edit_result: dict, test_result: dict, **kwargs) -> str:
        ts = "pass" if test_result.get("passed") else ("error" if test_result.get("exit_code", 1) > 1 else "fail")
        return self.record(prompt=prompt, language=language,
                          generated_code=edit_result.get("code", ""),
                          test_result=ts, file_path=file_path,
                          exit_code=test_result.get("exit_code"),
                          stderr_snippet=test_result.get("stderr"), **kwargs)

    def record_from_bash(self, prompt: str, command: str, exit_code: int,
                         stdout: str, stderr: str, **kwargs) -> str:
        ts = "pass" if exit_code == 0 else ("error" if exit_code > 1 else "fail")
        return self.record(prompt=prompt, language="bash",
                          generated_code=command, test_result=ts,
                          stderr_snippet=stderr[:500] if stderr else None, **kwargs)

    def add_quality_signal(self, trace_id: str, source: str, score: float, detail: dict | None = None):
        """给已有code_trace追加质量信号"""
        record = self.api.lm.get(trace_id)
        if not record:
            raise ValueError(f"trace not found: {trace_id}")
        data = record["data"]
        qs = data.get("quality_signal", {})
        qs[source] = {"score": score, "detail": detail or {}}
        data["quality_signal"] = qs
        self.api.lm.conn.execute(
            "UPDATE records SET data=?, updated_at=? WHERE id=?",
            (json.dumps(data, ensure_ascii=False),
             datetime.fromtimestamp(time.time(), tz=timezone.utc).isoformat(), trace_id))
        self.api.lm.conn.execute(
            "INSERT INTO events (record_id, event_type, from_state, to_state, actor, data, timestamp) "
            "VALUES (?, 'quality_tag', ?, ?, ?, ?, ?)",
            (trace_id, record["state"], record["state"], self.member,
             json.dumps({"source": source, "score": score}),
             datetime.fromtimestamp(time.time(), tz=timezone.utc).isoformat()))
        self.api.lm.conn.commit()

    def get_stats(self) -> dict:
        rows = self.api.lm.conn.execute(
            "SELECT json_extract(data, '$.test_result') as r, COUNT(*) as c "
            "FROM records WHERE type='code_trace' GROUP BY r").fetchall()
        by_result = {r["r"]: r["c"] for r in rows}
        total = sum(by_result.values())

        by_lang = {}
        lang_rows = self.api.lm.conn.execute(
            "SELECT json_extract(data, '$.language') as l, COUNT(*) as c "
            "FROM records WHERE type='code_trace' GROUP BY l").fetchall()
        for r in lang_rows:
            by_lang[r["l"]] = r["c"]

        return {
            "total_traces": total,
            "by_test_result": by_result,
            "by_language": by_lang,
            "pass_rate": by_result.get("pass", 0) / total if total else 0,
        }
