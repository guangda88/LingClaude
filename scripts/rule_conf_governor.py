#!/usr/bin/env python3
"""F5 conf 治理器：verify_ledger 证据挂钩 conf 上限（0.9/0.6/0.5 三档）。

依据 docs/research/20260922_flywheel_closure_diagnosis.md §M.9 v4.2 F5 触发条件：
    ① corrections ≥ 20 条 —— 已满足（2026-09-23 回填 1194 条，source=system_backfill）
    ② verify_ledger 自动落账全链路确认 —— 部分满足（CLI+测试在，生产覆盖未全）
    ③ F0 基线 ≥ 1 周 —— 未满足（刚起步）
故本脚本**挂档即手动执行**，不接 daemon 自动调度；条件②③满足后再升级值守。

三档上限语义（诚实标注证据强度）：
    0.9  有 verify_ledger 证据：规则的 context_keywords 与已验证 claim 交叉命中
         （"这条规则被真实执行证据背书过"）
    0.6  中性默认：无证据也无负面信号（"可用但未经证实"）
    0.5  被纠正过：规则的错误线索出现在某条 correction 的 original_error 里
         （"该错误模式已经被系统纠正过一次，别再高置信推荐它"）

用法：
    python3 scripts/rule_conf_governor.py            # 执行治理
    python3 scripts/rule_conf_governor.py --dry-run  # 只报告不动库
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

KB_PATH = Path.home() / "lingclaude/.lingclaude/knowledge.db"
CAP_EVIDENCED = 0.9
CAP_NEUTRAL = 0.6
CAP_CORRECTED = 0.5
_TOKEN_SPLIT = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]+")


def _tokens(text: str) -> set[str]:
    """claim/关键词 → 小写 token 集（长度≥2，数字串保留）。"""
    out = set()
    for tok in _TOKEN_SPLIT.split((text or "").lower()):
        if len(tok) >= 2:
            out.add(tok)
    return out


def load_evidence_tokens() -> set[str]:
    """verify_ledger 全部已验证 claim 的 token 并集。"""
    from lingclaude.core.verify_ledger import get_verify_ledger

    vl = get_verify_ledger()
    toks: set[str] = set()
    for trace in vl.iter_traces():
        res = vl.query(trace_id=trace)
        entries = res.value if hasattr(res, "value") else res
        for e in entries or []:
            toks |= _tokens(getattr(e, "claim", ""))
    return toks


def load_correction_clues(conn: sqlite3.Connection) -> set[str]:
    """corrections.original_error 的 token 并集（含 session 标记剔除后）。

    注意两库分工（§M.6 B 裁定）：corrections 在 data_flywheel.db，
    规则在 knowledge.db——本函数用独立连接，别重蹈"连 A 库查 B 表"。
    """
    fw_path = Path.home() / "lingclaude/.lingclaude/data_flywheel.db"
    fw = sqlite3.connect(fw_path)
    try:
        rows = fw.execute(
            "SELECT original_error FROM corrections WHERE source IN "
            "('system_backfill','user','claude')"
        ).fetchall()
    finally:
        fw.close()
    # 保留原始文本（供片段级子串匹配），token 集不再用于 corrected 判定
    texts: set[str] = set()
    for (orig,) in rows:
        cleaned = re.sub(r"\[session:[0-9a-f]+\]", " ", orig or "")
        texts.add(cleaned)
    return texts


def _anchors(pj: dict, description: str) -> set[str]:
    """规则 → 具体错误锚点片段（len≥12）：code_patterns 的 error 值 + description。

    片段级而非 token 级：'error'/'failed' 这类通用词碰撞会把中性规则误判
    成 corrected（实测 31 条里大半是 'error' 单词撞出来的假阳性）。
    """
    import json as _json

    anchors: set[str] = set()
    for cp in pj.get("code_patterns", []) or []:
        s = str(cp)
        # code_patterns 存的是 JSON 字符串壳，剥出 error 值
        try:
            inner = _json.loads(s)
            if isinstance(inner, dict):
                s = str(inner.get("error") or inner)
            elif isinstance(inner, str):
                s = inner
        except Exception:
            pass
        s = s.strip().strip('"')
        if len(s) >= 12:
            anchors.add(s)
    # description 尾部常带 msg 前 80 字符（build_rule 拼装），同样可作锚点
    for seg in re.split(r"[|；;：:]\s*", description or ""):
        seg = seg.strip()
        if len(seg) >= 12 and not seg.startswith(("重复", "频率", "跨会话")):
            anchors.add(seg)
    return anchors


def governed(conn: sqlite3.Connection, evidence: set[str], corrected_corpus: set[str],
             dry: bool) -> list[tuple[str, float, float, str]]:
    """对候选规则逐条定档 → UPDATE。返回 (id, 旧conf, 新conf, 档位) 列表。

    三档判定：
        evidenced — 规则关键词与 verify claim 的 token 交叉 ≥2（证据背书）
        corrected — 规则的具体错误锚点片段出现在 correction 全文语料（真被纠正过）
        neutral   — 其余
    """
    rows = conn.execute(
        "SELECT id, confidence, pattern_json, description FROM rules "
        "WHERE id LIKE 'flywheel_pattern_%' OR id LIKE 'refined_%'"
    ).fetchall()
    import json

    corpus = " \n ".join(corrected_corpus)
    changes = []
    for rid, conf, pjson, desc in rows:
        try:
            pj = json.loads(pjson or "{}")
        except Exception:
            pj = {}
        kws: set[str] = set()
        for kw in pj.get("context_keywords", []) or []:
            kws |= _tokens(str(kw))

        # evidence：token 交叉 ≥2（单 token 太脆，'tool' 这类词谁都可能有）
        tier = "neutral"
        if len(kws & evidence) >= 2:
            tier = "evidenced"
        else:
            anchors = _anchors(pj, desc or "")
            if anchors and any(a in corpus for a in anchors):
                tier = "corrected"

        cap = {"evidenced": CAP_EVIDENCED, "corrected": CAP_CORRECTED}.get(tier, CAP_NEUTRAL)
        # SET-to-cap 语义（非 min-clamp）：治理器是这三类机器生成规则的
        # 置信度权威。min() 只降不升——一旦某版判定逻辑出错把 conf 压低，
        # 修正逻辑后重跑也无法恢复（实测踩坑：token 碰撞版错误压了 61 条）。
        # SET 语义幂等且自愈：重跑即按当前最可信判定归位。
        new_conf = cap
        if abs(new_conf - float(conf or 0.0)) > 1e-9:
            changes.append((rid, float(conf), new_conf, tier))
            if not dry:
                conn.execute(
                    "UPDATE rules SET confidence=?, updated_at=datetime('now') "
                    "WHERE id=?",
                    (new_conf, rid),
                )
    if not dry:
        with conn:
            conn.commit()
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="只报告不动库")
    args = ap.parse_args()
    if not KB_PATH.exists():
        print(f"[FAIL] kb 不存在: {KB_PATH}")
        return 1

    evidence = load_evidence_tokens()
    conn = sqlite3.connect(KB_PATH)
    try:
        corrected_corpus = load_correction_clues(conn)
        changes = governed(conn, evidence, corrected_corpus, args.dry_run)
        mode = "[DRY]" if args.dry_run else "[OK]"
        print(
            f"{mode} evidence_tokens={len(evidence)} "
            f"correction_corpus={len(corrected_corpus)}条 "
            f"降conf改动={len(changes)}"
        )
        by_tier: dict[str, int] = {}
        for _, old, new, tier in changes:
            by_tier[tier] = by_tier.get(tier, 0) + 1
        for tier, n in sorted(by_tier.items()):
            print(f"  {tier}: {n}")
        for rid, old, new, tier in changes[:5]:
            print(f"  e.g. {rid[:40]} {old}→{new} ({tier})")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
