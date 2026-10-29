"""lc 最小回放器：journal/rollout/决策流 → agentplay/v1 兼容子集 + 本地 HTML。

来源（2026-10-29 五方评审 ④，AgentPlay schema 借鉴——借 schema 不借运行时）:
    AgentPlay 的核心资产是 agentplay/v1 脱敏 schema（只增不改、可测试 redaction），
    其 viewer 运行时读 ~/.claude/~/.codex 布局，lc 不在其中任何一种。
    本模块把 lc 三源映射为该 schema 的兼容子集:
        journal  tool_call  → event kind=tool     (tool/verb/seq)
        journal  tool_result → 同 seq 事件 err/outcome
        journal  turn_end    → event kind=llm      (tokens)
        journal  checkpoint  → event kind=compaction (messages_count 下降时)
        rollout  checkpoint  → totals.token 曲线点（input/output）
        decisions 台账(同 session_id) → kind=route 注记（verdict/p/reason）

脱敏契约（schema 要求不含 prompt 原文——lc 源天然满足）:
    tool_result.output_preview 截 200 字符且可 --redact 关闭预览；
    arguments 只保留 keys（工具参数名清单），不保留值。

幂等:
    watermark data/arch_ledger/replay_watermark.json 记 per-session 已导出事件数
    （journal 为 append-only，序号即水位）；重跑只导出新增事件并整文件重写。
    export 输出为确定性 JSON（排序/缩进固定），同输入必同输出。

用法:
    python3 scripts/session_map.py --session <id>            # 导出 JSON(map)
    python3 scripts/session_map.py --session <id> --html     # 附带本地 HTML
    python3 scripts/session_map.py --session <id> --redact   # 输出不含预览文本
"""
from __future__ import annotations

import argparse
import html as _html
import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LING_DIR = _PROJECT_ROOT / ".lingclaude"
_JOURNAL_DIR = _LING_DIR / "journals"
_ROLLOUT_DIR = _LING_DIR / "rollouts"
_LEDGER_DIR = _PROJECT_ROOT / "data" / "arch_ledger"
_WATERMARK = _LEDGER_DIR / "replay_watermark.json"

SCHEMA_ID = "agentplay/v1-lc-subset"  # 明示为子集，不冒充完整 schema
_PREVIEW_LIMIT = 200
_KIND_TOOL = "tool"
_KIND_LLM = "llm"
_KIND_ROUTE = "route"
_KIND_COMPACTION = "compaction"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_journal(session_id: str) -> list[dict]:
    return _read_jsonl(_JOURNAL_DIR / f"{session_id}.jsonl")


def load_rollout(session_id: str) -> list[dict]:
    """rollout 文件名是 thread_id，session_id 在首行 session_meta 里——扫首行匹配。"""
    if not _ROLLOUT_DIR.exists():
        return []
    for p in sorted(_ROLLOUT_DIR.glob("rollout-*.jsonl")):
        with p.open(encoding="utf-8", errors="replace") as f:
            first = f.readline()
        try:
            meta = json.loads(first)
        except json.JSONDecodeError:
            continue
        if meta.get("event") == "session_meta" and meta.get("session_id") == session_id:
            return _read_jsonl(p)
    return []


def load_decisions(session_id: str, months: list[str] | None = None) -> list[dict]:
    """按 session_id 关联决策流条目（decisions_YYYYMM.jsonl）。"""
    if months is None:
        months = sorted(
            p.stem.replace("decisions_", "")
            for p in _LEDGER_DIR.glob("decisions_*.jsonl")
        )
    out: list[dict] = []
    for m in months:
        for d in _read_jsonl(_LEDGER_DIR / f"decisions_{m}.jsonl"):
            if d.get("session_id") == session_id:
                out.append(d)
    return out


def _verb_of(tool_name: str) -> str:
    n = (tool_name or "").lower()
    if any(k in n for k in ("read", "cat", "view")):
        return "read"
    if any(k in n for k in ("grep", "search", "glob", "find")):
        return "search"
    if any(k in n for k in ("edit", "write", "create", "insert", "delete", "replace")):
        return "edit"
    if any(k in n for k in ("bash", "exec", "run", "command")):
        return "exec"
    if any(k in n for k in ("web", "fetch", "http")):
        return "web"
    return "tool"


def build_map(
    session_id: str,
    *,
    redact: bool = False,
    journal: list[dict] | None = None,
    rollout: list[dict] | None = None,
    decisions: list[dict] | None = None,
) -> dict:
    """三源 → agentplay/v1 兼容子集（全量确定性重建——同输入必同输出）。"""
    journal = journal if journal is not None else load_journal(session_id)
    rollout = rollout if rollout is not None else load_rollout(session_id)
    decisions = decisions if decisions is not None else load_decisions(session_id)

    events: list[dict] = []
    t0: float | None = None

    def rel_ts(iso: str | None) -> int:
        nonlocal t0
        try:
            t = datetime.fromisoformat(str(iso)).timestamp()
        except (ValueError, TypeError):
            t = None
        if t is None:
            return 0
        if t0 is None:
            t0 = t
        return int((t - t0) * 1000)

    # --- journal: tool_call / tool_result 以 tool_call_id 配对 ---
    pending: dict[str, dict] = {}
    for idx, e in enumerate(journal):
        et = e.get("type")
        if et == "tool_call":
            ev = {
                "seq": idx,
                "t": rel_ts(e.get("timestamp")),
                "kind": _KIND_TOOL,
                "agent": "main",
                "tool": e.get("name"),
                "verb": _verb_of(str(e.get("name"))),
                "call_id": e.get("tool_call_id"),
                "arg_keys": sorted((e.get("arguments") or {}).keys())
                if isinstance(e.get("arguments"), dict)
                else [],
            }
            pending[str(e.get("tool_call_id"))] = ev
            events.append(ev)
        elif et == "tool_result":
            ev = pending.get(str(e.get("tool_call_id")))
            if ev is None:
                continue
            ev["err"] = bool(e.get("is_error"))
            prev = str(e.get("output_preview") or "")
            ev["outcome"] = "" if redact else prev[:_PREVIEW_LIMIT]
        elif et == "turn_end":
            events.append(
                {
                    "seq": idx,
                    "t": rel_ts(e.get("timestamp")),
                    "kind": _KIND_LLM,
                    "agent": "main",
                    "in_tok": e.get("total_input"),
                    "out_tok": e.get("total_output"),
                }
            )
        elif et == "checkpoint":
            events.append(
                {
                    "seq": idx,
                    "t": rel_ts(e.get("timestamp")),
                    "kind": _KIND_COMPACTION,
                    "agent": "main",
                    "messages": e.get("messages_count"),
                }
            )

    # --- rollout: token 曲线（totals 点）---
    token_curve = [
        {
            "t": rel_ts(r.get("ts")),
            "round": r.get("round_idx"),
            "in": r.get("total_input"),
            "out": r.get("total_output"),
        }
        for r in rollout
        if r.get("event") == "checkpoint"
    ]

    # --- decisions: route 注记 ---
    route_events = [
        {
            "seq": None,
            "t": rel_ts(None) if False else int(float(d.get("ts", 0)) * 1000),
            "kind": _KIND_ROUTE,
            "agent": "main",
            "tool": d.get("action"),
            "verdict": d.get("verdict"),
            "p": d.get("p"),
            "reason": str(d.get("reason") or "")[:120],
        }
        for d in decisions
    ]

    events.sort(key=lambda x: (x["t"], x["seq"] if x.get("seq") is not None else 10**9))

    tool_counter = Counter(e["tool"] for e in events if e["kind"] == _KIND_TOOL)
    first_iso = None
    if journal:
        first_iso = journal[0].get("timestamp")

    return {
        "schema": SCHEMA_ID,
        "session": {
            "id": session_id,
            "agentTool": "lc/lingclaude",
            "startedAt": first_iso,
            "outcome": "unknown",  # done 域闭环建立前诚实标 unknown
            "truncated": False,
        },
        "totals": {
            "calls": sum(1 for e in events if e["kind"] == _KIND_TOOL),
            "errors": sum(1 for e in events if e.get("err")),
            "llm_turns": sum(1 for e in events if e["kind"] == _KIND_LLM),
            "routes": len(route_events),
            "tokens_in": max((c.get("in") or 0) for c in token_curve) if token_curve else 0,
            "tokens_out": max((c.get("out") or 0) for c in token_curve) if token_curve else 0,
        },
        "files": sorted(tool_counter.keys()),
        "events": events,
        "token_curve": token_curve,
        "route_events": route_events,
    }


# ---------------- watermark（幂等） ----------------


def _load_watermark() -> dict:
    try:
        return json.loads(_WATERMARK.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 无水位/损坏 → 全量导出
        return {}


def _save_watermark(wm: dict) -> None:
    _WATERMARK.write_text(
        json.dumps(wm, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )


def watermark_for(session_id: str) -> int | None:
    return _load_watermark().get(session_id)


def advance_watermark(session_id: str, seq: int) -> None:
    wm = _load_watermark()
    wm[session_id] = max(int(wm.get(session_id, 0)), int(seq))
    wm["_updated"] = datetime.now().isoformat()
    _save_watermark(wm)


# ---------------- 本地 HTML（stdlib 渲染，零外发） ----------------

_HTML_TMPL = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>lc replay {sid}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px;background:#0f1115;color:#d7dce3}}
h1{{font-size:18px}} .tot{{color:#8b93a1;font-size:13px;margin-bottom:12px}}
.e{{border-left:3px solid #2a2f3a;padding:4px 10px;margin:3px 0;font-size:13px}}
.tool{{border-color:#4c8dff}} .llm{{border-color:#9a6bff}} .route{{border-color:#e0a63c}}
.err{{border-color:#e05252;background:#2a1518}} .compaction{{border-color:#3aa07a}}
.arg{{color:#8b93a1}} .pv{{color:#7a8290;white-space:pre-wrap}}
</style></head><body>
<h1>lc replay — {sid}</h1>
<div class="tot">{totals}</div>
{body}
</body></html>
"""


def render_html(m: dict) -> str:
    rows = []
    for e in m["events"]:
        cls = e["kind"]
        extra = f" err" if e.get("err") else ""
        head = f"[{e['t']:>7}ms] {e['kind']}"
        if e["kind"] == _KIND_TOOL:
            line = f"{head} {e.get('tool')} verb={e.get('verb')} args={e.get('arg_keys')}"
            if e.get("outcome"):
                line += f'\n<div class="pv">{_html.escape(str(e["outcome"]))}</div>'
        elif e["kind"] == _KIND_LLM:
            line = f"{head} in={e.get('in_tok')} out={e.get('out_tok')}"
        elif e["kind"] == _KIND_ROUTE:
            line = (
                f"{head} {e.get('tool')} verdict={e.get('verdict')} "
                f"p={e.get('p')} {e.get('reason')}"
            )
        else:
            line = f"{head} messages={e.get('messages')}"
        rows.append(f'<div class="e {cls}{extra}">{_html.escape(line)}</div>')
    tot = m["totals"]
    tot_s = (
        f"calls={tot['calls']} errors={tot['errors']} llm_turns={tot['llm_turns']} "
        f"routes={tot['routes']} tokens={tot['tokens_in']}/{tot['tokens_out']}"
    )
    return _HTML_TMPL.format(sid=_html.escape(m["session"]["id"]), totals=tot_s, body="\n".join(rows))


def main() -> int:
    ap = argparse.ArgumentParser(description="lc 最小回放器（agentplay/v1 子集）")
    ap.add_argument("--session", required=True, help="session_id")
    ap.add_argument("--html", action="store_true", help="同时渲染本地 HTML")
    ap.add_argument("--redact", action="store_true", help="不导出工具输出预览（脱敏契约）")
    ap.add_argument("--out", default=None, help="输出目录（默认 data/replay/）")
    ap.add_argument("--no-watermark", action="store_true", help="忽略并推进水位（调试用）")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else _PROJECT_ROOT / "data" / "replay"
    out_dir.mkdir(parents=True, exist_ok=True)

    wm = None if args.no_watermark else watermark_for(args.session)
    n_journal = sum(1 for _ in (_JOURNAL_DIR / f"{args.session}.jsonl").open(encoding="utf-8", errors="replace")) if (_JOURNAL_DIR / f"{args.session}.jsonl").exists() else 0
    if wm is not None and n_journal <= wm:
        print(f"[replay] {args.session}: journal 无新增（{n_journal}<={wm}），幂等短路")
        return 0

    m = build_map(args.session, redact=args.redact)
    if not m["events"] and not m["token_curve"]:
        print(f"[replay] session {args.session} 无可导出事件", file=sys.stderr)
        return 1

    n_new = len(m["events"])
    json_path = out_dir / f"{args.session}.map.json"
    json_path.write_text(
        json.dumps(m, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )
    if args.html:
        html_path = out_dir / f"{args.session}.map.html"
        html_path.write_text(render_html(m), encoding="utf-8")
        print(f"[replay] html → {html_path}")

    max_seq = max((e["seq"] for e in m["events"] if e.get("seq") is not None), default=-1)
    if not args.no_watermark:
        advance_watermark(args.session, max_seq + 1)
    print(
        f"[replay] {json_path.name}: events={n_new} "
        f"calls={m['totals']['calls']} errors={m['totals']['errors']} "
        f"routes={m['totals']['routes']} (watermark={'off' if args.no_watermark else 'on'})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
