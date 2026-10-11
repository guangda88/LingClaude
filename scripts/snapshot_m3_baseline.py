#!/usr/bin/env python3
"""M3 阶段 1 基线快照落账——102 件红名单入 StateStore（真实落账，事故后首执行）。

产出：
- data/arch_ledger/arch_m3_redlist_baseline/baseline-<head>.json（StateStore record 形态）
- 纪律：core/ 现场实扫（不接外部清单）；head/as_of 锚点；ERR-02 drift 说明随 metadata。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lingclaude.engine.m3_core_coupling import check  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "arch_ledger" / "arch_m3_redlist_baseline"


def head() -> str:
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True, cwd=ROOT).stdout.strip()


def main() -> int:
    result = check(ROOT / "lingclaude" / "core")
    record = {
        "type": "arch_m3_redlist_baseline",
        "key": f"baseline-{result['meta']['baseline_head']}",
        "as_of": result["meta"]["as_of"],
        "head": head(),
        "created": datetime.now(timezone.utc).isoformat(),
        "method": result["meta"]["method"],
        "drift_note": result["meta"]["drift_note"],
        "trunk": result["trunk"],
        "redlist": result["redlist"],
        "counts": result["counts"],
        "pending_judgment": ["__init__.py（纯 re-export 壳与否待裁；若含业务 import 则进迁出候选）"],
        "mislocated_correction": (
            "test_tool_pipeline.py：卷宗口径曾误记为 core/ 下测试文件（ERR-2026-0925-02），"
            "实测在 tests/（364 行，测 engine.tool_pipeline），位置正确不入红名单"
        ),
        "self_inclusion_note": (
            "红名单 103 = 预估 102 + 1：m3_core_coupling（本检查器自身）也在 core/ 也是 "
            "迁出候选——测量仪器不自豁；其归宿（gov/ 域或阶段 2 随守卫件套统一安置）随 "
            "M3 阶段 2 裁决定，不因自指而豁免"
        ),
        "phases": {"stage1": "告警级（本快照）", "stage2": "CI 红线（待建闸收口后裁决）",
                    "stage3": "import hook 运行时拒绝（待动刀期）"},
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{record['key']}.json"
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    json.load(open(out))  # 落盘后回读校验
    print(f"baseline saved: {out.relative_to(ROOT)} "
          f"(trunk={result['counts']['trunk']} redlist={result['counts']['redlist']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
