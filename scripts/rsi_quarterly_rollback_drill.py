#!/usr/bin/env python3
"""RSI 季度回滚演练（件12收尾）——验证「回滚到上一验证态」能力真实可用。

三幕全链（全部在临时沙箱目录内，零碰生产文件/状态）：
  幕一 ArtifactGuard 基础回路：快照→篡改→恢复→指纹校验→负例→latest_good
  幕二 daemon.manual_rollback 集成：A1 人工应急回滚核心（含台账诚实结算）
  幕三 prune 保留策略：恢复即删 + 未恢复保最近 N 份（审计行永留）

用法：
  python3 scripts/rsi_quarterly_rollback_drill.py            # 演练（沙箱自清理）
  python3 scripts/rsi_quarterly_rollback_drill.py --keep     # 保留沙箱现场供取证
退出码：0=全部通过，1=有失败项（季度演练失败应立即排查，禁止静默）。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lingclaude.self_optimizer.artifact_guard import ArtifactGuard  # noqa: E402
from lingclaude.self_optimizer.daemon import OptimizationDaemon  # noqa: E402

POLICY_A = "# drill-policy-a\nconsecutive_fail_limit: 5\nedit_tools: [Edit, Write]\n"
POLICY_A_TAMPERED = "# TAMPERED-A\neverything: gone\n"
POLICY_B = "# drill-policy-b\nkeep_me: true\n"


class Drill:
    def __init__(self) -> None:
        self.sandbox = Path(tempfile.mkdtemp(prefix="rsi_drill_"))
        self.guard = ArtifactGuard(snapshot_dir=self.sandbox / "artifacts")
        self.results: list[tuple[str, bool, str]] = []
        self.policy_a = self.sandbox / "policy_a.yaml"
        self.policy_b = self.sandbox / "policy_b.yaml"
        self.policy_a.write_text(POLICY_A, encoding="utf-8")
        self.policy_b.write_text(POLICY_B, encoding="utf-8")

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append((name, bool(ok), detail))

    # ---------------- 幕一：guard 基础回路 ----------------
    def act1(self) -> None:
        meta = self.guard.snapshot(self.policy_a, f"drill_a_{_stamp()}")
        self.check("幕一·快照成功且sha256落账", meta is not None and bool(meta.sha256))
        if meta is None:
            return
        self.policy_a.write_text(POLICY_A_TAMPERED, encoding="utf-8")

        ok, reason = self.guard.restore(meta.snapshot_id, reason="drill_act1")
        content_ok = self.policy_a.read_text(encoding="utf-8") == POLICY_A
        self.check(
            "幕一·恢复成功+指纹校验+内容逐字节一致",
            ok and reason == "restored_verified" and content_ok,
            f"reason={reason}",
        )

        ok2, reason2 = self.guard.restore("no_such_snapshot", reason="drill_neg")
        self.check(
            "幕一·负例：不存在id拒绝并给明确错误",
            not ok2 and reason2.startswith("snapshot_not_found"),
            f"reason={reason2}",
        )
        self.check(
            "幕一·latest_good：已恢复快照不再作为可回滚点",
            self.guard.latest_good(self.policy_a) is None,
        )
        listed = self.guard.list_snapshots(path=self.policy_a)
        restored_row = [r for r in listed if r["snapshot_id"] == meta.snapshot_id]
        self.check(
            "幕一·list_snapshots审计行含恢复记录",
            len(restored_row) == 1 and restored_row[0]["restored_at"] is not None,
        )

    # ---------------- 幕二：daemon 集成 ----------------
    def act2(self) -> None:
        daemon = OptimizationDaemon(state_dir=self.sandbox / "state")
        meta = daemon.artifacts.snapshot(self.policy_b, f"drill_b_{_stamp()}")
        self.check("幕二·经daemon.artifacts快照成功", meta is not None)
        if meta is None:
            return
        self.policy_b.write_text("# TAMPERED-B\nbroken: true\n", encoding="utf-8")

        # 造真实 pending 实验单并挂观察单（主路径：state.observing.exp_id）
        exp_id = daemon.experiments.start(1, {"drill": True}, 10.0)
        daemon.state.observing = {
            "exp_id": exp_id,
            "snapshot_id": meta.snapshot_id,
            "applied_at": _stamp(),
        }
        result = daemon.manual_rollback(meta.snapshot_id)
        content_ok = self.policy_b.read_text(encoding="utf-8") == POLICY_B
        self.check(
            "幕二·manual_rollback恢复成功+内容一致",
            getattr(result, "success", False) and content_ok,
            f"detail={getattr(result, 'detail', getattr(result, 'message', ''))}",
        )
        row = daemon.experiments.get(exp_id) if exp_id else None
        self.check(
            "幕二·关联观察单诚实结算为rolled_back",
            bool(row) and row["verdict"] == "rolled_back",
            f"verdict={row['verdict'] if row else 'None'}",
        )
        self.check(
            "幕二·观察单已清空（不残留悬置挂单）",
            daemon.state.observing is None,
        )

    # ---------------- 幕三：prune 保留策略 ----------------
    def act3(self) -> None:
        # policy_b 的可回滚快照已无（幕二已恢复即删）→ 连拍12份新快照
        ids = []
        for i in range(12):
            m = self.guard.snapshot(self.policy_b, f"drill_p_{_stamp()}_{i}")
            if m is not None:
                ids.append(m.snapshot_id)
        self.check("幕三·连拍12份快照", len(ids) == 12, f"got={len(ids)}")
        pruned = self.guard.prune_snapshots(keep=10)
        on_disk = list(self.guard.snapshot_dir.glob("drill_p_*.snap"))
        self.check(
            "幕三·prune保最近10份", len(on_disk) == 10, f"pruned={pruned}"
        )
        rows = len(self.guard._read_meta())  # 审计链只增不减
        self.check("幕三·jsonl审计行未被修剪", rows >= 14)  # 幕一2行+幕三12行

    def run(self) -> int:
        self.act1()
        self.act2()
        self.act3()
        failed = [r for r in self.results if not r[1]]
        print(f"\n===== RSI 季度回滚演练报告（{_stamp()}）=====")
        print(f"沙箱: {self.sandbox}")
        for name, ok, detail in self.results:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" ({detail})" if detail else ""))
        print(f"结果: {len(self.results) - len(failed)}/{len(self.results)} 通过")
        return 1 if failed else 0


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="保留沙箱现场供取证")
    args = parser.parse_args()
    drill = Drill()
    try:
        return drill.run()
    finally:
        if args.keep:
            print(f"沙箱已保留（取证）: {drill.sandbox}")
        else:
            shutil.rmtree(drill.sandbox, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
