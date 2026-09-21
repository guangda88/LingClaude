# lingclaude/core/rollout.py
"""P0-1（2026-09-21，全 15 家精读 §3.2）：codex 式不可变会话事件日志。

对齐 openai/codex 的 rollout 模型（codex-rs/rollout/src/recorder.rs）：
- **append-only JSONL**：一行一事件（session_meta + 逐条 RolloutItem），只追加不覆写。
- **单调 ordinal**：每行携带 timestamp + ordinal，乱序/重放可按 ordinal 归位。
- **fork/revert 写新文件不删旧**：fork 从源 thread 的某 ordinal 截断复制历史，
  生成 `rollout-<ts>-<thread_id>_<fork_tag>.jsonl` 并记 `forked_from_id` +
  `forked_from_ordinal_exclusive`；revert 同构。原文件永不删改——「回退」
  变成「新开分叉」，恢复成本低且历史不可篡改（审计台账可 replay 任意点）。

与 checkpoint 的分工（不改既有崩溃恢复链路）：
- `checkpoint`（session_store.save_checkpoint）= 可覆盖快照，崩溃恢复用；
- `rollout`（本模块）= append-only 事件溯源，fork/revert/审计 replay 用。
- 循环体（model_call.py）**零改动**：本模块由持久化层调用（`_save_checkpoint`
  旁路录制一条 rollout 事件），不触碰循环体（§3.1 已证不可变会话不依赖循环纯化）。

停层声明（铁律 2 细则 5）：
- 内核 = RolloutRecorder（append-only 写 + 单调 ordinal 计数，进程内）
- 接缝 = record() / fork() / revert() 协议
- 实现 = 单实现（JSONL 文件），预留多 backend（SQLite 索引）扩展位
边界纪律：本模块永不删改既有行（append-only），fork/revert 只产新文件；
任何写失败 best-effort 记 warning 不抛（与 checkpoint 语义一致）。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROLLOUT_DIR = Path(".lingclaude/rollouts")


def _ts_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _safe_tag(tag: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", tag)


@dataclass
class RolloutEvent:
    """rollout 一行事件（JSONL 序列化单元）。"""
    ordinal: int
    ts: str
    event: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_json_line(self) -> str:
        payload = {"ts": self.ts, "ordinal": self.ordinal, "event": self.event}
        payload.update(self.data)
        return json.dumps(payload, ensure_ascii=False)


class RolloutRecorder:
    """append-only rollout 日志记录器（codex RolloutRecorder 的 Python 对位）。

    用法：
        rr = RolloutRecorder(session_id="abc")
        rr.open_meta(branch="master")               # 写 session_meta 行
        rr.record("message", {"role": "user", "content": "...", "round_idx": 0})
        rr.record("checkpoint", {"round_idx": 1})
        new_path = rr.fork("rewind")                # 从当前 ordinal 截断新开分叉
        rr.revert("bad_round")                       # 写 revert 事件到当前文件（不删旧）

    fork/revert 语义（codex 对齐）：
    - fork(tag)：新文件 `rollout-<ts>-<session>_<tag>.jsonl`，首行 meta 记
      forked_from_id（源 thread）+ forked_from_ordinal_exclusive（源 ordinal 截断点），
      复制源文件到截断点的全部行再切换写入目标；源文件不删改。
    - revert(tag)：当前文件追加一条 `revert` 事件（记 target_ordinal），
      语义是「从该 ordinal 起后续事件作废、新开分叉」——revert 实际等价于
      在 target_ordinal 处 fork 一个新文件，再标记新文件为 revert-of。
    """

    def __init__(
        self,
        session_id: str,
        rollout_dir: Path | None = None,
        thread_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.thread_id = thread_id or uuid.uuid4().hex[:16]
        self._dir = rollout_dir or ROLLOUT_DIR
        self._ordinal = 0
        self._lock = threading.Lock()
        # 当前写入目标（fork 后切换；源文件路径保留在 _all_paths 不删）
        self._active: Path | None = None
        self._all_paths: list[Path] = []
        # 文件句柄懒开（append 模式，O_APPEND 原子追加语义在本地盘成立）
        self._fh: Any = None

    # ── 生命周期 ──

    def open_meta(self, **meta: Any) -> Path:
        """开新文件写 session_meta 首行（ordinal=0）。幂等：已开则仅补 meta。"""
        with self._lock:
            if self._active is None:
                self._dir.mkdir(parents=True, exist_ok=True)
                self._active = self._dir / f"rollout-{_ts_slug()}-{self.thread_id}.jsonl"
                self._all_paths.append(self._active)
                self._fh = None
                self._write_meta({"thread_id": self.thread_id,
                                  "session_id": self.session_id, **meta})
            else:
                # 已开文件：meta 补记（fork 继承 meta 等场景）
                self._append_event("meta_update", dict(meta))
            return self._active

    def _write_meta(self, meta: dict[str, Any]) -> None:
        line = RolloutEvent(ordinal=0, ts=self._now(), event="session_meta", data=meta).to_json_line()
        self._active.write_text(line + "\n", encoding="utf-8")
        self._ordinal = 0

    # ── 追加 ──

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def record(self, event: str, data: dict[str, Any] | None = None) -> int:
        """追加一条事件，返回其 ordinal。best-effort：失败返回 -1 不抛。"""
        with self._lock:
            if self._active is None:
                self.open_meta()
            self._ordinal += 1
            try:
                self._ensure_fh()
                line = RolloutEvent(self._ordinal, self._now(), event, data or {}).to_json_line()
                self._fh.write(line + "\n")
                self._fh.flush()
                return self._ordinal
            except OSError as e:
                logger.warning("rollout record failed (best-effort): %s", e)
                return -1

    def _ensure_fh(self) -> None:
        if self._fh is None or self._active is None:
            self._fh = open(self._active, "a", encoding="utf-8")

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None

    # ── fork / revert（不可变：只产新文件，不删改旧） ──

    def fork(self, tag: str, *, from_ordinal: int | None = None) -> Path:
        """从（默认当前）ordinal 截断复制源历史，新开分叉文件。

        codex 对齐：新文件记 forked_from_id（源 thread）+
        forked_from_ordinal_exclusive（截断点），源文件保留不删。
        """
        with self._lock:
            src = self._active
            if src is None:
                raise RuntimeError("rollout 未 open_meta，无法 fork")
            cut = from_ordinal if from_ordinal is not None else self._ordinal
            self._dir.mkdir(parents=True, exist_ok=True)
            dst = self._dir / f"rollout-{_ts_slug()}-{self.thread_id}_{_safe_tag(tag)}.jsonl"
            # 复制源文件 meta（首行）+ 到 cut 为止的事件行
            src_lines = src.read_text(encoding="utf-8").splitlines()
            keep = [ln for ln in src_lines if self._line_ordinal(ln) <= cut]
            dst.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
            # 补一条 fork_meta 行（记溯源）
            fork_meta = RolloutEvent(ordinal=cut + 1, ts=self._now(), event="fork_meta", data={
                "forked_from_id": self.thread_id,
                "forked_from_ordinal_exclusive": cut,
                "tag": tag,
            }).to_json_line()
            with open(dst, "a", encoding="utf-8") as f:
                f.write(fork_meta + "\n")
            self._all_paths.append(dst)
            # 切换写入目标到新分叉
            self._active = dst
            self._ordinal = cut + 1
            self._fh = None
            logger.info("rollout fork: %s → %s (cut@%d)", src.name, dst.name, cut)
            return dst

    def revert(self, tag: str, target_ordinal: int | None = None) -> Path:
        """revert = 在 target_ordinal 处 fork 新文件 + 标记 revert-of。

        不删改旧文件（不可变）；语义是「target_ordinal 之后的事件作废，
        后续写入走新分叉」。target 缺省 = 当前 ordinal（整段 revert 到开头）。
        """
        new_path = self.fork(tag, from_ordinal=target_ordinal)
        # 在新文件追加 revert 溯源标记
        if target_ordinal is not None:
            rev = RolloutEvent(ordinal=self._ordinal + 1, ts=self._now(),
                               event="revert_meta", data={
                                   "revert_of": self.thread_id,
                                   "target_ordinal": target_ordinal,
                               }).to_json_line()
            with open(new_path, "a", encoding="utf-8") as f:
                f.write(rev + "\n")
        logger.info("rollout revert: %s (target@%s)", new_path.name, target_ordinal)
        return new_path

    @staticmethod
    def _line_ordinal(line: str) -> int:
        try:
            return int(json.loads(line).get("ordinal", -1))
        except (json.JSONDecodeError, ValueError, AttributeError):
            return -1

    # ── 读取 / 列表（审计 replay） ──

    @classmethod
    def read_all(cls, path: Path) -> list[dict[str, Any]]:
        """读一个 rollout 文件的全部事件（replay 用，ordinal 升序）。"""
        out: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        out.sort(key=lambda r: r.get("ordinal", 0))
        return out

    def list_files(self) -> list[Path]:
        if not self._dir.exists():
            return []
        prefix = f"rollout-*-{self.thread_id}"
        return sorted(self._dir.glob(f"{prefix}*.jsonl"))

    def history_at(self, ordinal: int) -> list[dict[str, Any]]:
        """重放到某 ordinal（含）为止的全部事件——不可变会话的「快照重建」原语。"""
        events = self.read_all(self._active) if self._active else []
        return [e for e in events if e.get("ordinal", 0) <= ordinal]
