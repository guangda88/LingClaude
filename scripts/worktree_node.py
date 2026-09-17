#!/usr/bin/env python3
"""worktree 配对账节点：并行修改的代码层隔离 + record 层共享。

来源：用户裁定"Orca worktree 合并噩梦的灵族解法不是不用，而是每个 worktree
配对账节点——record 层本来就共享，代码层才需要隔离"（2026-09-17 硬化为代码）。

核心设计：
- 每个 worktree = 一个隔离的代码副本（git worktree，互不踩文件）；
- record 层（StateStore/data/arch_ledger）**不隔离**，全部 worktree 指向主仓
  同一台账——对账节点无歧义；
- work_claim 锁与 worktree 绑定：认领即建 worktree，释放即销毁——
  代码生命周期与锁生命周期同步，杜绝"孤儿 worktree 合并噩梦"。

用法：
    # 新建隔离节点并自动 bind 锁（默认 60 分钟 TTL）
    python scripts/worktree_node.py new lingke agent_lingxi --ttl 60

    # 完工销毁（自动 release 锁；有未提交变更时拒绝——不丢工作）
    python scripts/worktree_node.py done lingke agent_lingxi

    # 查看全部在役节点与对应锁
    python scripts/worktree_node.py list
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from lingclaude.core.state_store import StateStore
from lingclaude.plugins.agents.work_claim import WorkClaim

REPO = Path(__file__).parents[1]
LEDGER = REPO / "data" / "arch_ledger"          # record 层：始终主仓共享
NODE_TYPE = "worktree_node"
WORKTREES = REPO / ".worktrees"                  # 代码层：隔离副本


def _store() -> StateStore:
    return StateStore(backend="json", root=LEDGER)


def _git(*args: str, cwd: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def new_node(member: str, slug: str, ttl_min: int = 60) -> dict:
    """新建 worktree 节点 + bind 锁（代码隔离与认领一次完成）。"""
    store = _store()
    wc = WorkClaim(store)
    node_key = f"{member}__{slug}"
    branch = f"wt/{member}/{slug}"

    # 幂等检查：已有同 key 节点则拒绝重建
    if store.load(NODE_TYPE, node_key):
        return {"ok": False, "reason": "node-exists", "key": node_key}

    # 锁先占（占不到就别建 worktree——先协商再动工）
    claim = wc.bind(member, path=f"worktree/{slug}", ttl_min=ttl_min,
                    note=f"worktree node {node_key}")
    if not claim.get("ok"):
        return {"ok": False, "reason": "claim-denied", **claim}

    # 建 worktree（独立分支，代码层完全隔离）
    path = WORKTREES / node_key
    r = _git("worktree", "add", "-b", branch, str(path))
    if r.returncode != 0:
        wc.release(member, path=f"worktree/{slug}")
        return {"ok": False, "reason": "git-failed", "stderr": r.stderr[-300:]}

    store.save(NODE_TYPE, node_key, {
        "member": member, "slug": slug, "branch": branch,
        "path": str(path), "claim_path": f"worktree/{slug}",
        "state": "active", "created_at": time.time(),
        "expires_at": claim["expires_at"],
    })
    return {"ok": True, "key": node_key, "path": str(path), "branch": branch,
            "hint": f"cd {path} && 干活；完工跑 done {member} {slug}"}


def done_node(member: str, slug: str) -> dict:
    """销毁节点：查未提交变更 → release 锁 → remove worktree → record 留痕。"""
    store = _store()
    wc = WorkClaim(store)
    node_key = f"{member}__{slug}"
    rec = store.load(NODE_TYPE, node_key)
    if not rec or rec.get("state") != "active":
        return {"ok": False, "reason": "no-active-node"}

    path = Path(rec["path"])
    # 安全闸：未提交变更拒绝销毁（不丢工作——Orca 合并噩梦的教训）
    r = _git("status", "--porcelain", cwd=path)
    if r.returncode == 0 and r.stdout.strip():
        return {"ok": False, "reason": "dirty-worktree",
                "detail": "有未提交变更，先 commit 或 stash 再 done",
                "dirty": r.stdout.strip().splitlines()[:10]}

    wc.release(member, path=rec["claim_path"])
    _git("worktree", "remove", str(path))
    _git("branch", "-d", rec["branch"])  # 已合并才能删，未合并留分支（-d 安全删除）
    rec.update({"state": "done", "done_at": time.time()})
    store.save(NODE_TYPE, node_key, rec)
    return {"ok": True, "key": node_key, "branch": rec["branch"],
            "hint": f"分支 {rec['branch']} 已保留，合并走正常 PR 流程"}


def list_nodes() -> list[dict]:
    """全部节点与锁状态（record 层共享，任何 worktree/主仓查询结果一致）。"""
    store = _store()
    wc = WorkClaim(store)
    out = []
    for key in store.list_keys(NODE_TYPE) or []:
        rec = store.load(NODE_TYPE, key)
        if rec and rec.get("state") == "active":
            claim = wc.holder(rec["claim_path"])
            rec["claim_alive"] = bool(claim and not claim.get("expired"))
            out.append(rec)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="worktree 配对账节点")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_new = sub.add_parser("new")
    p_new.add_argument("member"), p_new.add_argument("slug")
    p_new.add_argument("--ttl", type=int, default=60)
    p_done = sub.add_parser("done")
    p_done.add_argument("member"), p_done.add_argument("slug")
    sub.add_parser("list")
    args = ap.parse_args()

    if args.cmd == "new":
        out = new_node(args.member, args.slug, args.ttl)
    elif args.cmd == "done":
        out = done_node(args.member, args.slug)
    else:
        out = list_nodes()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
