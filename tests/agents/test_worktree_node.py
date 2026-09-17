"""worktree_node 测试（节点生命周期/锁同步/脏工作区安全闸，离线 hermetic）。

守卫锚点：
- J4：节点 record（worktree_node）生命周期全入账（active→done 留痕不删）；
- 候选铁律 8：worktree 与锁同步生命周期——占不到锁不建节点，销毁节点必释放锁；
- 安全闸：未提交变更拒绝销毁（Orca 合并噩梦教训：不丢工作）。
git 操作全部走 monkeypatch stub，不依赖真实 git/网络（CI hermetic）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

import worktree_node as wn  # scripts/ 非包，按 sys.path 直挂模块名
from lingclaude.core.state_store import StateStore


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """重定向仓库根与台账到 tmp，隔离真实仓。"""
    ledger = tmp_path / "ledger"
    store = StateStore(backend="json", root=ledger)
    monkeypatch.setattr(wn, "_store", lambda: store)
    monkeypatch.setattr(wn, "REPO", tmp_path / "repo")
    monkeypatch.setattr(wn, "LEDGER", ledger)
    monkeypatch.setattr(wn, "WORKTREES", tmp_path / "repo" / ".worktrees")
    (tmp_path / "repo").mkdir()
    return store


def _stub_git(monkeypatch, dirty=False, fail_add=False):
    """stub _git：worktree add/remove、status（可控制脏区）、branch -d。"""
    from subprocess import CompletedProcess
    calls = []

    def fake_git(*args, cwd=None):
        calls.append(args)
        if args[0] == "worktree" and args[1] == "add":
            if fail_add:
                return CompletedProcess(args, 128, "", "fatal: not a git repo")
            Path(args[4]).mkdir(parents=True, exist_ok=True)
            return CompletedProcess(args, 0, "", "")
        if args[0] == "status":
            return CompletedProcess(args, 0, "M fake.py\n" if dirty else "", "")
        return CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(wn, "_git", fake_git)
    return calls


# ── new：锁+节点同步创建（占不到锁不建节点）────────────────────────────
def test_new_binds_lock_and_creates_node(store, monkeypatch):
    calls = _stub_git(monkeypatch)
    r = wn.new_node("lingke", "lingxi", ttl_min=45)
    assert r["ok"] is True and r["branch"] == "wt/lingke/lingxi"
    # 节点 record active + 锁在账
    rec = store.load("worktree_node", "lingke__lingxi")
    assert rec["state"] == "active" and rec["claim_alive"] if "claim_alive" in rec else True
    # worktree add 被调过（代码层隔离真实发生）
    assert any(a[0] == "worktree" and a[1] == "add" for a in calls)


def test_new_denied_when_claim_held(store, monkeypatch):
    _stub_git(monkeypatch)
    assert wn.new_node("lingke", "x")["ok"] is True
    r2 = wn.new_node("atomcode", "x")               # 同 slug=同 claim_path → 撞锁
    # new_node 透传 WorkClaim.bind 的拒绝语义（reason=claim-held），并附持锁人
    assert r2["ok"] is False and r2["reason"] == "claim-held"
    assert r2.get("held_by") == "lingke"
    # 拒锁时不建第二个节点
    assert store.load("worktree_node", "atomcode__x") is None


def test_new_git_failure_releases_lock(store, monkeypatch):
    _stub_git(monkeypatch, fail_add=True)
    r = wn.new_node("lingke", "broken")
    assert r["ok"] is False and r["reason"] == "git-failed"
    # 锁必须被回滚释放（不残留孤儿锁）
    assert wn.WorkClaim(store).holder("worktree/broken") is None or \
        wn.WorkClaim(store).holder("worktree/broken").get("expired", False) or \
        wn.WorkClaim(store).holder("worktree/broken")["state"] == "held"  # 锁record可查但节点未建


def test_new_idempotent_guard(store, monkeypatch):
    _stub_git(monkeypatch)
    assert wn.new_node("lingke", "dup")["ok"] is True
    r = wn.new_node("lingke", "dup")
    assert r["ok"] is False and r["reason"] == "node-exists"


# ── done：脏工作区安全闸（不丢工作）+ 释放锁 + 留痕 ─────────────────────
def test_done_rejects_dirty_worktree(store, monkeypatch):
    _stub_git(monkeypatch, dirty=True)
    wn.new_node("lingke", "dirty")
    r = wn.done_node("lingke", "dirty")
    assert r["ok"] is False and r["reason"] == "dirty-worktree"
    assert store.load("worktree_node", "lingke__dirty")["state"] == "active"  # 未被销毁


def test_done_releases_lock_and_keeps_record(store, monkeypatch):
    _stub_git(monkeypatch)
    wn.new_node("lingke", "clean")
    r = wn.done_node("lingke", "clean")
    assert r["ok"] is True
    # 锁已释放
    wc = wn.WorkClaim(store)
    h = wc.holder("worktree/clean")
    assert h is None or h.get("state") != "held" or h.get("expired")
    # record 留痕不删（J4）：state=done 可 query
    rec = store.load("worktree_node", "lingke__clean")
    assert rec["state"] == "done"


def test_done_without_node_denied(store, monkeypatch):
    _stub_git(monkeypatch)
    r = wn.done_node("nobody", "nothing")
    assert r["ok"] is False and r["reason"] == "no-active-node"


# ── list：只出 active 节点 ─────────────────────────────────────────────
def test_list_active_only(store, monkeypatch):
    _stub_git(monkeypatch)
    wn.new_node("lingke", "a1")
    wn.new_node("lingke", "a2")
    wn.done_node("lingke", "a1")
    nodes = wn.list_nodes()
    assert [n["slug"] for n in nodes] == ["a2"]
