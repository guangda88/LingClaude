#!/usr/bin/env python3
"""规则衰减复核 — Meadows L7（增强回路增益衰减）的机械化。

背景（docs/SYSTEMS_THEORY_SYNTHESIS.md R3）：规则库"只增不减"是无平衡的
增强回路——摩擦单调上涨（灵研原话："从未删过一条"）。本工具给规则建登记册
（含 last_verified 时间戳），把「X 天未经验证」的规则拉出复核队列，让"删规则"
成为一个有数据支撑的常规动作而不是 never。

诚实边界：本工具只做**登记 + 报告**；「规则触发即自动刷新 last_verified」的
接线留待后续（需要 hook 进 linggit 与守卫执行点）。首次 init 后所有规则
last_verified=null → 全部进复核队列，这是真实状态，不是 bug。

用法:
    python scripts/rule_decay_review.py init             # 建册/合并（保留已验证时间戳）
    python scripts/rule_decay_review.py review           # 复核队列（默认 30 天）
    python scripts/rule_decay_review.py review --days 7 --strict   # 有过期规则则 exit 1
    python scripts/rule_decay_review.py mark --id guard:H17 --days-ago 0   # 手工记录一次验证
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / ".lingclaude" / "rule_registry.json"

# 规则来源（id 前缀 → 扫描方式）
SOURCES = {
    "linggit": REPO_ROOT / "linggit" / "rules" / "review_rules.yaml",
    "guards": REPO_ROOT / ".lingclaude" / "metacognitive_guards.md",
    "coding_rules": REPO_ROOT / ".lingclaude" / "coding_rules.md",
    "security_rules": REPO_ROOT / ".lingclaude" / "security_rules.md",
    "task_protection_rules": REPO_ROOT / ".lingclaude" / "task_protection_rules.md",
    "self_driven_rules": REPO_ROOT / ".lingclaude" / "self_driven_rules.md",
    "l3_rules": REPO_ROOT / ".lingclaude" / "L3_rules.md",
}

_GUARD_ROW_RE = re.compile(r"^\|\s*(H\d+[a-z]?)\s*\|\s*([^|]+?)\s*\|", re.MULTILINE)
_YAML_DESC_RE = re.compile(r"^\s*-\s*type:.*?^\s*description:\s*[\"']?(.+?)[\"']?\s*$",
                           re.MULTILINE | re.DOTALL)


@dataclass
class RuleEntry:
    id: str
    file: str
    description: str
    last_verified: str | None = None  # ISO 日期；null = 从未记录到验证
    last_verified_evidence: str = ""  # 验证证据来源（H17：验证必须可溯源）
    registered_at: str = ""


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _rel(path: Path) -> str:
    """优先仓库相对路径；不在仓库内（如测试沙箱）时回退绝对路径。"""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def scan_sources() -> list[RuleEntry]:
    """扫描全部规则源 → 登记条目（不含 last_verified，由合并逻辑保留）。"""
    rules: list[RuleEntry] = []
    today = _today()

    # 1. linggit review_rules.yaml — 按 description 逐条
    linggit = SOURCES["linggit"]
    if linggit.exists():
        text = linggit.read_text(encoding="utf-8")
        descs = re.findall(r"description:\s*[\"']?(.+?)[\"']?\s*$", text, re.MULTILINE)
        for i, d in enumerate(descs, 1):
            rules.append(RuleEntry(
                id=f"linggit:{i:02d}", file=_rel(linggit),
                description=d.strip()[:80], registered_at=today,
            ))

    # 2. 元认知守卫表 — | HN | 名称 | 每行一条
    guards = SOURCES["guards"]
    if guards.exists():
        text = guards.read_text(encoding="utf-8")
        for m in _GUARD_ROW_RE.finditer(text):
            rules.append(RuleEntry(
                id=f"guard:{m.group(1)}", file=_rel(guards),
                description=f"{m.group(1)} {m.group(2)}", registered_at=today,
            ))

    # 3. markdown 规则集 — 文件级粒度（诚实标注未逐条登记）
    for name in ("coding_rules", "security_rules", "task_protection_rules",
                 "self_driven_rules", "l3_rules"):
        path = SOURCES[name]
        if path.exists():
            rules.append(RuleEntry(
                id=f"md:{name}", file=_rel(path),
                description=f"{name} 规则集（文件级登记，未逐条拆分）",
                registered_at=today,
            ))
    return rules


def load_registry() -> dict:
    if REGISTRY_PATH.exists():
        try:
            return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "rules": []}


def save_registry(reg: dict) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(
        json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def cmd_init() -> int:
    reg = load_registry()
    known = {r["id"]: r for r in reg.get("rules", [])}
    merged: list[dict] = []
    added = kept = 0
    for entry in scan_sources():
        old = known.pop(entry.id, None)
        if old is not None:
            old["description"] = entry.description  # 描述跟随源文件更新
            old["file"] = entry.file
            merged.append(old)
            kept += 1
        else:
            merged.append(asdict(entry))
            added += 1
    # 源里已消失的规则保留在册并标记（删规则也该留痕，而非静默蒸发）
    for ghost in known.values():
        ghost.setdefault("description", "")
        ghost["note"] = "源文件已无此规则（可能已被删除）— 确认后请手工移除"
        merged.append(ghost)
    reg["rules"] = merged
    reg["updated_at"] = _today()
    save_registry(reg)
    print(f"[init] 登记完成：新增 {added}，保留 {kept}， ghosts {len(known)} → {REGISTRY_PATH}")
    return 0


def stale_rules(days: int) -> tuple[list[dict], datetime]:
    reg = load_registry()
    cutoff = datetime.now() - timedelta(days=days)
    stale: list[dict] = []
    for r in reg.get("rules", []):
        lv = r.get("last_verified")
        if not lv:
            stale.append(r)
            continue
        try:
            if datetime.strptime(lv, "%Y-%m-%d") < cutoff:
                stale.append(r)
        except ValueError:
            stale.append(r)
    return stale, cutoff


def cmd_review(days: int, strict: bool) -> int:
    stale, cutoff = stale_rules(days)
    if not stale:
        print(f"[review] 全部 {len(load_registry().get('rules', []))} 条规则在 {days} 天内有验证记录 ✅")
        return 0
    print(f"[review] 复核队列（{len(stale)} 条超过 {days} 天未验证，截止 {cutoff:%Y-%m-%d}）：")
    for r in stale:
        lv = r.get("last_verified") or "从未"
        print(f"  - {r['id']:<22} last_verified={lv:<10} {r['description'][:50]}")
    if strict:
        print("[review] --strict：存在过期未复核规则 → exit 1")
        return 1
    return 0


def cmd_mark(rule_id: str, days_ago: int, evidence: str = "") -> int:
    reg = load_registry()
    date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    for r in reg.get("rules", []):
        if r["id"] == rule_id:
            r["last_verified"] = date
            if evidence:
                r["last_verified_evidence"] = evidence
            save_registry(reg)
            print(f"[mark] {rule_id} last_verified={date}")
            return 0
    print(f"[mark] 未找到规则 {rule_id}（先跑 init）", file=sys.stderr)
    return 1


def cmd_refresh(prefix: str, evidence: str = "") -> int:
    """批量刷新：把 id 以 prefix 开头的规则标为今日已验证。

    语义（诚实边界）：只应由**规则真实执行过**的调用点触发并注明证据——
    - `refresh linggit`：pre-commit 灵督审查管线实际跑过这批检查
    - `refresh guard`：doc_consistency 对守卫表做了跨文档一致性校验
    不代表"规则行为被人工复核"，复核仍走 review 队列。
    """
    reg = load_registry()
    date = _today()
    n = 0
    for r in reg.get("rules", []):
        if r["id"].startswith(prefix):
            r["last_verified"] = date
            if evidence:
                r["last_verified_evidence"] = evidence
            n += 1
    if n:
        save_registry(reg)
    print(f"[refresh] {prefix}:* {n} 条 → last_verified={date}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="规则衰减复核（登记/复核/标记验证）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="扫描规则源建册/合并")
    p_review = sub.add_parser("review", help="输出复核队列")
    p_review.add_argument("--days", type=int, default=30, help="验证有效期（天）")
    p_review.add_argument("--strict", action="store_true", help="有过期规则则 exit 1")
    p_mark = sub.add_parser("mark", help="手工记录一次验证")
    p_mark.add_argument("--id", required=True, help="规则 id，如 guard:H17")
    p_mark.add_argument("--days-ago", type=int, default=0, help="验证发生在几天前")
    p_mark.add_argument("--evidence", default="", help="验证证据来源说明")
    p_refresh = sub.add_parser("refresh", help="批量刷新 last_verified（仅限真实执行点调用）")
    p_refresh.add_argument("prefix", help="规则 id 前缀，如 linggit / guard")
    p_refresh.add_argument("--evidence", default="", help="执行证据说明（必填于真实接线中）")
    args = parser.parse_args(argv)

    if args.cmd == "init":
        return cmd_init()
    if args.cmd == "review":
        return cmd_review(args.days, args.strict)
    if args.cmd == "mark":
        return cmd_mark(args.id, args.days_ago, args.evidence)
    if args.cmd == "refresh":
        return cmd_refresh(args.prefix, args.evidence)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
