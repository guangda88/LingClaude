"""全局状态黑板（blackboard）——Goal/Worker/Constraint/Evidence 四类状态面。

问题（2026-10-27 Jev 架构对标实证）：lc 有证据协议面（evidence_protocol）
但无「任务中途的目标-约束-证据」统一状态面——各 gate 的判定依据散在局部
调用点，任务中途换执行者/换会话时上下文断裂，长任务没有跨步一致性视图。

本模块提供四类状态的内存黑板 + 磁盘快照（JSON）：

    bb = get_blackboard()
    bb.set_goal(Goal(text="交付 X", done_when="测试全绿"))
    bb.add_constraint(Constraint(text="不得改生产配置"))
    bb.add_evidence(Evidence(kind="test_passed", digest="...", detail="..."))
    bb.set_current_worker("writer")     # 执行权接力
    snap = bb.snapshot_dict()           # 快照注入 / 持久化

核心不变式：
- 线程安全（RLock 包全部读写，对齐 verify_ledger 锁纪律）；
- 磁盘快照 fail-open：快照故障不影响内存态（黑板是辅助面不是执行链）；
- Evidence 登记可挂 evidence_protocol 的 digest（声明可当场哈希复核）；
- 边界纪律：不做调度决策（那是各 gate 的事）、不解析内容——只存取。
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "Goal", "Worker", "Constraint", "Evidence",
    "Blackboard", "get_blackboard", "reset_blackboard",
]

_DEFAULT_SNAPSHOT = Path("data/arch_ledger/blackboard_snapshot.json")
_MAX_LIST = 64  # 每类状态条数上限（防长任务无界膨胀）


@dataclass
class Goal:
    """当前任务目标（单槽——同一时刻只有一个活跃目标，set 即替换）。"""

    text: str
    done_when: str = ""          # 完成判据（供 done/success 门控对照）
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "done_when": self.done_when,
                "updated_at": self.updated_at}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Goal":
        return cls(text=str(d.get("text", "")),
                   done_when=str(d.get("done_when", "")),
                   updated_at=float(d.get("updated_at", 0.0)))


@dataclass
class Worker:
    """执行权持有者（单槽——接力语义，对齐 next_worker 概念）。"""

    name: str
    handover_reason: str = ""    # 为何换人（决策流可引用）
    since: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "handover_reason": self.handover_reason,
                "since": self.since}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Worker":
        return cls(name=str(d.get("name", "")),
                   handover_reason=str(d.get("handover_reason", "")),
                   since=float(d.get("since", 0.0)))


@dataclass
class Constraint:
    """任务约束（多槽追加——硬约束/偏好约束都进这里，供各 gate 对照）。"""

    text: str
    scope: str = "task"          # task / session / policy
    added_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "scope": self.scope, "added_at": self.added_at}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Constraint":
        return cls(text=str(d.get("text", "")), scope=str(d.get("scope", "task")),
                   added_at=float(d.get("added_at", 0.0)))


@dataclass
class Evidence:
    """任务证据条目（多槽追加；digest 建议接 evidence_protocol 指纹）。"""

    kind: str                    # file_created / test_passed / commit_created / claim_verified / free
    detail: str
    digest: str = ""             # sha256 指纹（可空——free 类允许无指纹）
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail,
                "digest": self.digest, "ts": self.ts}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Evidence":
        return cls(kind=str(d.get("kind", "free")), detail=str(d.get("detail", "")),
                   digest=str(d.get("digest", "")), ts=float(d.get("ts", 0.0)))


class Blackboard:
    """四类状态黑板（线程安全 + fail-open 磁盘快照）。"""

    def __init__(self, snapshot_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._goal: Optional[Goal] = None
        self._worker: Optional[Worker] = None
        self._constraints: list[Constraint] = []
        self._evidence: list[Evidence] = []
        self._snapshot_path = snapshot_path or _DEFAULT_SNAPSHOT

    # ---------- 写（全部 fail-open 快照） ----------

    def set_goal(self, text: str, done_when: str = "") -> Goal:
        with self._lock:
            self._goal = Goal(text=text, done_when=done_when)
            self._save_snapshot()
            return self._goal

    def set_current_worker(self, name: str, handover_reason: str = "") -> Worker:
        with self._lock:
            self._worker = Worker(name=name, handover_reason=handover_reason)
            self._save_snapshot()
            return self._worker

    def add_constraint(self, text: str, scope: str = "task") -> Constraint:
        with self._lock:
            c = Constraint(text=text, scope=scope)
            self._constraints.append(c)
            self._constraints = self._constraints[-_MAX_LIST:]
            self._save_snapshot()
            return c

    def add_evidence(self, kind: str, detail: str, digest: str = "") -> Evidence:
        with self._lock:
            e = Evidence(kind=kind, detail=detail, digest=digest)
            self._evidence.append(e)
            self._evidence = self._evidence[-_MAX_LIST:]
            self._save_snapshot()
            return e

    # ---------- 读 ----------

    def snapshot_dict(self) -> dict[str, Any]:
        """一致快照（供注入上下文 / 决策流 meta / 测试断言）。"""
        with self._lock:
            return {
                "goal": self._goal.to_dict() if self._goal else None,
                "worker": self._worker.to_dict() if self._worker else None,
                "constraints": [c.to_dict() for c in self._constraints],
                "evidence": [e.to_dict() for e in self._evidence],
            }

    def render(self, max_chars: int = 1200) -> str:
        """渲染成紧凑文本块（供系统提示注入；超限截尾保最新证据）。"""
        with self._lock:
            lines: list[str] = []
            if self._goal:
                lines.append(f"Goal: {self._goal.text}"
                             + (f"（完成判据: {self._goal.done_when}）"
                                if self._goal.done_when else ""))
            if self._worker:
                lines.append(f"Worker: {self._worker.name}")
            for c in self._constraints[-8:]:
                lines.append(f"Constraint[{c.scope}]: {c.text}")
            for e in self._evidence[-8:]:
                d = f" digest={e.digest}" if e.digest else ""
                lines.append(f"Evidence[{e.kind}]: {e.detail}{d}")
            text = "\n".join(lines)
            return text[-max_chars:]

    # ---------- 持久化（fail-open） ----------

    def _save_snapshot(self) -> bool:
        """原子写快照。任何磁盘故障静默返回 False（不反噬内存态）。"""
        try:
            p = self._snapshot_path
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            payload = {
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "state": self.snapshot_dict(),
            }
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1),
                encoding="utf-8")
            tmp.replace(p)
            return True
        except Exception:  # noqa: BLE001 — 快照故障不阻断内存黑板
            return False

    def load_snapshot(self) -> bool:
        """从磁盘恢复（进程重启接续；损坏文件静默放弃返回 False）。"""
        try:
            p = self._snapshot_path
            if not p.exists():
                return False
            payload = json.loads(p.read_text(encoding="utf-8"))
            state = payload.get("state") or {}
            with self._lock:
                if isinstance(state.get("goal"), dict):
                    self._goal = Goal.from_dict(state["goal"])
                if isinstance(state.get("worker"), dict):
                    self._worker = Worker.from_dict(state["worker"])
                self._constraints = [
                    Constraint.from_dict(c) for c in state.get("constraints", [])
                    if isinstance(c, dict)][-_MAX_LIST:]
                self._evidence = [
                    Evidence.from_dict(e) for e in state.get("evidence", [])
                    if isinstance(e, dict)][-_MAX_LIST:]
            return True
        except Exception:  # noqa: BLE001 — 损坏快照不反噬进程
            return False


_DEFAULT_BB: Optional[Blackboard] = None
_DEFAULT_BB_LOCK = threading.Lock()


def get_blackboard(snapshot_path: Optional[Path] = None) -> Blackboard:
    """单例获取（snapshot_path 可注入；默认 data/arch_ledger/blackboard_snapshot.json）。"""
    global _DEFAULT_BB
    if snapshot_path is not None:
        bb = Blackboard(snapshot_path=snapshot_path)
        bb.load_snapshot()  # fail-open：损坏/缺失都静默
        return bb
    if _DEFAULT_BB is None:
        with _DEFAULT_BB_LOCK:
            if _DEFAULT_BB is None:
                bb = Blackboard()
                bb.load_snapshot()  # fail-open：损坏/缺失都静默
                _DEFAULT_BB = bb
    return _DEFAULT_BB


def reset_blackboard() -> None:
    """测试辅助：清空单例（仅内存，不动磁盘）。"""
    global _DEFAULT_BB
    with _DEFAULT_BB_LOCK:
        _DEFAULT_BB = None
