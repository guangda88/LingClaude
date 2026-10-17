"""artifact 守卫（P1-4/5，2026-10-17）：可证可退可停的目标件保护层。

职责（对应 FINAL_synthesis.md 阶段一-4/5）：
  1. last-known-good 快照：目标文件（behavior_policy.yaml 等）应用前
     原子快照（tmp 写入 + os.replace），记录 sha256 与元数据。
  2. 观察期自动恢复：应用后观察窗内出现回退/复发上升，restore()
     一键恢复快照（回滚本身入台账，见 daemon 接线）。
  3. 状态机标注（codex-P0-2 的单机映射）：单机单写者场景不新增
     verdict 值，复用 pending（=proposed+approved+observing 三态合一，
     写盘即时生效无审批闸）+ reason 前缀承载观察期结果：
       reason = "applied"             → 观察通过（accepted）
       reason = "applied:obs_regress" → 观察期回退 → 恢复快照后结算
       reason = "applied:baseline_gone_worse" → 继承反向配对拦截
  4. 继承反向配对【ac-P0-3】：best_ever_params 回滚/再应用前先在
     近 K 天语料上与当前参数配对重验——旧基线近期劣化则不回滚，
     只归档重置（防「赢在历史、输在现在」）。

不变量：
  - 快照永不覆盖（每 cycle 唯一名字，带 experiment_id）；恢复后快照
    保留（供事后取证），由外部清理策略管理。
  - 所有方法故障安全：快照/恢复失败不抛出，返回 (ok, msg) 由调用方
    决定 fail-closed（接受被拒）或降级（台账不可写时观测件不阻塞）。
  - 指纹化：快照记录 sha256（与 evaluator_meta 同一 hash 语义）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

#: 继承反向配对默认窗口（近 K 天语料）
RGP_WINDOW_DAYS = 7.0
#: 快照保留策略（A2-3）：每目标文件保留的最近未恢复快照份数
DEFAULT_KEEP_SNAPSHOTS = 10
#: 近期语料最少轨迹数——低于此值数据不足，配对判「不拦截」（证据不足
#: 不行动原则：没有近期证据证明旧基线劣化，就不阻止回滚）
RGP_MIN_TRACES = 3


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sha256_file(path: str | Path) -> str:
    """文件 sha256（16 位截断，与 evaluator_meta.fingerprint 同语义）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


@dataclass
class SnapshotMeta:
    """快照元数据（随 jsonl 追加落盘，行内自包含）。"""

    snapshot_id: str  # = experiment_id（与台账串联合）
    path: str
    sha256: str
    created_at: str
    restored_at: str | None = None
    restore_reason: str = ""


@dataclass
class PairVerdict:
    """继承反向配对结论。"""

    block: bool  # True = 旧基线近期劣化，禁止回滚
    reason: str
    old_score: float | None = None
    cur_score: float | None = None
    trace_count: int = 0


class ArtifactGuard:
    """目标文件快照/恢复 + 继承反向配对。全部方法故障安全。"""

    def __init__(
        self,
        snapshot_dir: str | Path | None = None,
        ledger_index: str | Path | None = None,
        replay_db_path: str | Path | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parent.parent.parent
        self.snapshot_dir = Path(
            snapshot_dir or (project_root / ".lingclaude" / "artifacts")
        )
        self.ledger_index = Path(
            ledger_index or (self.snapshot_dir / "snapshots.jsonl")
        )
        self.replay_db_path = Path(
            replay_db_path
            or (project_root / ".lingclaude" / "data_flywheel.db")
        )
        try:
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass  # 建目录失败留到 snapshot() 时再报（故障安全）

    # ------------------------------------------------------------------ #
    # P1-4: 快照 / 恢复
    # ------------------------------------------------------------------ #
    def snapshot(self, path: str | Path, snapshot_id: str) -> SnapshotMeta | None:
        """应用前原子快照目标文件。失败返回 None（调用方应拒绝应用）。"""
        src = Path(path).expanduser()
        try:
            if not src.exists():
                return None
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
            dst = self.snapshot_dir / f"{snapshot_id}.snap"
            # 原子写：tmp + os.replace（进程崩溃也不会留下半截快照）
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self.snapshot_dir), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
                    shutil.copyfileobj(inp, out)
                os.replace(tmp_name, dst)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
            meta = SnapshotMeta(
                snapshot_id=snapshot_id,
                path=str(src),
                sha256=sha256_file(dst),
                created_at=_now_iso(),
            )
            self._append_meta(meta)
            return meta
        except Exception:
            return None

    def restore(
        self, snapshot_id: str, reason: str = ""
    ) -> tuple[bool, str]:
        """恢复快照到原路径（原子替换）。回滚本身可入台账（调用方）。"""
        try:
            meta = self._find_meta(snapshot_id)
            if meta is None:
                return False, f"snapshot_not_found:{snapshot_id}"
            src = self.snapshot_dir / f"{snapshot_id}.snap"
            if not src.exists():
                return False, f"snapshot_file_missing:{snapshot_id}"
            dst = Path(meta.path).expanduser()
            if not dst.parent.exists():
                return False, f"target_dir_missing:{dst}"
            fd, tmp_name = tempfile.mkstemp(dir=str(dst.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
                    shutil.copyfileobj(inp, out)
                os.replace(tmp_name, dst)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
            meta.restored_at = _now_iso()
            meta.restore_reason = reason
            self._append_meta(meta)  # 恢复也追加记录（jsonl 审计链）
            # 校验恢复后指纹一致（可证）
            ok_hash = sha256_file(dst) == meta.sha256
            return ok_hash, (
                "restored_verified" if ok_hash else "restored_hash_mismatch"
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"restore_error:{exc}"

    def latest_good(self, path: str | Path) -> SnapshotMeta | None:
        """指定目标文件最近一份未恢复的快照（startup_watch 核验数据源）。

        jsonl 追加语义：同一 snapshot_id 可能有「快照行 + 恢复行」两条——
        按 id 去重取最后一行（其 restored_at 才是该快照的最终状态）。
        """
        try:
            target = str(Path(path).expanduser())
            latest_by_id: dict[str, SnapshotMeta] = {}
            for m in self._read_meta():
                if m.path == target:
                    latest_by_id[m.snapshot_id] = m
            good = [m for m in latest_by_id.values() if m.restored_at is None]
            return (
                max(good, key=lambda m: m.created_at) if good else None
            )
        except Exception:
            return None

    def prune_snapshots(
        self, keep: int = DEFAULT_KEEP_SNAPSHOTS
    ) -> int:
        """A2-3 保留策略（2026-10-17）：每目标文件只留最近 keep 份快照。

        快照「永不覆盖」（不变量）但允许清理：已恢复的快照即时删文件
        （last-known-good 语义已耗尽，恢复行仍留在 jsonl 供取证）；
        未恢复的按 created_at 保新删旧。只删本 guard 目录内的 .snap，
        越界路径不碰。返回删除的份数。
        """
        removed = 0
        try:
            latest_by_id: dict[str, SnapshotMeta] = {}
            for m in self._read_meta():
                latest_by_id[m.snapshot_id] = m  # 最后一行为最终状态
            by_path: dict[str, list[SnapshotMeta]] = {}
            for m in latest_by_id.values():
                if m.path:
                    by_path.setdefault(m.path, []).append(m)
            for metas in by_path.values():
                keep_rows: list[SnapshotMeta] = []
                for m in metas:
                    if m.restored_at is not None:
                        # 已恢复：last-known-good 语义耗尽，可删
                        if self._unlink_snap(m.snapshot_id):
                            removed += 1
                    else:
                        keep_rows.append(m)
                keep_rows.sort(key=lambda m: m.created_at)
                for m in keep_rows[: -keep] if keep > 0 else keep_rows:
                    if self._unlink_snap(m.snapshot_id):
                        removed += 1
        except Exception:
            pass  # 清理是最佳努力，失败不影响主流程
        return removed

    def _unlink_snap(self, snapshot_id: str) -> bool:
        try:
            f = self.snapshot_dir / f"{snapshot_id}.snap"
            if f.exists():
                f.unlink()
                return True
        except OSError:
            pass
        return False

    def list_snapshots(self, path: str | Path | None = None) -> list[dict[str, Any]]:
        """快照列表（A1 CLI 人工回滚的候选展示）。新→旧排列。

        返回字典行（含 snapshot_id/path/created_at/restored_at/restore_reason/
        snapshot_file_exists），path 为 None 时列全部目标文件。
        """
        try:
            rows = [m.__dict__ for m in self._read_meta()]
        except Exception:
            return []
        latest_by_id: dict[str, dict[str, Any]] = {}
        for r in rows:
            if path is not None and r.get("path") != str(Path(path).expanduser()):
                continue
            latest_by_id[r["snapshot_id"]] = r
        out = sorted(
            latest_by_id.values(),
            key=lambda r: r.get("created_at") or "",
            reverse=True,
        )
        for r in out:
            r["snapshot_file_exists"] = (
                (self.snapshot_dir / f"{r['snapshot_id']}.snap").exists()
            )
        return out

    # ------------------------------------------------------------------ #
    # P1-5: 继承反向配对
    # ------------------------------------------------------------------ #
    def reverse_pair_check(
        self,
        old_params: dict[str, Any],
        cur_params: dict[str, Any],
        window_days: float = RGP_WINDOW_DAYS,
        min_traces: int = RGP_MIN_TRACES,
    ) -> PairVerdict:
        """旧基线回滚/再应用前的近期配对重验。

        旧基线在近 K 天语料上劣于当前参数 → block（只归档重置，不回滚）。
        语料不足（< min_traces）→ 不拦截（证据不足不行动）。
        评分同 ReplayObjective（代价越低越好，minimize）。
        """
        try:
            from lingclaude.self_optimizer.replay_objective import (
                ReplayObjective,
                load_traces,
            )

            since = (
                datetime.now() - timedelta(days=window_days)
            ).isoformat(timespec="seconds")
            traces = load_traces(self.replay_db_path, since=since)
            if len(traces) < min_traces:
                return PairVerdict(
                    block=False,
                    reason=f"insufficient_recent_traces:{len(traces)}",
                )
            replay = ReplayObjective.__new__(ReplayObjective)
            replay.traces = traces
            replay.heal_loss = 0.8
            replay.nudge_cost = 0.6
            old_score = replay.evaluate(dict(old_params))
            cur_score = replay.evaluate(dict(cur_params))
            if old_score > cur_score:
                return PairVerdict(
                    block=True,
                    reason="old_baseline_worse_on_recent",
                    old_score=old_score,
                    cur_score=cur_score,
                    trace_count=len(traces),
                )
            return PairVerdict(
                block=False,
                reason="old_baseline_ok",
                old_score=old_score,
                cur_score=cur_score,
                trace_count=len(traces),
            )
        except Exception as exc:  # noqa: BLE001 — 配对故障不拦截回滚
            return PairVerdict(block=False, reason=f"pair_check_error:{exc}")

    # ------------------------------------------------------------------ #
    def _append_meta(self, meta: SnapshotMeta) -> None:
        try:
            self.ledger_index.parent.mkdir(parents=True, exist_ok=True)
            with open(self.ledger_index, "a", encoding="utf-8") as f:
                f.write(json.dumps(meta.__dict__, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 索引失败不阻断快照文件本身

    def _read_meta(self) -> list[SnapshotMeta]:
        out: list[SnapshotMeta] = []
        try:
            if not self.ledger_index.exists():
                return out
            with open(self.ledger_index, encoding="utf-8") as f:
                for line in f:
                    try:
                        out.append(
                            SnapshotMeta(**json.loads(line))
                        )
                    except Exception:
                        continue
        except Exception:
            pass
        return out

    def _find_meta(self, snapshot_id: str) -> SnapshotMeta | None:
        metas = [m for m in self._read_meta() if m.snapshot_id == snapshot_id]
        return metas[-1] if metas else None
