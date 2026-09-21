# lingclaude/model/quota_governance.py
"""配额窗口直查（方案C v4 P0#1，对齐 atomcode rate_limit hook——不猜要问）。

问题（cbff31f 实证）：GLM 1310 周期限额撞墙时只能从错误文本**猜**重置时刻，
30min 冷却会在重置前到期反复撞墙，2h 兜底则过度保守（窗口可能 20min 后就开）。

本模块把「猜出来的重置时刻」升级为**一等公民窗口对象**：
- 收集：record_error 路径顺带把错误文本里的重置时间戳提取为 QuotaWindow
- 复用：时间戳解析单源复用 task_router._HARD_QUOTA_RE + is_hard_quota_error
  （不造第二套解析——两套解析必然漂移）
- 决策：decide_from_windows() 问窗口不猜——有未过期窗口 → defer 到 reset_at；
  窗口过期（reset_at+宽限）→ allow 并标记 stale（真实调用是唯一后续验证）

边界纪律：本模块只做窗口记录与决策，不发送任何 HTTP 探活——
端点探活是 provider_probe 的职责（TTL 机制），两者互不侵犯。

停层声明（铁律 2 细则 5）：内核=QuotaWindowPool（进程内存态窗口池，
属 J4 存量私连存储既有形态，不新增直连）；接缝=decide_from_windows()
协议（defer/allow/stale 三态决策面）；实现=单实现（error_text 来源），
预留 usage_api 用量端点扩展位。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 窗口过期宽限：reset_at 之后仍 defer 这么久（服务端时钟偏差缓冲）
_RESET_GRACE_SECONDS = 60.0
# 无时间戳的硬配额错误的保守窗口（对齐 task_router._HARD_QUOTA_FALLBACK_COOLDOWN）
_FALLBACK_WINDOW_SECONDS = 7200.0


@dataclass
class QuotaWindow:
    """单个 provider 的配额窗口观察记录。

    source: "error_text"（从错误文本解析）/ "usage_api"（预留：未来接用量端点）
    """

    provider: str
    reset_at: datetime
    observed_at: datetime = field(default_factory=datetime.now)
    source: str = "error_text"
    kind: str = "unknown"          # monthly / weekly / 5h / unknown（尽力标注）

    def seconds_until_reset(self, now: Optional[datetime] = None) -> float:
        now = now or datetime.now()
        return (self.reset_at - now).total_seconds()

    def is_active(self, now: Optional[datetime] = None) -> bool:
        """窗口是否仍有效：now < reset_at + 宽限。"""
        return self.seconds_until_reset(now) > -_RESET_GRACE_SECONDS

    def to_dict(self, now: Optional[datetime] = None) -> dict[str, Any]:
        now = now or datetime.now()
        return {
            "provider": self.provider,
            "reset_at": self.reset_at.isoformat(),
            "seconds_until_reset": round(self.seconds_until_reset(now), 1),
            "active": self.is_active(now),
            "source": self.source,
            "kind": self.kind,
            "observed_at": self.observed_at.isoformat(),
        }


def extract_window_from_error(provider: str, error_detail: str) -> Optional[QuotaWindow]:
    """从硬配额错误文本提取窗口（单源解析：复用 task_router 既有正则与判定）。

    非硬配额错误 → None（调用方无需区分「解析失败」与「不是配额错误」，
    两者都不产生窗口）。
    """
    if not (error_detail or "").strip():
        return None
    try:
        from lingclaude.model.retry import is_hard_quota_error
        from lingclaude.model.task_router import _HARD_QUOTA_RE
    except ImportError:  # pragma: no cover 防循环导入兜底
        logger.warning("quota_governance: 解析单源不可用，跳过窗口提取")
        return None
    if not is_hard_quota_error(error_detail):
        return None
    m = _HARD_QUOTA_RE.search(error_detail)
    kind = "unknown"
    low = error_detail.lower()
    if "1310" in error_detail or "月" in error_detail:
        kind = "monthly"
    elif "周" in error_detail:
        kind = "weekly"
    elif "1308" in error_detail or "5h" in low or "5小时" in error_detail:
        kind = "5h"
    if m:
        ts = m.group(1) or m.group(2)
        try:
            reset_at = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            return QuotaWindow(provider=provider, reset_at=reset_at,
                               source="error_text", kind=kind)
        except ValueError:
            logger.warning("quota_governance: 时间戳解析失败 %r", ts)
    # 无时间戳：用兜底窗口（保守但可被后续成功调用推翻）
    return QuotaWindow(
        provider=provider,
        reset_at=datetime.now() + timedelta(seconds=_FALLBACK_WINDOW_SECONDS),
        source="error_text", kind=kind or "fallback",
    )


class QuotaWindowPool:
    """进程内窗口池：provider → 最新窗口（后写覆盖）。

    线程模型：_lock 保护 dict；窗口对象不可变使用（替换而非改字段）。
    """

    def __init__(self) -> None:
        self._windows: dict[str, QuotaWindow] = {}
        self._lock = threading.RLock()

    def record(self, window: QuotaWindow) -> QuotaWindow:
        """记录/刷新窗口（同名 provider 后写覆盖——最新观察最可信）。"""
        with self._lock:
            self._windows[window.provider] = window
        logger.info("quota_governance: %s 窗口记录 reset_at=%s (kind=%s, source=%s)",
                    window.provider, window.reset_at.isoformat(),
                    window.kind, window.source)
        return window

    def record_from_error(self, provider: str, error_detail: str) -> Optional[QuotaWindow]:
        """错误路径便捷入口：提取并记录；非配额错误 no-op 返回 None。"""
        window = extract_window_from_error(provider, error_detail)
        if window is not None:
            self.record(window)
        return window

    def clear(self, provider: Optional[str] = None) -> int:
        """清除窗口（真实成功调用后应调用——窗口只是观察，成功是最强证据）。"""
        with self._lock:
            if provider is None:
                n = len(self._windows)
                self._windows.clear()
                return n
            if self._windows.pop(provider, None) is not None:
                return 1
            return 0

    def decide_from_windows(self, provider: str,
                            now: Optional[datetime] = None) -> dict[str, Any]:
        """问窗口不猜：路由前查询该 provider 是否仍在配额等待期。

        返回:
            {"action": "allow", ...}                        — 无窗口/窗口已过期
            {"action": "defer", "reset_at": ..., ...}       — 窗口未到 reset_at
        窗口过期但仍在宽限内 → allow + stale=True（下次调用是验证，失败会再记录）。
        """
        now = now or datetime.now()
        with self._lock:
            window = self._windows.get(provider)
        if window is None:
            return {"action": "allow", "provider": provider, "reason": "no_window"}
        secs = window.seconds_until_reset(now)
        if secs > 0:
            return {
                "action": "defer",
                "provider": provider,
                "reason": "quota_window_active",
                "reset_at": window.reset_at.isoformat(),
                "seconds_until_reset": round(secs, 1),
                "kind": window.kind,
                "source": window.source,
            }
        stale = secs > -_RESET_GRACE_SECONDS
        return {
            "action": "allow",
            "provider": provider,
            "reason": "window_expired" if not stale else "window_grace",
            "stale": stale,
            "reset_at": window.reset_at.isoformat(),
            "kind": window.kind,
        }

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """全池投影（inspect/治理面板数据源）。过期窗口保留原样（不隐式清除）。"""
        now = datetime.now()
        with self._lock:
            return {p: w.to_dict(now) for p, w in self._windows.items()}


# 模块级单例（进程内唯一窗口池；测试用 _reset）
_default_pool: Optional[QuotaWindowPool] = None


def get_quota_pool() -> QuotaWindowPool:
    global _default_pool
    if _default_pool is None:
        _default_pool = QuotaWindowPool()
    return _default_pool


def _reset_quota_pool() -> None:
    """仅测试用。"""
    global _default_pool
    _default_pool = None
