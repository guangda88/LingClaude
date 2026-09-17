"""work_claim：多 Agent 并行修改的工作区锁（StateStore record 原语）。

来源：2026-09-17 多 Agent 协作并行修改冲突实例（lingxi 插片双方同时改导致
测试基线漂移），用户裁定硬化为代码。

铁律锚点：
- 铁律 3/J4：锁的每次操作（bind/renew/release）全 record 化，可 query 可回放；
- 候选铁律 8：持锁者失联（时限过期）锁自动失效——不存在永久死锁；
- J5 守卫同源：查锁跳过是被守卫消费的一等语义（skipped:claim-held），
  不是静默吞错。

用法（正确流程，三步）：
    1. 动工前：WorkClaim.bind(store, member="lingke", path="lingclaude/plugins/agents/agent_lingxi/", ttl_min=60)
    2. 守卫跑测试前：WorkClaim.holder(store, path=...) → 持锁则跳过该模块记 skipped:claim-held
    3. 完工后：WorkClaim.release(store, member="lingke", path=...)
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from lingclaude.core.state_store import StateStore

DEFAULT_TTL_MIN = 60          # 默认持锁时限：60 分钟
CLAIM_TYPE = "work_claim"     # record type


class WorkClaim:
    """基于 StateStore 的工作区独占锁（路径级，非文件级——目录整体认领）。"""

    def __init__(self, store: StateStore) -> None:
        self._store = store

    # ── 三动作：bind / renew / release ────────────────────────────────
    def bind(self, member: str, path: str, ttl_min: int = DEFAULT_TTL_MIN,
             note: str = "") -> dict:
        """认领独占锁。已有人持锁（未过期）则拒绝并返回持锁人信息。

        path 归一化：目录以 / 结尾，相对路径小写化（防 lingxi/LingXi 类大小写歧义）。
        """
        key = self._key(path)
        holder = self.holder(path)
        if holder and holder["member"] != member and not holder["expired"]:
            self._record(key, "bind_denied", member, path, note,
                         {"held_by": holder["member"],
                          "expires_at": holder["expires_at"]})
            return {"ok": False, "reason": "claim-held",
                    "held_by": holder["member"],
                    "expires_at": holder["expires_at"]}
        expires = time.time() + ttl_min * 60
        self._store.save(CLAIM_TYPE, key, {
            "member": member, "path": path, "state": "held",
            "bound_at": time.time(), "expires_at": expires,
            "note": note[:200],
        })
        self._record(key, "bound", member, path, note, {"ttl_min": ttl_min})
        return {"ok": True, "member": member, "path": path, "expires_at": expires}

    def renew(self, member: str, path: str, ttl_min: int = DEFAULT_TTL_MIN) -> dict:
        """续锁。只有持锁人自己能续；锁已过期/被夺则拒绝。"""
        key = self._key(path)
        rec = self._store.load(CLAIM_TYPE, key)
        if not rec or rec.get("member") != member:
            return {"ok": False, "reason": "not-holder"}
        if rec["expires_at"] < time.time():
            self._record(key, "renew_denied_expired", member, path, "", {})
            return {"ok": False, "reason": "expired"}
        expires = time.time() + ttl_min * 60
        rec.update({"expires_at": expires, "renewed_at": time.time()})
        self._store.save(CLAIM_TYPE, key, rec)
        self._record(key, "renewed", member, path, "", {"expires_at": expires})
        return {"ok": True, "expires_at": expires}

    def release(self, member: str, path: str) -> dict:
        """释放锁（持锁人本人；或锁已过期时任何人可清理）。"""
        key = self._key(path)
        rec = self._store.load(CLAIM_TYPE, key)
        if not rec:
            return {"ok": False, "reason": "no-claim"}
        expired = rec["expires_at"] < time.time()
        if rec["member"] != member and not expired:
            return {"ok": False, "reason": "not-holder",
                    "held_by": rec["member"]}
        rec.update({"state": "released", "released_at": time.time(),
                    "released_by": member})
        self._store.save(CLAIM_TYPE, key, rec)
        self._record(key, "released", member, path, "", {})
        return {"ok": True}

    # ── 查询：守卫消费的一等接口 ──────────────────────────────────────
    def holder(self, path: str) -> Optional[dict]:
        """查持锁人。过期锁返回 expired=True（候选铁律 8：失联自动失效）。"""
        key = self._key(path)
        rec = self._store.load(CLAIM_TYPE, key)
        if not rec or rec.get("state") != "held":
            return None
        rec = dict(rec)
        rec["expired"] = rec["expires_at"] < time.time()
        return rec

    def check_paths(self, paths: list[str]) -> dict:
        """守卫批量查锁：返回 {path: 持锁信息}，仅含**有人持有效锁**的路径。

        守卫用法：被锁住的模块记 skipped:claim-held，其余正常跑——
        不让半成品污染全量回归，也不静默吞掉被跳过的事实。
        """
        locked = {}
        for p in paths:
            h = self.holder(p)
            if h and not h["expired"]:
                locked[p] = {"member": h["member"], "expires_at": h["expires_at"]}
        return locked

    # ── 内部 ──────────────────────────────────────────────────────────
    @staticmethod
    def _key(path: str) -> str:
        """路径 → record key：统一相对化+规范化，避免绝对/相对双 key 漂移。

        注意顺序：先判机器前缀（此时还能看到开头的 /），再剥斜杠——
        反过来会因 strip('/') 吞掉前缀的首个 / 导致绝对路径永不该匹配
        （2026-09-17 test_path_key_normalization 实测教训）。
        """
        p = path.replace("\\", "/").strip()
        if p.startswith("/home/"):
            p = p.split("/", 4)[-1]  # 去掉机器前缀（/home/ai/<仓>/），保留仓内相对路径
        p = p.strip("/")
        return p.replace("/", "__")

    def _record(self, key: str, event: str, member: str, path: str,
                note: str, extra: dict) -> None:
        """J4：锁生命周期事件全入账（work_claim_log type，与锁本体分离）。"""
        log_id = f"{key}:{event}:{int(time.time())}"
        self._store.save("work_claim_log", log_id, {
            "claim_key": key, "event": event, "member": member,
            "path": path, "note": note[:100], **extra,
            "at": time.time(),
        })
