#!/usr/bin/env python3
"""返审触发器（2026-09-17 用户裁定制度化）："算子革命先革自己的命"常态化。

触发条件（state 存储于 arch_audit_policy，上次审查指纹存于 arch_audit_state）：
  自身变化：仓库 HEAD 变化；铁律文档/守卫/台账脚本/词汇表/StateStore/SeamRegistry/
           ci.yml 任一 fingerprint 变化 → 审自身（守卫与铁律是否仍自洽、有无新违例）
  外界变化：data/arch_ledger 台账被外部增删（豁免/债务变动）→ 审外界
           （新豁免是否合法、新债务是否定价）
产出：审出的问题以 arch_audit_task record 入册（优化任务，供 SDT 巡检消费），
      无问题则只刷新指纹。审查本身不自动改代码（审计权限：发现→入册→等待）。

用法:
  python3 scripts/self_audit_trigger.py           # 检查触发，输出报告
  python3 scripts/self_audit_trigger.py --tasks   # 仅列未关闭的优化任务
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lingclaude.core.state_store import StateStore  # noqa: E402

T_POLICY = "arch_audit_policy"
T_STATE = "arch_audit_state"
T_TASK = "arch_audit_task"

# 审自身的关键文件（fingerprint 口径：sha256 前 12 位）
SELF_FILES = [
    "docs/LINGYUAN_IRON_LAW.md",
    "tests/test_iron_law_guards.py",
    "tests/fixtures/core_vocabulary.yaml",
    "scripts/arch_ledger.py",
    "scripts/seam_trend_inspect.py",
    "lingclaude/core/state_store.py",
    "lingclaude/core/seam.py",
    ".github/workflows/ci.yml",
]
# 审外界：台账 record 类型（外部经 arch_ledger.py 或手改文件都会变 mtime/hash）
# 2026-09-22 P0#1：纳入 arch_m6_snapshot——datalog_aggregator 写出新快照 =
# fingerprint 变化 → 触发返审（diagnosis §D 新 P0#1 改动面第 2 点）
LEDGER_TYPES = ["arch_debt", "arch_exemption", "arch_m6_snapshot"]


def _store() -> StateStore:
    return StateStore(backend="json", root=ROOT / "data" / "arch_ledger")


def _sha(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    except OSError:
        return None


def _fingerprints() -> dict[str, str]:
    out = {}
    for rel in SELF_FILES:
        h = _sha(ROOT / rel)
        if h:
            out[rel] = h
    for t in LEDGER_TYPES:
        d = ROOT / "data" / "arch_ledger" / t
        if d.is_dir():
            for f in sorted(d.rglob("*.json")):
                out[f"{t}:{f.stem}"] = _sha(f) or ""
    return out


def _head() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return "?"


def _policy() -> dict:
    s = _store()
    pol = s.load(T_POLICY, "default")
    if pol is None:
        pol = {
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mandate": "2026-09-17 用户裁定：自身有变化审自身，外界有变化审外界，审出不法问题列入优化任务",
            "self_triggers": SELF_FILES,
            "external_triggers": LEDGER_TYPES,
            "schedule": "SDT-lc-006（每次自驱会话唤醒时跑一次；改动铁律/守卫/台账后必跑）",
        }
        (s._json_backend._root / T_POLICY).mkdir(parents=True, exist_ok=True)
        s.save(T_POLICY, "default", pol)
    return pol


def _last_state() -> dict:
    s = _store()
    return s.load(T_STATE, "default") or {}


def _save_state(fp: dict) -> None:
    s = _store()
    (s._json_backend._root / T_STATE).mkdir(parents=True, exist_ok=True)
    s.save(T_STATE, "default", {
        "head": _head(), "fingerprints": fp,
        "checked": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


def open_tasks() -> list[tuple[str, dict]]:
    s = _store()
    out = []
    d = s._json_backend._root / T_TASK
    if d.is_dir():
        for f in sorted(d.rglob("*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if rec.get("state") == "open":
                out.append((f.relative_to(d).with_suffix("").as_posix(), rec))
    return out


def file_task(slug: str, severity: str, finding: str, source: str) -> None:
    s = _store()
    (s._json_backend._root / T_TASK).mkdir(parents=True, exist_ok=True)
    if s.load(T_TASK, slug):
        return  # 已在册不重复
    s.save(T_TASK, slug, {
        "severity": severity, "finding": finding, "source": source,
        "state": "open",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    print(f"  [任务入册] {slug} ({severity}): {finding}")


def run_audit() -> int:
    pol = _policy()
    last = _last_state()
    fp_now = _fingerprints()
    fp_prev = last.get("fingerprints", {})
    head_now, head_prev = _head(), last.get("head")

    self_changed = [k for k in fp_now if k in set(pol["self_triggers"]) and fp_prev.get(k) != fp_now[k]]
    external_changed = [k for k in fp_now if not k.startswith("docs/") and k not in set(pol["self_triggers"])
                        and fp_prev.get(k) != fp_now[k]]
    head_changed = head_prev not in (None, head_now)

    print("== 返审触发器 ==")
    print(f"HEAD: {head_prev} → {head_now}" + ("（自身代码变化）" if head_changed else "（未变）"))
    print(f"自身关键文件变化: {self_changed or '无'}")
    print(f"外界（台账）变化: {external_changed or '无'}")

    triggered = bool(self_changed or external_changed or head_changed)
    if not triggered and not last:
        print("首次运行：建立基线指纹，不触发审查。")
        _save_state(fp_now)
        return 0

    if not triggered:
        print("无触发条件 → 不审查（上次指纹已存档）。")
        return 0

    print("\n触发成立 → 执行返观返审（守卫自检 + 台账核账）：")
    # 审自身：守卫自检（守卫即查询的直接执行）
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_iron_law_guards.py", "-q"],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    guards_ok = r.returncode == 0
    print(f"  守卫自检: {'PASS' if guards_ok else 'FAIL'}")
    if not guards_ok:
        file_task("audit-guards-fail", "P0",
                  f"返审时守卫自检失败（触发源: {self_changed or head_now}），详见 pytest 输出",
                  "self_audit_trigger")

    # 审外界：债务到期核账
    from arch_ledger import expired_debts
    expired = expired_debts()
    if expired:
        file_task("audit-debts-expired", "P1", f"到期未清债务: {'; '.join(expired)}", "self_audit_trigger")

    # 台账完整性：豁免/债务 record 可解析且 state 合法
    s = _store()
    for t in (T_TASK, "arch_debt", "arch_exemption"):
        for key in s.list_keys(t):
            if s.load(t, key) is None:
                file_task(f"audit-ledger-corrupt-{t}-{key.replace('/', '_')}", "P1",
                          f"台账 record 损坏: {t}/{key}", "self_audit_trigger")

    _save_state(fp_now)
    tasks = open_tasks()
    print(f"\n优化任务在册: {len(tasks)} 条（--tasks 查看；审出→入册→等待，不自动改码）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", action="store_true")
    args = ap.parse_args()
    if args.tasks:
        for slug, rec in open_tasks():
            print(f"{rec['severity']}  {slug}: {rec['finding']}")
        return 0
    return run_audit()


if __name__ == "__main__":
    sys.exit(main())
