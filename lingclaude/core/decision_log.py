"""统一决策流（decisions ledger）——各门控判定依据的一等公民归宿。

问题（2026-10-27 Jev 架构对标实证）：lc 的各纯代码门控（success_gate/
risk_gate/fast_route/relevance 过滤）判定依据在调用点就地消费完即丢——
P 值、信号、拒绝理由没有统一事件流，事后无法回答「当时为何这么判」，
RSI 学习面（turn_learner / rule_extractor）也吃不到这些高价值监督信号。

本模块提供唯一的跨门控决策登记面：

    record_decision(domain=..., action=..., verdict=..., ...)

追加写入 data/arch_ledger/decisions.jsonl（单文件全局时序，按月滚转
decisions_YYYYMM.jsonl），对齐 verify_ledger / work_claim_log 的台账惯例。

核心不变式（与 verify_ledger 同源）：
- 只追加不删除（append-only）；
- fail-open：台账故障绝不阻断主流程（门控归门控，台账只是锚点）；
- 进程内去重窗口（默认 2s 同指纹跳写），防 fast_route 每秒多次调用刷盘；
- 边界纪律：不解析结果、不执行命令、不校验 verdict 真伪——本模块只做
  「决策的登记与检索」，语义由调用方（各 gate）负责。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

__all__ = [
    "DecisionDomain",
    "DecisionEntry",
    "DecisionLog",
    "get_decision_log",
    "record_decision",
]

# 决策域（与门控一一对应；新增 gate 必须在此登记，防自由字符串漂移）
DOMAIN_SUCCESS = "success"
DOMAIN_RISK = "risk"
DOMAIN_ROUTE = "route"
DOMAIN_RELEVANCE = "relevance"
DOMAIN_NEXT_WORKER = "next_worker"
VALID_DOMAINS = frozenset({
    DOMAIN_SUCCESS, DOMAIN_RISK, DOMAIN_ROUTE, DOMAIN_RELEVANCE, DOMAIN_NEXT_WORKER,
})

# 判定结论（受控词表，下游 evals 打标按此分组）
VERDICT_ALLOW = "allow"
VERDICT_DENY = "deny"
VERDICT_GRAY = "gray"
VALID_VERDICTS = frozenset({VERDICT_ALLOW, VERDICT_DENY, VERDICT_GRAY})

# 每日每域落盘上限：台账是锚点不是数据湖，防失控刷盘（触发即丢弃并计费计数）
MAX_ENTRIES_PER_DAY_PER_DOMAIN = 2000

# 进程内去重窗口（秒）：同指纹（domain+action+verdict+reason+session）短时间重复跳写
_DEDUP_WINDOW_S = 2.0

_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9_.-]")

_DEFAULT_ROOT = Path("data/arch_ledger")


def _safe_token(token: str, limit: int = 80) -> str:
    cleaned = _SAFE_TOKEN_RE.sub("_", str(token))[:limit]
    return cleaned or "unknown"


@dataclass(frozen=True)
class DecisionEntry:
    """单条决策事件（不可变——登记后不许改写，改判只能新条目）。"""

    ts: float
    domain: str
    action: str
    verdict: str
    reason: str
    session_id: str
    p: Optional[float] = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ts": self.ts,
            "ts_iso": datetime.fromtimestamp(self.ts).isoformat(timespec="seconds"),
            "domain": self.domain,
            "action": _safe_token(self.action, 120),
            "verdict": self.verdict,
            "reason": str(self.reason)[:200],
            "session_id": _safe_token(self.session_id, 64),
        }
        if self.p is not None:
            d["p"] = round(float(self.p), 3)
        if self.meta:
            d["meta"] = self.meta
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DecisionEntry":
        return cls(
            ts=float(d.get("ts", 0.0)),
            domain=str(d.get("domain", "")),
            action=str(d.get("action", "")),
            verdict=str(d.get("verdict", "")),
            reason=str(d.get("reason", "")),
            session_id=str(d.get("session_id", "")),
            p=d.get("p"),
            meta=dict(d.get("meta") or {}),
        )


@dataclass
class DecisionLog:
    """JSONL 追加台账（单文件全局时序，按月滚转）。

    root 默认 data/arch_ledger（对齐 verify_log 台账目录惯例），
    测试与嵌入方注入自定义 root。
    """

    root: Path
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    # 去重指纹 -> 最近写入时间
    _recent: dict[str, float] = field(default_factory=dict, repr=False)
    # 每日限流计数："(YYYYMMDD, domain)" -> count
    _day_counts: dict[tuple[str, str], int] = field(default_factory=dict, repr=False)
    _dropped: int = 0

    # ---------- 写 ----------

    def record(
        self,
        domain: str,
        action: str,
        verdict: str,
        reason: str = "",
        *,
        p: Optional[float] = None,
        session_id: str = "",
        meta: Optional[dict[str, Any]] = None,
        dedup: bool = True,
    ) -> DecisionEntry:
        """登记一条决策事件。任何磁盘故障 fail-open：返回条目但不保证落盘。

        dedup=True 时同指纹（domain+action+verdict+reason+session）在
        _DEDUP_WINDOW_S 内的重复写入直接跳过（返回条目但不落盘）。
        """
        entry = DecisionEntry(
            ts=time.time(),
            domain=domain,
            action=action,
            verdict=verdict,
            reason=reason,
            session_id=_resolve_session_id(session_id),
            p=p,
            meta=dict(meta or {}),
        )
        try:
            fingerprint = "|".join(
                _safe_token(x, 100)
                for x in (domain, action, verdict, reason, session_id)
            )
            now = time.time()
            with self._lock:
                if dedup:
                    last = self._recent.get(fingerprint)
                    if last is not None and now - last < _DEDUP_WINDOW_S:
                        return entry
                    self._recent[fingerprint] = now
                    if len(self._recent) > 256:  # 防指纹表无界增长
                        cutoff = now - _DEDUP_WINDOW_S * 2
                        self._recent = {
                            k: v for k, v in self._recent.items() if v > cutoff
                        }
                day_key = datetime.fromtimestamp(now).strftime("%Y%m%d")
                count_key = (day_key, domain)
                if self._day_counts.get(count_key, 0) >= MAX_ENTRIES_PER_DAY_PER_DOMAIN:
                    self._dropped += 1
                    return entry
                self._day_counts[count_key] = self._day_counts.get(count_key, 0) + 1
                self.root.mkdir(parents=True, exist_ok=True)
                path = self.root / f"decisions_{day_key[:6]}.jsonl"
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
            return entry
        except Exception:  # noqa: BLE001 — 台账 fail-open，绝不阻断主流程
            return entry

    # ---------- 读 ----------

    def iter_entries(
        self,
        *,
        domain: Optional[str] = None,
        verdict: Optional[str] = None,
        tail: int = 200,
    ) -> Iterator[dict[str, Any]]:
        """从最新文件向前读，返回过滤后的条目（供消费方拉取学习信号）。"""
        try:
            files = sorted(self.root.glob("decisions_*.jsonl"), reverse=True)
        except OSError:
            return
        produced = 0
        for path in files:
            if produced >= tail:
                return
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                if produced >= tail:
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if domain and d.get("domain") != domain:
                    continue
                if verdict and d.get("verdict") != verdict:
                    continue
                produced += 1
                yield d

    def stats(self) -> dict[str, Any]:
        """进程内计数快照（去重跳写/限流丢弃），供观测与测试断言。"""
        with self._lock:
            return {
                "dropped_rate_limit": self._dropped,
                "dedup_cache_size": len(self._recent),
                "day_counts": {f"{d}|{dom}": c for (d, dom), c in self._day_counts.items()},
            }


_DEFAULT_LOG: Optional[DecisionLog] = None
_DEFAULT_LOG_LOCK = threading.Lock()


def get_decision_log(root: Optional[Path] = None) -> DecisionLog:
    """单例获取（root 可注入；默认相对 CWD 的 data/arch_ledger）。"""
    global _DEFAULT_LOG
    if root is not None:
        return DecisionLog(root=root)
    if _DEFAULT_LOG is None:
        with _DEFAULT_LOG_LOCK:
            if _DEFAULT_LOG is None:
                _DEFAULT_LOG = DecisionLog(root=_DEFAULT_ROOT)
    return _DEFAULT_LOG


# ---------- 会话归因（2026-10-27 收口「session_id=unknown」）----------
# 进程级 provider：query_engine 装配时注册一次（engine.session_id 即会话身份
# 单源），此后全进程所有 gate 调用 record_decision 不传 session_id 也自动
# 归因——fast_lane 等模块级函数零改动获得会话上下文。已知局限：同进程多
# engine（如 inprocess 子代理另建 engine）时 provider 指向最后注册者，并发
# 期归因可能串会话（best-effort 语义，后续可升级 contextvar）。
_SESSION_PROVIDER_LOCK = threading.Lock()
_SESSION_PROVIDER: Optional[Callable[[], str]] = None


def set_session_provider(fn: Optional[Callable[[], str]]) -> None:
    """注册/注销（fn=None）会话身份 provider。注册故障由消费端 fail-open 兜底。"""
    global _SESSION_PROVIDER
    with _SESSION_PROVIDER_LOCK:
        _SESSION_PROVIDER = fn


def _resolve_session_id(explicit: Optional[str]) -> str:
    """归因解析：显式参数 > 进程级 provider > 空串（落 unknown token）。"""
    if explicit:
        return str(explicit)
    try:
        with _SESSION_PROVIDER_LOCK:
            fn = _SESSION_PROVIDER
        if fn is not None:
            return str(fn() or "")
    except Exception:  # noqa: BLE001 — 归因失败绝不阻断判定登记
        pass
    return ""


def record_decision(
    domain: str,
    action: str,
    verdict: str,
    reason: str = "",
    *,
    p: Optional[float] = None,
    session_id: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
    dedup: bool = True,
) -> DecisionEntry:
    """模块级便捷入口：拿单例并登记。任何异常自吞（fail-open）。"""
    try:
        return get_decision_log().record(
            domain, action, verdict, reason,
            p=p, session_id=session_id, meta=meta, dedup=dedup,
        )
    except Exception:  # noqa: BLE001
        try:
            return DecisionEntry(
                ts=time.time(), domain=domain, action=action, verdict=verdict,
                reason=reason, session_id=session_id, p=p, meta=dict(meta or {}),
            )
        except Exception:  # noqa: BLE001
            raise RuntimeError("unreachable")