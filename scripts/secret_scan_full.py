#!/usr/bin/env python3
"""
全库 secret 扫描脚本 v2 — 流式输出，低内存。
结果写到 data/secret_scan_result_<timestamp>.json（已 gitignore）。
"""

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path("/home/ai/lingclaude")
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tox",
             ".mypy_cache", ".pytest_cache", ".ruff_cache",
             "data/spill", "data/cache", "data/tmp_*",
             ".lingclaude", ".lingfamily", ".crush", ".audit",
             ".audit-records", ".mental_health", ".repobench",
             "webui-server/target", "lingcode/target",
             "backups", "archive", "logs"}
SKIP_EXT = {".pyc", ".pyo", ".bin", ".png", ".jpg", ".jpeg", ".gif", ".ico",
            ".pdf", ".zip", ".tar", ".gz", ".tgz", ".so", ".o", ".a", ".db",
            ".sqlite", ".sqlite3", ".lock", ".pickle", ".pkl", ".parquet",
            ".npy", ".npz", ".pt", ".pth", ".onnx", ".engine", ".woff", ".woff2",
            ".ttf", ".eot", ".mp3", ".mp4", ".wav", ".flac", ".mov", ".avi",
            ".rlib", ".d", ".rmeta", ".bak", ".tmp", ".swp", ".swo"}
CHUNK_SIZE = 256 * 1024  # 256KB 读块
MAX_HITS_PER_FILE = 50   # 单个文件最多存多少条详细命中，超过只存计数
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB 跳过，超大文件 (session_history.json 22MB) 流式扫

# 匹配模式：(类型名, 正则, 脱敏保留前N位)
PATTERNS = [
    ("sk-openai",      re.compile(rb"sk-[A-Za-z0-9_-]{20,}"), 4),
    ("sk-ant",         re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}"), 8),
    ("sk-proj",        re.compile(rb"sk-proj-[A-Za-z0-9_-]{20,}"), 9),
    ("sk-or",          re.compile(rb"sk-or-[A-Za-z0-9_-]{20,}"), 7),
    ("sk-live",        re.compile(rb"sk-live-[A-Za-z0-9_-]{20,}"), 9),
    ("sk-test",        re.compile(rb"sk-test-[A-Za-z0-9_-]{20,}"), 9),
    ("ghp",            re.compile(rb"ghp_[A-Za-z0-9]{20,}"), 4),
    ("gho",            re.compile(rb"gho_[A-Za-z0-9]{20,}"), 4),
    ("ghr",            re.compile(rb"ghr_[A-Za-z0-9]{20,}"), 4),
    ("github_pat",     re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"), 11),
    ("AKIA",           re.compile(rb"AKIA[0-9A-Z]{16}"), 4),
    ("ASIA",           re.compile(rb"ASIA[0-9A-Z]{16}"), 4),
    ("AIza",           re.compile(rb"AIza[0-9A-Za-z_-]{35}"), 4),
    ("ya29",           re.compile(rb"ya29\.[0-9A-Za-z_-]{20,}"), 4),
    ("JWT",            re.compile(rb"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"), 6),
    ("glpat",          re.compile(rb"glpat-[A-Za-z0-9_-]{20,}"), 6),
    ("glrt",           re.compile(rb"glrt-[A-Za-z0-9_-]{20,}"), 5),
    ("gldt",           re.compile(rb"gldt-[A-Za-z0-9_-]{20,}"), 5),
    ("xoxb",           re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}"), 4),
    ("dapi",           re.compile(rb"dapi[a-f0-9]{32}"), 4),
    ("ddc",            re.compile(rb"ddc[a-f0-9]{32}"), 3),
    ("dpf",            re.compile(rb"dpf[a-f0-9]{32}"), 3),
    ("dsi",            re.compile(rb"dsi[a-f0-9]{32}"), 3),
    ("qkn",            re.compile(rb"qkn_[A-Za-z0-9]{20,}"), 4),
    ("qsk",            re.compile(rb"qsk-[A-Za-z0-9_-]{20,}"), 4),
    ("PRIVATE_KEY",    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"), 20),
    ("api_key_assign", re.compile(rb"(?i)api[_-]?key\s*[=:]\s*[\"']?[A-Za-z0-9_-]{16,}"), 7),
    ("token_assign",   re.compile(rb"(?i)\b(token|secret|password|passwd|pwd)\b\s*[=:]\s*[\"']?[A-Za-z0-9_./+-]{16,}"), 5),
    ("bearer_auth",    re.compile(rb"(?i)bearer\s+[A-Za-z0-9._-]{20,}"), 6),
]

FALSE_POSITIVE_BYTES = [
    b"example", b"demo", b"test", b"sample", b"placeholder",
    b"your_", b"xxx", b"***", b"___", b"---", b"...",
    b"YOUR_API_KEY", b"YOUR_TOKEN", b"YOUR_SECRET",
    b"sk-xxx", b"sk-***", b"sk-your",
]


def is_false_positive(match_bytes: bytes) -> bool:
    low = match_bytes.lower()
    for kw in FALSE_POSITIVE_BYTES:
        if kw in low:
            return True
    return False


def redact_bytes(match_bytes: bytes, keep_prefix: int) -> str:
    try:
        s = match_bytes.decode("utf-8", errors="replace")
    except:
        s = repr(match_bytes)
    if len(s) <= keep_prefix:
        return s + "***"
    return s[:keep_prefix] + "***"


def scan_file_bytes(file_path: Path) -> tuple[int, dict, list]:
    """用字节模式扫描文件（更快，更少内存）。
    返回 (总命中数, 类型计数, 详细命中列表(截断))。
    """
    type_counts: dict[str, int] = {}
    detail_hits = []
    total = 0
    detail_limit_reached = False

    try:
        size = file_path.stat().st_size
        if size == 0:
            return 0, {}, []
        if size > MAX_FILE_SIZE:
            return _scan_large_file(file_path, size)
    except OSError:
        return 0, {}, []

    try:
        with open(file_path, "rb") as f:
            # 用字节模式逐块扫描，处理跨行匹配
            leftover = b""
            offset = 0  # 当前块在文件中的起始偏移
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    # 处理最后的残片
                    if leftover:
                        for type_name, pattern, keep in PATTERNS:
                            for m in pattern.finditer(leftover):
                                mb = m.group(0)
                                if is_false_positive(mb):
                                    continue
                                total += 1
                                type_counts[type_name] = type_counts.get(type_name, 0) + 1
                                if not detail_limit_reached:
                                    detail_hits.append({
                                        "type": type_name,
                                        "offset": offset + m.start(),
                                        "redacted": redact_bytes(mb, keep),
                                    })
                                    if len(detail_hits) >= MAX_HITS_PER_FILE:
                                        detail_limit_reached = True
                    break

                buf = leftover + chunk
                # 保留最后 200 字节作为下一块的 leftover（处理跨行匹配）
                if len(buf) > 200:
                    scan_end = len(buf) - 200
                    leftover = buf[scan_end:]
                    scan_buf = buf[:scan_end]
                else:
                    scan_buf = buf
                    leftover = b""

                for type_name, pattern, keep in PATTERNS:
                    for m in pattern.finditer(scan_buf):
                        mb = m.group(0)
                        if is_false_positive(mb):
                            continue
                        total += 1
                        type_counts[type_name] = type_counts.get(type_name, 0) + 1
                        if not detail_limit_reached:
                            detail_hits.append({
                                "type": type_name,
                                "offset": offset + m.start(),
                                "redacted": redact_bytes(mb, keep),
                            })
                            if len(detail_hits) >= MAX_HITS_PER_FILE:
                                detail_limit_reached = True

                offset += len(scan_buf)
    except (OSError, MemoryError):
        pass

    return total, type_counts, detail_hits


def _scan_large_file(file_path: Path, size: int) -> tuple[int, dict, list]:
    """对超大文件做采样扫描：开头 2MB + 中间 2MB + 结尾 1MB。"""
    type_counts: dict[str, int] = {}
    detail_hits = []
    total = 0
    detail_limit_reached = False

    # 采样区间（字节偏移）
    regions = [
        (0, min(2 * 1024 * 1024, size)),                              # 头 2MB
    ]
    if size > 4 * 1024 * 1024:
        mid_start = size // 2 - 1024 * 1024
        regions.append((max(0, mid_start), min(size, mid_start + 2 * 1024 * 1024)))  # 中 2MB
    if size > 1024 * 1024:
        regions.append((max(0, size - 1024 * 1024), size))              # 尾 1MB

    try:
        with open(file_path, "rb") as f:
            for start, end in regions:
                f.seek(start)
                buf = f.read(end - start)
                for type_name, pattern, keep in PATTERNS:
                    for m in pattern.finditer(buf):
                        mb = m.group(0)
                        if is_false_positive(mb):
                            continue
                        total += 1
                        type_counts[type_name] = type_counts.get(type_name, 0) + 1
                        if not detail_limit_reached:
                            detail_hits.append({
                                "type": type_name,
                                "offset": start + m.start(),
                                "redacted": redact_bytes(mb, keep),
                            })
                            if len(detail_hits) >= MAX_HITS_PER_FILE:
                                detail_limit_reached = True
    except (OSError, MemoryError):
        pass

    return total, type_counts, detail_hits


def should_skip(rel_path: Path) -> bool:
    # 路径前缀跳过（支持 "webui-server/target" 这种多段前缀）
    skip_prefixes = {
        "webui-server/target",
        "lingcode/target",
    }
    rel_str = rel_path.as_posix()
    for prefix in skip_prefixes:
        if rel_str == prefix or rel_str.startswith(prefix + "/"):
            return True
    # 目录名跳过
    for part in rel_path.parts:
        if part in SKIP_DIRS:
            return True
    # 后缀跳过
    if rel_path.suffix.lower() in SKIP_EXT:
        return True
    return False


def main():
    start = time.time()
    files_scanned = 0
    files_with_hits = 0
    total_hits = 0
    type_counts_total: dict[str, int] = {}
    file_entries = []  # 只存文件级摘要，不存详细命中

    for root, dirs, files in os.walk(ROOT):
        root_path = Path(root)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]

        for fname in files:
            fpath = root_path / fname
            try:
                rel = fpath.relative_to(ROOT)
            except ValueError:
                continue

            if should_skip(rel):
                continue

            files_scanned += 1
            hit_count, file_types, _details = scan_file_bytes(fpath)

            if hit_count > 0:
                files_with_hits += 1
                total_hits += hit_count
                for t, c in file_types.items():
                    type_counts_total[t] = type_counts_total.get(t, 0) + c

                file_entries.append({
                    "path": str(rel),
                    "hit_count": hit_count,
                    "type_counts": file_types,
                    "size_bytes": fpath.stat().st_size if fpath.exists() else 0,
                })

            # 每 1000 个文件打印一次进度（到 stderr，不影响 stdout 的结果）
            if files_scanned % 1000 == 0:
                elapsed = time.time() - start
                print(f"[进度] 已扫 {files_scanned} 文件, {files_with_hits} 个有命中, 耗时 {elapsed:.1f}s",
                      file=sys.stderr, flush=True)

    elapsed = time.time() - start

    # 排序
    file_entries.sort(key=lambda x: x["hit_count"], reverse=True)
    type_counts_sorted = dict(sorted(type_counts_total.items(), key=lambda x: x[1], reverse=True))

    # 写结果
    out_dir = ROOT / "data"
    out_dir.mkdir(exist_ok=True)
    ts = int(time.time())
    out_path = out_dir / f"secret_scan_result_{ts}.json"

    result = {
        "summary": {
            "files_scanned": files_scanned,
            "files_with_hits": files_with_hits,
            "total_hits": total_hits,
            "elapsed_seconds": round(elapsed, 2),
            "type_counts": type_counts_sorted,
        },
        "files": file_entries,  # 只存文件级摘要，不存详细命中内容
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # stdout 只打印摘要
    print(f"扫描完成: {files_scanned} 文件, {files_with_hits} 个文件有命中, 共 {total_hits} 处")
    print(f"耗时: {elapsed:.2f}s")
    print(f"结果文件: {out_path}")
    print()
    print("类型统计:")
    for t, c in type_counts_sorted.items():
        print(f"  {t:25s} {c}")
    print()
    print("命中文件 TOP 20:")
    for fe in file_entries[:20]:
        print(f"  {fe['hit_count']:4d}  {fe['path']}")


if __name__ == "__main__":
    main()
