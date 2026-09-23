#!/usr/bin/env python3
"""N1 互账完备守卫 —— federation_pair 双 record 周期对账（铁律 5 配套，2026-09-23 第二批整改 P0 #4）。

背景（docs/audit/20260923_total_report.md §4.1 P0 #4）：N1 此前"在册未实现"——
federation_pair 2 条 record（lc-ac / ac-lc）是 git 手工提交，scripts/ 零 n1_*.py，
六态状态机零编码，drift 态永远无人触发。本脚本补上 detection loop：
  1. 对账：枚举 federation_pair 台账，逐对验证对偶 record 存在 + state 一致 +
     counterpart 指针互指（缺一/不一致即警，铁律 5 条文原话）；
  2. 绑 N5：对有 manifest 的 pair，用 scripts/contract_drift.py 同一指纹器
     （behavior_fingerprint）核契约面——指纹分歧 = 六态中 drift 的触发条件；
  3. 自动补建：A 侧有、B 侧缺时 --fix 可建对偶 record（谁先建语义：
     先挂载方建"我即你插片"，后挂载方建"你即我插片"）；
  4. 状态机 transition 走 StateStore 原语（create/transition/query）。

六态（docs/LINGYUAN_IRON_LAW.md:301-308）:
  proposing -> paired -> drift | broken -> restored | dissolved

用法:
  python3 scripts/n1_federation_audit.py            # 对账（CI/周期用），警即退出 1
  python3 scripts/n1_federation_audit.py --fix      # 对账 + 自动补建缺失对偶
  python3 scripts/n1_federation_audit.py --json     # 机器可读
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT  # 仓库根（manifest 解析用，恒定）；ROOT 允许被测试重定向到台账副本
sys.path.insert(0, str(ROOT))

from lingclaude.core.state_store import StateStore  # noqa: E402

T_PAIR = "federation_pair"
VALID_STATES = ("proposing", "paired", "drift", "broken", "restored", "dissolved")
TERMINAL = {"dissolved"}

# 复用 N5 指纹器（N1 绑 N5——总报告整改点名"共享指纹 hash 器"）
sys.path.insert(0, str(ROOT / "scripts"))
import contract_drift as _cd  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _store() -> StateStore:
    return StateStore(backend="json", root=ROOT / "data" / "arch_ledger")


def _opposite_key(key: str) -> str | None:
    """lc-ac <-> ac-lc：两侧 key 以 - 分隔，对偶即两段互换。"""
    parts = key.split("-")
    if len(parts) != 2:
        return None
    return f"{parts[1]}-{parts[0]}"


def transition(pair_key: str, new_state: str, note: str = "") -> dict:
    """六态 transition（StateStore 原语操作 record.state）。

    合法迁移按条文表格：proposing→paired→(drift|broken)→restored；
    任一态→dissolved（单向撤回即整对失效，两侧同转）；drift/broken→restored。
    """
    if new_state not in VALID_STATES:
        raise ValueError(f"非法 state: {new_state}（六态外）")
    s = _store()
    rec = s.load(T_PAIR, pair_key)
    if rec is None:
        raise ValueError(f"pair 不存在: {pair_key}")
    old = rec.get("state")
    allowed = {
        ("proposing", "paired"), ("paired", "drift"), ("paired", "broken"),
        ("paired", "dissolved"), ("drift", "restored"), ("drift", "dissolved"),
        ("broken", "restored"), ("broken", "dissolved"),
        ("restored", "drift"), ("restored", "broken"), ("restored", "dissolved"),
        ("restored", "paired"), ("proposing", "dissolved"),
    }
    if old != new_state and (old, new_state) not in allowed:
        raise ValueError(f"非法迁移: {old} -> {new_state}")
    rec["state"] = new_state
    hist = rec.setdefault("transitions", [])
    hist.append({"from": old, "to": new_state, "at": _now(), "note": note})
    s.save(T_PAIR, pair_key, rec)
    # dissolved 不允许留半边：两侧同转
    opp = _opposite_key(pair_key)
    if new_state == "dissolved" and opp:
        opp_rec = s.load(T_PAIR, opp)
        if opp_rec and opp_rec.get("state") != "dissolved":
            opp_rec["state"] = "dissolved"
            opp_rec.setdefault("transitions", []).append(
                {"from": opp_rec.get("state"), "to": "dissolved", "at": _now(),
                 "note": f"对偶撤回联动（{pair_key} dissolved，不留半边）"})
            s.save(T_PAIR, opp, opp_rec)
    return rec


def _check_contract_fingerprint(rec: dict, key: str) -> str | None:
    """绑 N5：pair 声明了 manifest 就用同一指纹器核契约面。返回告警文案或 None。"""
    manifest = rec.get("manifest")
    if not manifest:
        return None
    mp = REPO_ROOT / manifest  # manifest 属仓库布局，不随台账副本重定向
    if not mp.is_file():
        return f"{key}: manifest 指针悬空 {manifest}（broken 候选）"
    # manifest 属于 plugins/agents/<name>/ → target="agent:<name>"
    name = mp.parent.name
    try:
        fp, _ = _cd.behavior_fingerprint(f"agent:{name}")
    except SystemExit:
        return f"{key}: manifest 契约面抽取失败 {manifest}"
    ck = f"agent-{name}"
    old = _store().load(_cd.T_DRIFT, ck)
    if old is None:
        return (f"{key}: 对应 contract_drift 无记录（{ck} 未对账，"
                f"先跑 scripts/contract_drift.py record agent:{name}）")
    if old.get("fingerprint") != fp:
        return (f"{key}: 契约指纹漂移 {old.get('fingerprint')} -> {fp}"
                f"（N5 侦测 → 六态 drift 触发条件）")
    return None


def audit(fix: bool = False) -> dict:
    s = _store()
    keys = list(s.list_keys(T_PAIR))
    report: dict = {"ts": _now(), "pairs": [], "alerts": [], "fixed": []}
    pair_alerts: set[str] = set()  # 逐侧检查、成对告警去重（双侧都验，不只查单边）

    def _alert(msg: str) -> None:
        if msg not in pair_alerts:
            pair_alerts.add(msg)
            report["alerts"].append(msg)

    for k in keys:
        rec = s.load(T_PAIR, k)
        if rec is None:
            continue
        entry = {"key": k, "state": rec.get("state")}
        if rec.get("state") not in VALID_STATES:
            _alert(f"{k}: state={rec.get('state')} 六态外")
            entry["alert"] = "state 非法"
        opp = _opposite_key(k)
        if opp is None:
            _alert(f"{k}: key 非双侧形态（<a>-<b>），无法对账")
            entry["alert"] = "key 形态"
        else:
            entry["opposite"] = opp
            opp_rec = s.load(T_PAIR, opp)
            if opp_rec is None:
                if fix and rec.get("state") != "dissolved":
                    mirror = {
                        "type": T_PAIR, "key": opp, "state": "proposing",
                        "note": (f"N1 自动补建对偶（{k} 侧先建，本侧'你即我插片'"
                                 f"对偶 record，对账通过后转 paired）"),
                        "manifest": rec.get("manifest"),
                        "trust_level": rec.get("trust_level"),
                        "plug_level": rec.get("plug_level"),
                        "counterpart": f"data/arch_ledger/{T_PAIR}/{k}.json",
                        "created": _now(), "created_by": "n1_federation_audit",
                    }
                    s.save(T_PAIR, opp, mirror)
                    report["fixed"].append(f"补建对偶 {opp}（proposing）")
                    entry["opposite"] = "missing→fixed"
                else:
                    _alert(f"{k}: 对偶 record 缺失（{opp}，铁律 5'缺一即警'）")
                    entry["opposite"] = "missing"
            else:
                lo, hi = sorted([k, opp])
                if opp_rec.get("state") != rec.get("state") and \
                        rec.get("state") not in TERMINAL:
                    _alert(f"{lo}/{hi}: state 不一致（{rec.get('state')} vs "
                           f"{opp_rec.get('state')}）")
                    entry["alert"] = "state 不一致"
                ck1 = rec.get("counterpart", "")
                if ck1 and not ck1.rstrip("/").endswith(f"{opp}.json"):
                    _alert(f"{k}: counterpart 指针未互指（→{ck1}，应为 {opp}.json）")
        fp_alert = _check_contract_fingerprint(rec, k)
        if fp_alert:
            _alert(fp_alert)
            entry.setdefault("alert", "fingerprint")
        report["pairs"].append(entry)
    if report["fixed"]:
        report["alerts"].append("存在自动补建的对偶（proposing），需 B 侧对账后转 paired")

    # 告警即 transition：铁律 5 条文"drift | 进入 transition = 行为指纹 hash 分歧"。
    # 此前 N1 投诉正是"drift 永不触发"——只有 transition 的告警才把六态机跑活。
    s = _store()
    for p in report["pairs"]:
        st = p.get("state")
        if st in ("paired", "restored") and p.get("alert") in (
                "fingerprint", "state 不一致"):
            try:
                transition(p["key"], "drift", note=f"N1 周期对账自动降级: {p['alert']}")
                report.setdefault("transitions", []).append(
                    f"{p['key']}: {st} -> drift（{p['alert']}）")
            except ValueError as e:
                report["alerts"].append(f"{p['key']}: transition 失败 {e}")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="N1 互账完备守卫（federation_pair 周期对账）")
    ap.add_argument("--fix", action="store_true", help="自动补建缺失对偶 record")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    report = audit(fix=args.fix)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(f"== N1 互账对账 {report['ts']} ==")
        for p in report["pairs"]:
            print(f"  {p['key']}: state={p.get('state')} 对偶={p.get('opposite', '-')}"
                  + (f" ⚠{p.get('alert')}" if p.get('alert') else ""))
        for f in report["fixed"]:
            print(f"  [fix] {f}")
        for a in report["alerts"]:
            print(f"  [警] {a}")
        if not report["alerts"]:
            print("  全部对账一致 ✓")
    return 1 if report["alerts"] else 0


if __name__ == "__main__":
    sys.exit(main())
