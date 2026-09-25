#!/usr/bin/env python3
"""orphan_scan v2 — 五口径消费点扫描器（重建版）

依据：docs/research/handoff-orphan-scan-rebuild-20260925.md §3.1
通道：①完整路径 import ②from-import ③字符串动态装配 ④属性/相对引用 ⑤测试消费（单列）
硬约束：排除 .lingclaude/worktrees/**（12 副本噪音源）；.bak 不算代码；
       自身文件与自身 docstring 不算消费。
内置 --selfcheck：双向校验锚点（§3.2）l5_audit 必须有消费、
l7_cognitive_bridge 必须无生产消费，两条都对上才退出码 0。

用法：
  python3 scripts/orphan_scan_v2.py                 # 全量清单
  python3 scripts/orphan_scan_v2.py --selfcheck    # 只跑双向校验
  python3 scripts/orphan_scan_v2.py --module X     # 看单件明细
"""
from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = ROOT / "lingclaude" / "core"

# ---- 排除集（§3.3 worktree 噪音 + 非生产目录） ----
EXCLUDE_PARTS = {
    ".lingclaude/worktrees",   # 12 个 worktree 副本（上轮 13 倍重复计数噪音源）
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "data",                    # 账本/状态 JSON，非代码
    ".audit-records",
    ".atomcode",
    ".lingclaude/attic",       # 休眠件不算在产消费点，但引用它仍登记（保期权证据）
}

def excluded(p: Path) -> bool:
    rel = p.relative_to(ROOT).as_posix()
    for pat in EXCLUDE_PARTS:
        if rel.startswith(pat):
            return True
    return False

def is_bak(p: Path) -> bool:
    return p.name.endswith(".bak")

# ---- 收集 core 模块清单 ----
def core_modules() -> dict[str, Path]:
    mods = {}
    for p in sorted(CORE_DIR.glob("*.py")):
        if p.name == "__init__.py" or is_bak(p):
            continue
        mods[p.stem] = p
    return mods

# ---- 收集扫描目标文件（生产 + 测试） ----
def scan_targets() -> list[Path]:
    files = []
    for base in (ROOT / "lingclaude", ROOT / "tests"):
        for p in base.rglob("*.py"):
            if excluded(p) or is_bak(p):
                continue
            files.append(p)
    # 顶层散件（若有）
    for p in ROOT.glob("*.py"):
        if not excluded(p) and not is_bak(p):
            files.append(p)
    return sorted(set(files))

def pkg_parts(p: Path) -> list[str]:
    """文件所在包的点路径（目录链，相对 ROOT）。"""
    rel = p.relative_to(ROOT)
    return list(rel.parent.parts) if rel.parent != Path(".") else []

def resolve_from(pkg: list[str], level: int, module: str | None) -> str:
    """解析 relative import 的绝对目标。level=1 → 当前包。"""
    if level == 0:
        return module or ""
    base = pkg[: len(pkg) - (level - 1)] if level > 1 else pkg
    target = ".".join(base)
    if module:
        target = f"{target}.{module}" if target else module
    return target

class Consumer:
    __slots__ = ("file", "line", "chan")
    def __init__(self, file: str, line: int, chan: str):
        self.file, self.line, self.chan = file, line, chan
    def as_tuple(self):
        return (self.file, self.line, self.chan)

def scan_file(path: Path, mods: dict[str, Path]) -> list[tuple[str, Consumer]]:
    """返回 [(module_name, Consumer), ...]；字符串通道返回疑似命中待人工裁决。"""
    hits = []
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except SyntaxError:
        return hits
    rel = path.relative_to(ROOT).as_posix()
    is_test = rel.startswith("tests/")
    is_self_of = lambda m: rel == f"lingclaude/core/{m}.py"
    pkg = pkg_parts(path)
    chan = "⑤test" if is_test else "prod"

    # --- 通道①②④：AST import 与属性链 ---
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                # import lingclaude.core.X / import lingclaude.core.X.sub
                m = re.match(r"^(?:lingclaude\.)?core\.([A-Za-z_][\w]*)", a.name)
                if m and m.group(1) in mods and not is_self_of(m.group(1)):
                    hits.append((m.group(1), Consumer(rel, node.lineno, f"{chan}①")))
        elif isinstance(node, ast.ImportFrom):
            target = resolve_from(pkg, node.level, node.module)
            # from lingclaude.core.X import Y  /  from core.X import Y
            m = re.match(r"^(?:lingclaude\.)?core\.([A-Za-z_][\w]*)", target)
            if m and m.group(1) in mods and not is_self_of(m.group(1)):
                hits.append((m.group(1), Consumer(rel, node.lineno, f"{chan}②")))
                continue
            # from lingclaude.core import X  /  from core import X（level=0/1 均可能）
            if re.fullmatch(r"(?:lingclaude\.)?core", target):
                for a in node.names:
                    if a.name in mods and not is_self_of(a.name):
                        hits.append((a.name, Consumer(rel, node.lineno, f"{chan}②")))
        elif isinstance(node, ast.Attribute):
            # 通道④：core.X.attr 属性链（from lingclaude import core 后的用法）
            parts = []
            cur: ast.AST | None = node
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name) and cur.id in ("core", "lingclaude_core"):
                parts.append(cur.id)
                dotted = ".".join(reversed(parts))
                m = re.match(r"^(?:lingclaude_)?core\.([A-Za-z_][\w]*)", dotted)
                if m and m.group(1) in mods and not is_self_of(m.group(1)):
                    hits.append((m.group(1), Consumer(rel, node.lineno, f"{chan}④")))

    # --- 通道③：字符串动态装配（import_module / __import__ / "core.X" 字面量） ---
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            for m in re.finditer(r"(?:lingclaude\.)?core\.([A-Za-z_][\w]*)", v):
                mod = m.group(1)
                if mod in mods and not is_self_of(mod):
                    hits.append((mod, Consumer(rel, node.lineno, f"{chan}③str")))
    return hits

def run_scan() -> dict[str, dict]:
    mods = core_modules()
    table: dict[str, dict] = {
        m: {"prod": [], "test": []} for m in mods
    }
    for f in scan_targets():
        for mod, c in scan_file(f, mods):
            key = "test" if c.chan.startswith("⑤") else "prod"
            tup = c.as_tuple()
            if tup not in [(x if isinstance(x, tuple) else x.as_tuple()) for x in table[mod][key]]:
                table[mod][key].append(tup)
    # 汇总
    out = {}
    for m, d in table.items():
        out[m] = {
            "prod_count": len(d["prod"]),
            "test_count": len(d["test"]),
            "channels_prod": sorted({t[2] for t in d["prod"]}),
            "channels_test": sorted({t[2] for t in d["test"]}),
            "hits_prod": d["prod"],
            "hits_test": d["test"],
        }
    return out

def selfcheck(table: dict[str, dict]) -> bool:
    """§3.2 双向校验：两条都对上才 True。"""
    ok = True
    a = table.get("l5_audit", {})
    b = table.get("l7_cognitive_bridge", {})
    # 锚点1：l5_audit 必须有生产消费
    if a.get("prod_count", 0) > 0:
        print(f"[PASS] l5_audit 有生产消费：{a['prod_count']} 处，通道 {a['channels_prod']}")
        for t in a["hits_prod"][:5]:
            print(f"       e.g. {t[0]}:{t[1]} ({t[2]})")
    else:
        print("[FAIL] l5_audit 未报生产消费 —— 尺子仍有口径漏洞")
        ok = False
    # 锚点2：l7_cognitive_bridge 必须无生产消费（测试豁免登记不算）
    if b.get("prod_count", 0) == 0:
        print(f"[PASS] l7_cognitive_bridge 无生产消费（测试侧登记 {b.get('test_count', 0)} 处，为守卫豁免定性，不算消费）")
    else:
        print(f"[FAIL] l7_cognitive_bridge 报出 {b['prod_count']} 处生产消费 —— 需人工复核是否真消费")
        for t in b["hits_prod"]:
            print(f"       {t[0]}:{t[1]} ({t[2]})")
        ok = False
    return ok

def main():
    args = sys.argv[1:]
    table = run_scan()
    if "--selfcheck" in args:
        sys.exit(0 if selfcheck(table) else 2)
    if "--module" in args:
        i = args.index("--module")
        name = args[i + 1]
        d = table.get(name)
        if not d:
            print(f"NOT_FOUND: {name}")
            sys.exit(1)
        print(json.dumps({name: d}, ensure_ascii=False, indent=2))
        return
    # 全量输出
    selfcheck(table)
    print()
    orphans = [m for m, d in sorted(table.items()) if d["prod_count"] == 0]
    print(f"=== 零生产消费候选（{len(orphans)} 件，候选≠可回收，需逐件四判据）===")
    for m in orphans:
        d = table[m]
        tflag = f" test:{d['test_count']}" if d["test_count"] else ""
        print(f"  {m}{tflag}")
    alive = [m for m, d in table.items() if d["prod_count"] > 0]
    print(f"\n=== 有生产消费 {len(alive)} 件（不可误杀）===")
    json.dump(table, open(ROOT / ".atomcode" / "orphan_scan_v2_result.json", "w"), ensure_ascii=False, indent=1)
    print("\n明细已落：.atomcode/orphan_scan_v2_result.json")

if __name__ == "__main__":
    main()
