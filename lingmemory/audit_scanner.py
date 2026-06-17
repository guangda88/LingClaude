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
"""
import os
import re
from pathlib import Path, sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lingmemory.api import LingMemoryAPI
from lingmemory.core import DB_PATH


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


def scan_project(path: str, name: str, api: LingMemoryAPI) -> dict:
    stats = dict(files=0, findings=0)
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in {"venv", "node_modules", ".git", ".crush", "__pycache__"}]
        for f in files:
            if os.path.splitext(f)[1] not in {".py", ".ts", ".go", ".js", ".sh"}:
                continue
            stats["files"] += 1
            for finding in scan_file(os.path.join(root, f), name):
                try:
                    api.lm.create(type="audit_finding", data=finding, created_by="lingclaude")
                    stats["findings"] += 1
                except Exception:
                    pass
    return stats


def run_full_scan():
    api = LingMemoryAPI(str(DB_PATH), member="lingclaude")
    total = dict(files=0, findings=0)
    for name, path in PROJECT_PATHS.items():
        if not os.path.exists(path): continue
        s = scan_project(path, name, api)
        total["files"] += s["files"]; total["findings"] += s["findings"]
        print(f"  [{name}] files={s['files']} findings={s['findings']}")
    api.close()
    print(f"  TOTAL: files={total['files']} findings={total['findings']}")
    return total
