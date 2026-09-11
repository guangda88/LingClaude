#!/usr/bin/env python3
"""pre-push 拦截闸：豁免提交的后台全量复核未消化 FAIL 时拒绝 push。

背景（claudecode P0 剩余缺口，a3eff23 裁决记录）：LING_AUDIT_FAST 豁免提交的
全量复核在 post-commit 后台异步进行；pre-push full-pytest 用当前工作区跑测试，
挡不住「历史豁免提交本身带 failing test 但已被推上远程」。本闸在 push 时
逐提交检查复核终态：

  PASS          → 放行
  FAIL 未 ACK   → 拒绝（列 log 路径；人工确认后 ack 留痕可放行）
  复核进行中    → 拒绝（等终态；watcher 被杀由 check-stale 兜底终态化）
  无豁免证据    → 放行（正常提交，pre-commit/pre-push 已把关）

子命令：
  （无）            gate 模式 — 读 pre-push stdin 的 ref 范围逐提交检查
  ack <sha> -r ...  人工消化一条 FAIL（.audit/push_block_ack.json 留痕）
  status            列出未消化的 FAIL / 进行中的复核

旁路（显式、可见）：LINGCLAUDE_SKIP_REVIEW_GATE=1 放行（与 LEFTHOOK=0 同级，
仅用于紧急热修，使用即留痕于 shell 历史）。

stdlib-only；git 只读（rev-list/rev-parse），本脚本永不写 git 状态。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

MARKER_DIR = Path("/tmp/lingclaude_exempt_review")
ACK_FILE = ".audit/push_block_ack.json"
RANGE_CAP = 500  # 单次 push 逐提交检查上限，防病态大 push 拖死钩子


def _root() -> Path:
    env = os.environ.get("LINGCLAUDE_GATE_ROOT")
    if env:
        return Path(env)
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    return Path(out.stdout.strip()) if out.returncode == 0 else Path.cwd()


def _git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], capture_output=True, text=True)
    return out.stdout.split() if out.returncode == 0 else []


def _zero(sha: str) -> bool:
    return set(sha) == {"0"}


def push_range(local_ref: str, local_sha: str,
               remote_ref: str, remote_sha: str) -> list[str]:
    """pre-push stdin 四元组 → 待推提交列表（旧→新）。"""
    if _zero(local_sha):
        return []  # 分支删除
    if _zero(remote_sha):
        shas = _git("rev-list", local_sha, "--not", "--remotes")
    else:
        shas = _git("rev-list", f"{remote_sha}..{local_sha}")
    return shas[-RANGE_CAP:]  # 取最新 N 个；截断情形 status 子命令可见


def evidence(root: Path, short: str) -> tuple[str, str]:
    """单提交复核证据 → (状态, 详情)。状态 ∈ pass|fail_unacked|fail_acked|inflight|none"""
    results = sorted((root / ".audit").glob(f"exempt_review_{short}_*.json"))
    if results:
        try:
            info = json.loads(results[-1].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "none", f"终态文件损坏: {results[-1].name}"
        status = info.get("status")
        if status == "PASS":
            return "pass", results[-1].name
        if status in ("FAIL", None):
            acked = _acked(root, short)
            detail = (f"{results[-1].name} log={info.get('log')} "
                      f"reason={info.get('reason')}")
            return ("fail_acked" if acked else "fail_unacked", detail)
    if MARKER_DIR.exists() and list(MARKER_DIR.glob(f"{short}_*.json")):
        return "inflight", f"marker: {MARKER_DIR}/{short}_*.json"
    return "none", ""


def _acked(root: Path, short: str) -> bool:
    try:
        acks = json.loads((root / ACK_FILE).read_text(encoding="utf-8"))
        return short in acks
    except (OSError, ValueError):
        return False


def decide(state: str, short: str, detail: str) -> tuple[bool, str]:
    if state == "pass":
        return True, ""
    if state == "fail_unacked":
        return False, (f"豁免提交 {short} 后台全量复核 FAIL 未消化\n"
                       f"    证据: {detail}\n"
                       f"    消化: python3 scripts/exempt_review_gate.py ack {short} -r <原因>\n"
                       f"    （或修好后手动补一次全量 pytest 复核）")
    if state == "inflight":
        return False, (f"豁免提交 {short} 复核进行中/失踪（等终态）\n"
                       f"    {detail}")
    return True, ""


def cmd_gate(stdin_lines: list[str]) -> int:
    if os.environ.get("LINGCLAUDE_SKIP_REVIEW_GATE") == "1":
        print("[review-gate] LINGCLAUDE_SKIP_REVIEW_GATE=1 显式旁路 — 留痕",
              file=sys.stderr)
        return 0
    root = _root()
    commits: list[str] = []
    for line in stdin_lines:
        parts = line.split()
        if len(parts) >= 4:
            commits.extend(push_range(*parts[:4]))
    if not commits:
        return 0  # 无范围（空 stdin/新分支无 remote/删除）— 其他闸兜底
    blocked = 0
    for full in commits:
        short = full[:7]
        state, detail = evidence(root, short)
        ok, msg = decide(state, short, detail)
        if not ok:
            blocked += 1
            print(f"[review-gate] BLOCKED\n  {msg}", file=sys.stderr)
    if blocked:
        print(f"[review-gate] {blocked} 个豁免提交复核未消化 — push 被拒",
              file=sys.stderr)
        return 1
    return 0


def cmd_ack(shas: list[str], reason: str) -> int:
    root = _root()
    path = root / ACK_FILE
    try:
        acks = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        acks = {}
    for sha in shas:
        full = _git("rev-parse", sha)
        short = (full[0] if full else sha)[:7]
        acks[short] = {"reason": reason, "acked_at": _now(),
                       "gate_version": "1.0"}
        print(f"[review-gate] acked {short}: {reason}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(acks, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return 0


def cmd_status() -> int:
    root = _root()
    results = sorted((root / ".audit").glob("exempt_review_*_*.json"))
    fails = []
    for r in results:
        try:
            info = json.loads(r.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if info.get("status") != "PASS":
            short = info.get("commit", "?")
            fails.append((short, _acked(root, short), info.get("reason"), r.name))
    if not fails:
        print("无未终态 PASS 的复核记录。")
        return 0
    for short, acked, reason, name in fails:
        flag = "已ACK" if acked else "未消化"
        print(f"{short}  {flag:5s}  {reason}  ({name})")
    return 1 if any(not a for _, a, _, _ in fails) else 0


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    ack = sub.add_parser("ack", help="人工消化 FAIL 复核")
    ack.add_argument("shas", nargs="+")
    ack.add_argument("-r", "--reason", required=True)
    sub.add_parser("status", help="列出未消化 FAIL")
    args = ap.parse_args()

    if args.cmd == "ack":
        return cmd_ack(args.shas, args.reason)
    if args.cmd == "status":
        return cmd_status()
    return cmd_gate(sys.stdin.read().splitlines())


if __name__ == "__main__":
    sys.exit(main())
