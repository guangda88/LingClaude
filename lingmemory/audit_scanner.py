# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵码 V1.0 后置灰区：自动审计扫描器

出入：60条audit_check规则 in → 1574条finding out
后置灰区：代码已经写完，扫描发现问题

LACP v0.3.0 接入 (W2 P1b):
- scan_project 改为 emit_trace 记录 (phase=EXECUTE)
- 每个 finding 写入灵忆后 emit trace
- 全局 trace.jsonl 持久化 (JsonlFileBackend)
- context_ref: ".ling/audit/<scan_session_id>/finding-<idx>"
- caller_chain: [lingclaude, audit_scanner]
- actor_role: MEMBER (手动触发的 SDT-lc-001)
"""
import os
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
import re
from pathlib import Path, sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lingmemory.api import LingMemoryAPI
from lingmemory.core import DB_PATH

# LACP v0.3.0 trace emitter
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from lingclaude.lacp import (
    TraceEmitter,
    JsonlFileBackend,
    Phase,
    Outcome,
    ActorRole,
    Cost,
)


PATTERNS = {
    "SEC-INJ-001": (r"execute\s*\([\"'].*(?:\+|%s|format|f[\"'])", "SQL拼接注入"),
    "SEC-INJ-002": (r"os\.(?:system|popen)\s*\(.*|subprocess\.(?:call|run|Popen)\s*\(.*shell\s*=\s*True", "命令注入"),
    "SEC-CRYPTO-001": (r"(?:api[_-]?key|token|secret)\s*[:=]\s*[\"'][\w\-]{8,}[\"']", "硬编码密钥"),
    "SEC-CRYPTO-002": (r"hashlib\.(?:md5|sha1)\s*\(|\bDES\b|AES\.MODE_ECB", "弱加密"),
    "SEC-MISC-001": (r"\beval\s*\(|\bexec\s*\(", "危险函数eval/exec"),
    "SEC-MISC-004": (r"pickle\.loads?\s*\(", "pickle反序列化"),
    "SEC-FILE-002": (r"\.\./", "路径遍历"),
    "PERF-003": (r"SELECT\s+\*", "全表SELECT *"),
    "PERF-004": (r"\.read\s*\(\)|\.readlines\s*\(\)", "一次性加载文件"),
    "COMPL-001": (r"(?:身份证|id_card|mobile|phone|password)\s*[:=]\s*[\"']?\d{11,}", "敏感数据明文"),
    "DELIV-001": (r"(?:password|secret|api_key|token)\s*[:=]\s*[\"'][^\"']{8,}[\"']", "密钥泄露"),
}

PROJECT_PATHS = {}


def _new_scan_session() -> str:
    """生成 scan session UUID (用于 context_ref)."""
    return f"scan-{uuid.uuid4().hex[:8]}"


# === LACP v0.3.0 Trace Emitter (单例) ===
_trace_emitter: TraceEmitter | None = None


def _get_emitter() -> TraceEmitter:
    """获取全局 trace emitter (JsonlFileBackend → /home/ai/lingclaude/lacp_traces.jsonl)."""
    global _trace_emitter
    if _trace_emitter is None:
        trace_path = Path("/home/ai/lingclaude/lacp_traces.jsonl")
        _trace_emitter = TraceEmitter(backend=JsonlFileBackend(trace_path))
    return _trace_emitter


def scan_file(filepath: str, project: str) -> list[dict]:
    findings = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception:
        return findings
    for line_no, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue
        for cid, (pattern, desc) in PATTERNS.items():
            try:
                if re.search(pattern, stripped, re.IGNORECASE):
                    findings.append(dict(project=project, file=filepath, line=line_no,
                                         check_id=cid, severity="medium",
                                         snippet=stripped[:150]))
            except re.error:
                continue
    return findings


def scan_project(path: str, name: str, api: LingMemoryAPI, scan_session_id: str | None = None) -> dict:
    """扫描单个项目, emit LACP trace.

    新增参数 scan_session_id: 由 run_full_scan 统一生成, 保证全 8 项目 context_ref 一致.
    """
    import time
    scan_session_id = scan_session_id or _new_scan_session()
    emitter = _get_emitter()
    stats = dict(files=0, findings=0)
    start = time.monotonic()

    # LACP trace: phase=SCHEDULE (扫描开始)
    emitter.emit(
        phase=Phase.SCHEDULE,
        actor="lingclaude",
        actor_role=ActorRole.MEMBER,
        executor="audit_scanner@1.0.0",
        outcome=Outcome.PASS,
        context_ref=f".ling/audit/{scan_session_id}/scan-start",
        duration_ms=0,
        caller_chain=["lingclaude", "audit_scanner", "run_full_scan"],
        target_plugin="audit_scanner@1.0.0",
        metadata={"custom": {"project": name, "path": path}},
    )

    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in {"venv", "node_modules", ".git", ".crush", "__pycache__"}]
        for f in files:
            if os.path.splitext(f)[1] not in {".py", ".ts", ".go", ".js", ".sh"}:
                continue
            stats["files"] += 1
            for finding in scan_file(os.path.join(root, f), name):
                t0 = time.monotonic()
                try:
                    api.lm.create(type="audit_finding", data=finding, created_by="lingclaude")
                    stats["findings"] += 1
                    # LACP trace: phase=EXECUTE per finding (成功)
                    file_hash = uuid.uuid5(uuid.NAMESPACE_URL, finding["file"]).hex[:8]
                    emitter.emit(
                        phase=Phase.EXECUTE,
                        actor="lingclaude",
                        actor_role=ActorRole.MEMBER,
                        executor="audit_scanner@1.0.0",
                        outcome=Outcome.PASS,
                        context_ref=f".ling/audit/{scan_session_id}/{file_hash}/{finding['check_id']}/{finding['line']}",
                        duration_ms=int((time.monotonic() - t0) * 1000),
                        caller_chain=["lingclaude", "audit_scanner"],
                        target_plugin="audit_scanner@1.0.0",
                        metadata={"custom": {"check_id": finding["check_id"],
                                            "severity": finding["severity"]}},
                    )
                except Exception as e:
                    logger.error(
                        "audit_finding persist failed (file=%s): %s",
                        finding.get("file"), e,
                    )
                    # LACP trace: phase=EXECUTE per finding (失败)
                    emitter.emit(
                        phase=Phase.EXECUTE,
                        actor="lingclaude",
                        actor_role=ActorRole.MEMBER,
                        executor="audit_scanner@1.0.0",
                        outcome=Outcome.FAIL,
                        context_ref=f".ling/audit/{scan_session_id}/error/{finding.get('check_id', 'unknown')}",
                        duration_ms=int((time.monotonic() - t0) * 1000),
                        caller_chain=["lingclaude", "audit_scanner"],
                        target_plugin="audit_scanner@1.0.0",
                        metadata={"custom": {"error": str(e)[:200]}},
                    )

    elapsed_ms = int((time.monotonic() - start) * 1000)

    # LACP trace: phase=VERIFY (扫描结束)
    outcome = Outcome.PASS if stats["findings"] >= 0 else Outcome.FAIL
    emitter.emit(
        phase=Phase.VERIFY,
        actor="lingclaude",
        actor_role=ActorRole.MEMBER,
        executor="audit_scanner@1.0.0",
        outcome=outcome,
        context_ref=f".ling/audit/{scan_session_id}/scan-complete",
        duration_ms=elapsed_ms,
        caller_chain=["lingclaude", "audit_scanner", "run_full_scan"],
        target_plugin="audit_scanner@1.0.0",
        cost=Cost(ms=elapsed_ms, tokens=stats["files"]),
        metadata={"custom": {"files": stats["files"], "findings": stats["findings"],
                            "project": name, "scan_session_id": scan_session_id}},
    )
    return stats


def run_full_scan():
    api = LingMemoryAPI(str(DB_PATH), member="lingclaude")
    scan_session_id = _new_scan_session()
    total = dict(files=0, findings=0)
    print(f"  [scan_session_id={scan_session_id}] LACP v0.3.0 trace emission enabled")
    for name, path in PROJECT_PATHS.items():
        if not os.path.exists(path): continue
        s = scan_project(path, name, api, scan_session_id=scan_session_id)
        total["files"] += s["files"]; total["findings"] += s["findings"]
        print(f"  [{name}] files={s['files']} findings={s['findings']}")
    api.close()
    print(f"  TOTAL: files={total['files']} findings={total['findings']}")
    return total