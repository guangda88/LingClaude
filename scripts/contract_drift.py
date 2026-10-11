#!/usr/bin/env python3
"""N5 契约漂移守卫 —— T3 自优化插片行为指纹 hash 与漂移入册（铁律 5 配套，2026-09-23 第二批整改 P0 #1）。

背景（docs/audit/20260923_total_report.md §4.1 P0 #1）：N5 契约漂移侦测此前真零落地
——全仓 contract_drift/behavior_fingerprint 零源码命中，drift 态永远无人触发；
且 lingclaude/cli/n5_token_guard.py + n5_stream_watchdog.py 与律层 N5 是不同物
（CLI 层空响应守卫 vs 律层契约指纹），存在命名冲突。本脚本：
  1. 新命名空间 scripts/contract_drift.py（避开 n5_* CLI 前缀）——总报告整改点名；
  2. 行为指纹 = 关键路径（manifest 声明的核心命令/工具面）→ 归一化摘要的 SHA-256；
  3. 指纹入册 StateStore（type=contract_drift），state: clean -> drift（六态对账
     由 scripts/n1_federation_audit.py 复用本 hash 器——N1 绑 N5，共享指纹器）；
  4. drift 即警：非零退出码，可被 CI / 周期对账直接消费。

用法:
  python3 scripts/contract_drift.py record <target>     # 首记/复测：算指纹并比对入册
  python3 scripts/contract_drift.py check  <target>     # 只读对账（CI 用），漂移退出 1
  python3 scripts/contract_drift.py show   [target]     # 展示当前指纹与历史

target 形态: "agent:agent_lingxi"（plugins/agents/ 下的插件目录名）。
指纹范围（v1）：manifest.agent.json 声明的 transport.command + stop_layer.kernel
+ tools 清单——即"契约面"（对外承诺的行为边界），实现文件正文 hash 后续按需扩。
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

T_DRIFT = "contract_drift"
PLUGINS = ROOT / "lingclaude" / "lingclaude" / "plugins" / "agents"
if not PLUGINS.is_dir():
    PLUGINS = ROOT / "lingclaude" / "plugins" / "agents"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _store() -> StateStore:
    return StateStore(backend="json", root=ROOT / "data" / "arch_ledger")


def _manifest_path(target: str) -> Path:
    name = target.split(":", 1)[1] if ":" in target else target
    return PLUGINS / name / "manifest.agent.json"


def _contract_surface(target: str) -> dict:
    """抽取契约面：transport.command / stop_layer.kernel / tools 名单（对齐 N5 条文
    '关键路径输入→输出摘要'——manifest 是插片对主干的契约声明，漂移即契约漂移）。"""
    mp = _manifest_path(target)
    if not mp.is_file():
        raise SystemExit(f"manifest 不存在: {mp}")
    m = json.loads(mp.read_text(encoding="utf-8"))
    stop = m.get("stop_layer", {})
    caps = m.get("capabilities") or []
    surface = {
        "name": m.get("name"),
        "version": m.get("version"),
        "trust_level": m.get("trust_level"),
        "plug_level": m.get("plug_level"),
        "capabilities": sorted(caps),
        "transport_kind": (m.get("transport") or {}).get("kind"),
        "transport_command": (m.get("transport") or {}).get("command"),
        "cwd": (m.get("transport") or {}).get("cwd"),
        "stop_layer_kernel": stop.get("kernel"),
        "stop_layer_seams": sorted(stop.get("seams") or []),
    }
    return surface


def behavior_fingerprint(target: str) -> tuple[str, dict]:
    """契约面归一化（json sort_keys）后 SHA-256。返回 (hash, 契约面快照)。"""
    surface = _contract_surface(target)
    canonical = json.dumps(surface, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16], surface


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return "?"


def record(target: str) -> int:
    fp, surface = behavior_fingerprint(target)
    s = _store()
    key = target.replace(":", "-")
    old = s.load(T_DRIFT, key)
    if old is None:
        s.save(T_DRIFT, key, {
            "type": T_DRIFT, "target": target, "state": "clean",
            "fingerprint": fp, "surface": surface,
            "recorded_at": _now(), "git_head": _git_head(),
        })
        print(f"[N5] 首记 clean: {target} fp={fp}")
        return 0
    old_fp = old.get("fingerprint")
    if old_fp == fp:
        old["state"] = "clean"
        old["verified_at"] = _now()
        old["git_head"] = _git_head()
        s.save(T_DRIFT, key, old)
        print(f"[N5] 对账一致 clean: {target} fp={fp}")
        return 0
    old["state"] = "drift"
    old["fingerprint_prev"] = old_fp
    old["fingerprint"] = fp
    old["surface"] = surface
    old["drifted_at"] = _now()
    old["drift_head"] = _git_head()
    s.save(T_DRIFT, key, old)
    print(f"[N5] ⚠ 契约漂移: {target} {old_fp} -> {fp}（旧指纹留存 fingerprint_prev）")
    return 1


def check(target: str) -> int:
    """只读对账：台账指纹 vs 现算指纹。无记录即警（未对账 ≠ 健康）。"""
    fp, _ = behavior_fingerprint(target)
    key = target.replace(":", "-")
    old = _store().load(T_DRIFT, key)
    if old is None:
        print(f"[N5] ✗ 未对账: {target} 无 contract_drift record（先 record）")
        return 1
    if old.get("state") == "drift":
        print(f"[N5] ✗ 漂移未复核: {target} 台账态=drift fp={fp}")
        return 1
    if old.get("fingerprint") != fp:
        print(f"[N5] ✗ 契约漂移: {target} 台账={old.get('fingerprint')} 现算={fp}")
        return 1
    print(f"[N5] ✓ clean: {target} fp={fp}")
    return 0


def show(target: str | None) -> int:
    s = _store()
    keys = [target.replace(":", "-")] if target else list(s.list_keys(T_DRIFT))
    if not keys:
        print(f"（{T_DRIFT} 台账为空）")
        return 0
    for k in keys:
        rec = s.load(T_DRIFT, k)
        if rec:
            print(json.dumps(rec, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("——")[0])
    ap.add_argument("cmd", choices=["record", "check", "show"])
    ap.add_argument("target", nargs="?", help='如 "agent:agent_lingxi"')
    args = ap.parse_args()
    if args.cmd in ("record", "check") and not args.target:
        ap.error(f"{args.cmd} 需要 target")
    if args.cmd == "record":
        return record(args.target)
    if args.cmd == "check":
        return check(args.target)
    return show(args.target)


if __name__ == "__main__":
    sys.exit(main())
