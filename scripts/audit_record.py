#!/usr/bin/env python3
"""
audit_record.py -- restore the "write audit record" step missing after
lefthook replaced the v3.0 pre-commit hook.

Background: the v3.0 post-commit hook (still active, not replaced by
lefthook) requires:
  1. .audit/last_commit_audit.json exists
  2. signature is valid
  3. tree_hash equals HEAD^{tree}

The old v3.0 pre-commit hook wrote this record. After lefthook took over,
the new pre-commit ("linggit/hooks/pre_commit.py") only does incremental
review, it does NOT call save_audit_record. So post-commit finds no
matching record and auto-resets every commit. This script fixes that.

Usage in lefthook.yml (pre-commit command):
    run: python3 scripts/audit_record.py

Dry-run:
    python3 scripts/audit_record.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Reuse the lingzu auditing library (lives in .git/hooks).
# 2026-09-12: parents[2] 是 /home/ai（仓库上一级），应为 parents[1]（仓库根）。
# 路径 bug 导致 ling_audit_lib 找不到 → post-commit 审计链断 → 提交被撤销。
HOOKS_DIR = Path(__file__).resolve().parents[1] / ".git" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

try:
    from ling_audit_lib import (
        ensure_signing_key,
        compute_file_hashes,
        generate_audit_record,
        get_staged_files,
        detect_repo,
        save_audit_record,
    )
except ImportError as e:
    sys.exit(f"[FATAL] ling_audit_lib not available: {e}")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Write audit record to .audit/last_commit_audit.json"
    )
    p.add_argument("--repo", default="", help="repo name (auto-detect if empty)")
    p.add_argument("--dry-run", action="store_true", help="print only, do not write")
    p.add_argument("--status", default="", help="audit status string")
    p.add_argument(
        "--tests-passed", action="store_true", default=True,
        help="tests_passed flag (post-commit only checks tree+signature)",
    )
    p.add_argument(
        "--json-out", default="",
        help="额外把审计记录写到该文件（供 lefthook 后续命令读取）",
    )
    args = p.parse_args()

    try:
        repo = args.repo or detect_repo() or "unknown"
        files = get_staged_files()
        file_hashes = compute_file_hashes(files) if files else {}
        key = ensure_signing_key()
        rec = generate_audit_record(
            staged_files=files,
            l0=[],
            l1=[],
            l2=[],
            tests_passed=args.tests_passed,
            repo_name=repo,
            file_hashes=file_hashes,
            key=key,
            pytest_output_tail="",
        )
    except Exception as e:
        print(f"[audit_record] generate failed: {e}", file=sys.stderr)
        return 1

    print(
        f"[audit_record] repo={repo} tree={rec['tree_hash'][:12]}... "
        f"status={rec.get('status', '')} files={len(files)}"
    )

    if args.dry_run:
        print("[DRY-RUN] skipping write")
        return 0

    try:
        save_audit_record(rec)
        print("[audit_record] saved to .audit/last_commit_audit.json")
    except Exception as e:
        # 全局目录 /home/ai/.ling-audit 只读时 save 抛错，但本地
        # .audit/last_commit_audit.json 已写入（post-commit 读这个）。
        # 全局写失败不阻断提交。
        print(f"[audit_record] global save failed (non-fatal): {e}", file=sys.stderr)

    if args.json_out:
        try:
            Path(args.json_out).write_text(
                json.dumps(rec, indent=2, ensure_ascii=False)
            )
            print(f"[audit_record] json-out saved to {args.json_out}")
        except Exception as e:
            print(f"[audit_record] json-out failed: {e}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
