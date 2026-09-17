"""work_claim 测试（锁互斥/超时失效/查锁跳过语义，离线 hermetic）。

守卫锚点：
- J4：锁生命周期事件全入账（work_claim_log，bind_denied/released 可 query）；
- 候选铁律 8：持锁者失联（TTL 过期）锁自动失效——不存在永久死锁；
- J5：check_paths 是守卫消费的一等接口（有效锁返回、过期锁不挡）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from lingclaude.core.state_store import StateStore
from lingclaude.plugins.agents.work_claim import WorkClaim


@pytest.fixture()
def wc(tmp_path):
    return WorkClaim(StateStore(backend="json", root=tmp_path / "ledger"))


# ── 锁互斥：第二成员 bind 被拒，且拒载入账 ──────────────────────────────
def test_bind_mutex(wc):
    r1 = wc.bind("lingke", "lingclaude/plugins/agents/agent_lingxi/", ttl_min=30)
    assert r1["ok"] is True
    r2 = wc.bind("atomcode", "lingclaude/plugins/agents/agent_lingxi/")
    assert r2["ok"] is False and r2["reason"] == "claim-held"
    assert r2["held_by"] == "lingke"
    # bind_denied 也留账（J4：冲突可回放）
    logs = wc._store.list_keys("work_claim_log")
    assert any("bind_denied" in k for k in logs)


# ── 同成员重复 bind：幂等续占（不自锁）──────────────────────────────────
def test_bind_same_member_idempotent(wc):
    assert wc.bind("lingke", "path/a/")["ok"] is True
    assert wc.bind("lingke", "path/a/")["ok"] is True  # 自己再 bind 不拒绝


# ── 超时自动失效（候选铁律 8：失联无死锁）───────────────────────────────
def test_expired_claim_auto_invalid(wc, tmp_path):
    wc.bind("lingke", "path/b/", ttl_min=60)
    # 手动把锁改成已过期（模拟持锁者失联）
    key = wc._key("path/b/")
    rec = wc._store.load("work_claim", key)
    rec["expires_at"] = 0
    wc._store.save("work_claim", key, rec)

    h = wc.holder("path/b/")
    assert h["expired"] is True                    # 查锁报告过期
    # 他人可 bind 过期锁（夺锁成功）
    r = wc.bind("atomcode", "path/b/")
    assert r["ok"] is True and r["member"] == "atomcode"


# ── 过期锁：任何人可 release 清理 ──────────────────────────────────────
def test_release_expired_by_third_party(wc):
    wc.bind("lingke", "path/c/")
    key = wc._key("path/c/")
    rec = wc._store.load("work_claim", key)
    rec["expires_at"] = 0
    wc._store.save("work_claim", key, rec)
    r = wc.release("atomcode", "path/c/")          # 非持锁人清过期锁
    assert r["ok"] is True


# ── 非持锁人不能释放有效锁 ─────────────────────────────────────────────
def test_release_by_other_denied(wc):
    wc.bind("lingke", "path/d/")
    r = wc.release("atomcode", "path/d/")
    assert r["ok"] is False and r["reason"] == "not-holder"


# ── renew：持锁人可续，他人/过期不可 ───────────────────────────────────
def test_renew_rules(wc):
    wc.bind("lingke", "path/e/", ttl_min=60)
    assert wc.renew("lingke", "path/e/")["ok"] is True
    assert wc.renew("atomcode", "path/e/")["ok"] is False


# ── check_paths：守卫一等接口（有效锁挡、过期锁不挡、无锁不出现）─────────
def test_check_paths_for_guard(wc):
    wc.bind("lingke", "mods/a/")
    wc.bind("lingke", "mods/stale/")
    key = wc._key("mods/stale/")
    rec = wc._store.load("work_claim", key)
    rec["expires_at"] = 0
    wc._store.save("work_claim", key, rec)

    locked = wc.check_paths(["mods/a/", "mods/stale/", "mods/free/"])
    assert set(locked.keys()) == {"mods/a/"}       # 只有有效锁出现
    assert locked["mods/a/"]["member"] == "lingke"


# ── 路径 key 归一化：绝对/相对/尾斜杠同一把锁 ───────────────────────────
def test_path_key_normalization(wc):
    assert (wc.bind("lingke", "lingclaude/plugins/x/")["ok"]) is True
    # 绝对路径与相对路径、带尾斜杠与不带，都命中同一把锁
    r = wc.bind("atomcode", "/home/ai/lingclaude/lingclaude/plugins/x/")
    assert r["ok"] is False and r["reason"] == "claim-held"
