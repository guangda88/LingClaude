"""验证事件台账（verify ledger）——证据状态的持久化锚点。

问题（2026-09-21 模型幻觉审计实证）：长对话压缩后原始执行结果丢失，
模型只能靠上下文里的「结论」回忆验证状态，双向出错——
- 真幻觉：高压下断言从未发生的验证（编造落盘/编造测试通过）；
- 误报：把真实验证过的声明标注为「未验证/伪造」（过度保守一刀切）。
两者共享同一根源：证据没有持久化锚点。

本模块把每次关键验证升格为一等公民事件，追加写入
data/arch_ledger/verify_log/<trace_id>.jsonl：
{"ts", "kind", "claim", "evidence", "digest", "trace_id", "via"}

核心不变式：
- 只追加不删除（append-only，对齐 work_claim_log 台账先例）；
- digest = 证据文本的 sha256 前 16 位——「声称验证过」可当场哈希复核，
  不依赖模型的主观回忆；
- 所有 I/O 失败 fail-open：台账故障绝不阻断主流程（验证归验证，
  台账只是锚点，锚点坏了不许把真的拖成假的）。

停层声明（铁律 2 细则 5）：内核=VerifyLedger（单例 JSONL 追加台账，
进程内共享 + 磁盘持久）；接缝=record()/query()/anchor_block() 协议
（记录 / 查询 / 恢复注入三个操作面）；实现=单实现（本地 JSONL），
预留 LingBus 跨成员核验扩展位。

边界纪律：不解析测试输出、不执行任何命令、不校验 claim 真伪——
本模块只做「声明的登记与检索」，真伪由 evidence 字段承载的
原始执行输出供第三方复核（模型自己说了不算）。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

# 四类验证事件（层2 标注的推导来源）
KIND_FILE_CREATED = "file_created"
KIND_TEST_PASSED = "test_passed"
KIND_COMMIT_CREATED = "commit_created"
KIND_CLAIM_VERIFIED = "claim_verified"
VALID_KINDS = frozenset({
    KIND_FILE_CREATED, KIND_TEST_PASSED, KIND_COMMIT_CREATED, KIND_CLAIM_VERIFIED,
})

# JSONL 默认上限：每个对话轨迹文件最多保留条数（超限从头部截断，防膨胀）
MAX_ENTRIES_PER_TRACE = 2000
# 锚点块默认注入条数（层3 /resume 接线取最近 N 条）
ANCHOR_TAIL_DEFAULT = 20

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_trace_id(trace_id: str) -> str:
    """轨迹 ID 清洗：只留文件名安全字符，防路径逃逸。"""
    cleaned = _SAFE_ID_RE.sub("_", trace_id)[:80] or "unknown"
    return cleaned


def evidence_digest(evidence: str) -> str:
    """证据指纹：sha256 前 16 位。声明复核 = 重算哈希比对。"""
    return "sha256:" + hashlib.sha256(evidence.encode("utf-8", "ignore")).hexdigest()[:16]


@dataclass(frozen=True)
class VerifyEntry:
    """单条验证事件（不可变——登记后不许改写，错登只能新条目纠正）。"""

    ts: float
    kind: str
    claim: str
    evidence: str
    digest: str
    trace_id: str
    via: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts, "kind": self.kind, "claim": self.claim,
            "evidence": self.evidence, "digest": self.digest,
            "trace_id": self.trace_id, "via": self.via, "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VerifyEntry":
        return cls(
            ts=float(d.get("ts", 0.0)), kind=str(d.get("kind", "")),
            claim=str(d.get("claim", "")), evidence=str(d.get("evidence", "")),
            digest=str(d.get("digest", "")), trace_id=str(d.get("trace_id", "")),
            via=str(d.get("via", "")), note=str(d.get("note", "")),
        )


@dataclass
class VerifyLedger:
    """JSONL 追加台账（每个 trace 一个文件）。

    root 默认 data/arch_ledger/verify_log（对齐 work_claim_log 的台账目录惯例），
    测试与嵌入方注入自定义 root。
    """

    root: Path
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ---------- 写（层1） ----------

    def record(
        self,
        kind: str,
        claim: str,
        evidence: str,
        trace_id: str,
        via: str = "",
        note: str = "",
    ) -> VerifyEntry:
        """登记一条验证事件。任何磁盘故障 fail-open：返回条目但不保证落盘。

        claim 与 evidence 语义（写进纪律，防滥用）：
        - claim  = 模型要说的那句话（"tests/test_x.py 7/7 全绿"）；
        - evidence = 产生这句话的**原始执行输出片段**（pytest 摘要行等），
          不是结论复述——摘要的摘要不算证据。
        """
        if kind not in VALID_KINDS:
            raise ValueError(f"unknown verify kind: {kind!r} (valid: {sorted(VALID_KINDS)})")
        entry = VerifyEntry(
            ts=datetime.now().timestamp(),
            kind=kind,
            claim=claim[:500],
            evidence=evidence[:2000],
            digest=evidence_digest(evidence),
            trace_id=trace_id,
            via=via[:100],
            note=note[:300],
        )
        try:
            with self._lock:
                self._append_locked(entry)
        except Exception:  # noqa: BLE001 fail-open：台账故障不阻断主流程
            pass
        return entry

    def _append_locked(self, entry: VerifyEntry) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{_safe_trace_id(entry.trace_id)}.jsonl"
        lines: list[str] = []
        if path.exists():
            raw = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            # 超限从头部截断（保留最近 MAX 条）
            if len(raw) >= MAX_ENTRIES_PER_TRACE:
                raw = raw[-(MAX_ENTRIES_PER_TRACE - 1):]
            lines = raw
        lines.append(json.dumps(entry.to_dict(), ensure_ascii=False))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ---------- 读（层2 标注推导） ----------

    def query(
        self,
        trace_id: str,
        kind: Optional[str] = None,
        claim_substring: Optional[str] = None,
    ) -> list[VerifyEntry]:
        """按轨迹检索验证事件（可按 kind / claim 子串过滤），最新在后。"""
        try:
            path = self.root / f"{_safe_trace_id(trace_id)}.jsonl"
            if not path.exists():
                return []
            out: list[VerifyEntry] = []
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    e = VerifyEntry.from_dict(json.loads(line))
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue  # 坏行跳过，台账容错
                if kind is not None and e.kind != kind:
                    continue
                if claim_substring is not None and claim_substring not in e.claim:
                    continue
                out.append(e)
            return out
        except Exception:  # noqa: BLE001 fail-open
            return []

    def verify_claim(self, trace_id: str, claim_substring: str) -> Optional[VerifyEntry]:
        """声明核验：存在匹配条目即返回（三方标注推导用），否则 None。"""
        matches = self.query(trace_id, claim_substring=claim_substring)
        return matches[-1] if matches else None

    # ---------- 恢复注入（层3） ----------

    def anchor_block(
        self,
        trace_id: str,
        tail: int = ANCHOR_TAIL_DEFAULT,
    ) -> Optional[str]:
        """生成 /resume 注入的锚点块（最近 tail 条摘要，带 digest）。

        无条目返回 None（不注入空块）。文本形态对齐 system 注入惯例。
        """
        entries = self.query(trace_id)
        if not entries:
            return None
        lines = [
            f"[验证台账锚点] 对话 {trace_id} 最近 {min(tail, len(entries))} 条验证事件"
            "（digest 可复核，非主观回忆）:"
        ]
        for e in entries[-tail:]:
            ts_h = datetime.fromtimestamp(e.ts).strftime("%m-%d %H:%M")
            lines.append(
                f"  {ts_h} [{e.kind}] {e.claim} (digest={e.digest[7:23]})"
            )
        return "\n".join(lines)

    def iter_traces(self) -> Iterator[str]:
        """枚举已有台账的轨迹 ID（运维/统计用）。"""
        try:
            if not self.root.exists():
                return
            for p in sorted(self.root.glob("*.jsonl")):
                yield p.stem
        except Exception:  # noqa: BLE001 fail-open
            return


# ---------- 模块级便捷入口（对接线方的最小依赖面） ----------

_DEFAULT_ROOT = Path("data/arch_ledger/verify_log")
_default: Optional[VerifyLedger] = None
_default_lock = threading.Lock()


def get_verify_ledger(root: Optional[Path] = None) -> VerifyLedger:
    """单例获取（root 可注入；默认相对 CWD 的 data/arch_ledger/verify_log）。"""
    global _default
    if root is not None:
        return VerifyLedger(root=root)
    with _default_lock:
        if _default is None:
            _default = VerifyLedger(root=_DEFAULT_ROOT)
        return _default


def record_verify(kind: str, claim: str, evidence: str, trace_id: str,
                  via: str = "", note: str = "") -> VerifyEntry:
    """便捷包装：接线方一行登记（fail-open 由 VerifyLedger.record 保证）。"""
    return get_verify_ledger().record(
        kind=kind, claim=claim, evidence=evidence,
        trace_id=trace_id, via=via, note=note,
    )
