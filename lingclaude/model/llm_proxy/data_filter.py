from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SENSITIVE_PATTERNS = [
    re.compile(r'(api[_-]?key|apikey)\s*[:=]\s*\S+', re.IGNORECASE),
    re.compile(r'(password|passwd|pwd)\s*[:=]\s*\S+', re.IGNORECASE),
    re.compile(r'(token|secret|bearer)\s*[:=]\s*\S+', re.IGNORECASE),
    re.compile(r'(Authorization)\s*:\s*Bearer\s+\S+', re.IGNORECASE),
]

REDACTED = "******REDACTED******"


@dataclass
class AuditEntry:
    timestamp: float
    caller: str
    purpose: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    status: str


class DataFilter:
    def __init__(self, max_audit_entries: int = 10000, retention_seconds: float = 604800.0) -> None:
        self._max_entries = max_audit_entries
        self._retention = retention_seconds
        self._audit_log: deque[AuditEntry] = deque(maxlen=max_audit_entries)
        self._lock = threading.Lock()

    def sanitize(self, text: str) -> str:
        for pattern in SENSITIVE_PATTERNS:
            text = pattern.sub(lambda m: m.group(0).split("=")[0] + "=" + REDACTED if "=" in m.group(0) else m.group(0).split(":")[0] + ":" + REDACTED, text)
        return text

    def sanitize_headers(self, headers: dict[str, str]) -> dict[str, str]:
        out = {}
        for k, v in headers.items():
            if k.lower() in ("authorization", "api-key", "x-api-key"):
                out[k] = REDACTED
            else:
                out[k] = v
        return out

    def record(self, entry: AuditEntry) -> None:
        with self._lock:
            self._audit_log.append(entry)

    def query(
        self,
        caller: str | None = None,
        purpose: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        now = time.monotonic()
        cutoff = now - self._retention
        results: list[AuditEntry] = []
        with self._lock:
            for e in reversed(self._audit_log):
                if e.timestamp < cutoff:
                    break
                if caller and e.caller != caller:
                    continue
                if purpose and e.purpose != purpose:
                    continue
                if since and e.timestamp < since:
                    continue
                results.append(e)
                if len(results) >= limit:
                    break
        return results

    def stats(self) -> dict:
        with self._lock:
            total = len(self._audit_log)
            if total == 0:
                return {"total": 0}
            by_caller: dict[str, int] = {}
            by_status: dict[str, int] = {}
            tokens_in = 0
            tokens_out = 0
            for e in self._audit_log:
                by_caller[e.caller] = by_caller.get(e.caller, 0) + 1
                by_status[e.status] = by_status.get(e.status, 0) + 1
                tokens_in += e.input_tokens
                tokens_out += e.output_tokens
            return {
                "total": total,
                "by_caller": by_caller,
                "by_status": by_status,
                "total_input_tokens": tokens_in,
                "total_output_tokens": tokens_out,
            }
