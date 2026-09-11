"""事实校验层 (Fact Checker) — T1

防低质模型幻觉: 模型答完后查灵知 KG 确认每个 claim 有来源。
无来源 claim 标警告或重答。

灵知对接: 通过 sys.path 旁路导入 lingzhi FactVerifier。
P0.3 (V3 §五 E2): mock 假阳性已移除，fail-closed —— 灵知不可用时
显式抛 RuntimeError（含修复指引），宁缺勿假，不伪造 found=True 证据。

Production 部署 (T1.1):
- DB pool 注入优先级: 显式 db_pool 参数 > db_url 参数 > DATABASE_URL 环境变量 > mock
- module-level 单例 pool (避免 per-call 创建/销毁)
- 检索失败 rate-limited warning (避免日志洪水)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 灵知 FactVerifier 导入 (旁路)
_LINGZHI_PATH = "/home/ai/lingzhi"
if _LINGZHI_PATH not in sys.path:
    sys.path.insert(0, _LINGZHI_PATH)

try:
    from backend.services.retrieval.fact_verifier import FactVerifier as _LZFactVerifier
    from backend.services.retrieval.kg import KGRetriever as _LZKGRetriever
    _LINGZHI_AVAIL = True
    logger.info("灵知 FactVerifier 可用, 已加载")
except ImportError:
    _LINGZHI_AVAIL = False
    _LZFactVerifier = None  # type: ignore[assignment]
    _LZKGRetriever = None  # type: ignore[assignment]
    logger.info("灵知 FactVerifier 不可用, 使用 mock")


# === Module-level singleton DB pool (production 部署核心) ===
# 2026-09-11: 锁改为 import 期 eager 创建 — lazy 初始化自身也有竞态
# (两线程同时看到 None 各建一把锁, 各自进临界区, double-check 形同虚设)。
_POOL_SINGLETON: Any = None
_POOL_LOOP: Any = None  # pool 绑定的事件循环 (跨 loop 复用检测用)
_POOL_LOCK = threading.Lock()


def _get_pool_lock():
    """获取 module-level threading.Lock 单例 (同步锁, 跨 loop 安全)"""
    return _POOL_LOCK


async def get_db_pool(db_url: str | None = None, min_size: int = 2, max_size: int = 5):
    """获取 module-level asyncpg.Pool 单例

    Args:
        db_url: PostgreSQL DSN. None 则读 DATABASE_URL 环境变量.
        min_size/max_size: 连接池大小.

    Returns:
        asyncpg.Pool 单例 (首次调用创建, 后续复用).
    """
    global _POOL_SINGLETON, _POOL_LOOP
    if _POOL_SINGLETON is not None:
        # 2026-09-11: 跨 loop 复用检测 — 本模块同步包装层每次 new_event_loop,
        # pool 会被绑死在创建时的 loop 上, 跨 loop acquire 时 asyncpg 报错。
        # 显式告警留痕, 重构(pool 代理/专用 loop)另行立项。
        try:
            loop_now = asyncio.get_running_loop()
        except RuntimeError:
            loop_now = None
        if loop_now is not None and _POOL_LOOP is not None and loop_now is not _POOL_LOOP:
            logger.warning(
                "get_db_pool: pool 属于其他事件循环 (%s ≠ %s) — "
                "跨 loop 使用 asyncpg pool 会失败 (已知限制)",
                _POOL_LOOP, loop_now,
            )
        return _POOL_SINGLETON

    import asyncpg

    url = db_url or os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DB pool 未配置: 传 db_url 或设置 DATABASE_URL 环境变量 "
            "(灵知生产环境 DSN 见灵知配置, 切勿硬编码)"
        )

    # 2026-09-11: 非 blocking acquire + yield 替代原地自旋 — 原写法在协程
    # 语境下持同步锁跨 await, 事件循环整个冻结 (同线程其余协程全部停摆)。
    # 现在抢不到锁就让出调度权; 冷启动仅一个创建者, 空转几轮即到临界区。
    lock = _get_pool_lock()
    while not lock.acquire(blocking=False):
        await asyncio.sleep(0)
    try:
        if _POOL_SINGLETON is None:  # double-check
            _POOL_SINGLETON = await asyncpg.create_pool(url, min_size=min_size, max_size=max_size)
            _POOL_LOOP = asyncio.get_running_loop()
            logger.info("FactChecker DB pool 已创建: min=%d max=%d", min_size, max_size)
    finally:
        lock.release()
    return _POOL_SINGLETON


async def close_db_pool() -> None:
    """关闭 module-level pool (测试/进程退出时调用)"""
    global _POOL_SINGLETON
    if _POOL_SINGLETON is not None:
        await _POOL_SINGLETON.close()
        _POOL_SINGLETON = None
        logger.info("FactChecker DB pool 已关闭")


def reset_db_pool() -> None:
    """同步重置 pool 引用 (仅用于测试, 不关闭连接)"""
    global _POOL_SINGLETON
    _POOL_SINGLETON = None


class _LingZhiSyncSearch:
    """同步包装灵知 FactVerifier + KGRetriever

    生产路径:
      FactVerifier.check_claim -> KGRetriever.search -> asyncpg pool

    降级: pool 创建失败/检索异常 -> rate-limited warning -> 调用 mock_fallback
    (P0.3 起 mock_fallback 默认 fail-closed 抛 RuntimeError，不伪造 found=True)
    """

    def __init__(self, pool_provider: Callable[[], Any], mock_fallback: Callable[[str], list[dict]]):
        self._pool_provider = pool_provider
        self._mock_fallback = mock_fallback
        self._last_warn: float = 0.0
        self._kg_retriever: Any = None  # KGRetriever 单例 (pool 就绪后建一次)

    def _ensure_kg(self) -> Any:
        if self._kg_retriever is None:
            pool = self._pool_provider()
            self._kg_retriever = _LZKGRetriever(pool)
        return self._kg_retriever

    def search(self, query: str) -> list[dict]:
        """同步搜索 (生产 entry point)

        Returns:
            [{"id": str, "text": str, "confidence": float, ...}, ...]
        """
        import time

        try:
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None

            if running is not None:
                # 当前线程已有 loop 在跑 (pytest-asyncio / async ctx) —
                # 在独立线程跑新 loop, 避免 "loop is running" 冲突
                return self._run_in_thread(query)
            else:
                return self._run_in_new_loop(query)
        except Exception as e:
            now = time.time()
            if now - self._last_warn > 60:
                logger.warning("灵知 KG 检索失败: %s", e)
                self._last_warn = now
            # 失败回退 mock (不静默, 保证 audit_response 始终有结果)
            return self._mock_fallback(query)

    def _run_in_new_loop(self, query: str) -> list[dict]:
        """在新建 event loop 中跑异步搜索 (无运行 loop 时使用)"""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._asearch(query))
        finally:
            loop.close()

    def _run_in_thread(self, query: str) -> list[dict]:
        """在独立线程跑新 loop (已有 loop 在跑时使用, 例如 pytest-asyncio)"""
        import concurrent.futures

        def _runner() -> list[dict]:
            return self._run_in_new_loop(query)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_runner)
            return future.result(timeout=10.0)

    async def _asearch(self, query: str) -> list[dict]:
        kg = self._ensure_kg()
        results = await kg.search(query, top_k=3)
        # 灵知 KGRetriever 返回 [{id, content, similarity, source_table, ...}]
        # 适配为 fact_checker 期望的 [{id, text, confidence, ...}]
        return [
            {
                "id": r.get("id", ""),
                "text": r.get("content", r.get("text", "")),
                "confidence": r.get("similarity", r.get("confidence", 0.0)),
                "source_table": r.get("source_table", ""),
                "title": r.get("title", ""),
            }
            for r in results
        ]


@dataclass
class Claim:
    """从模型输出中提取的事实性声明"""
    text: str
    entity: str = ""
    span: tuple[int, int] = (0, 0)


@dataclass
class FactCheckResult:
    """事实校验结果"""
    claim: Claim
    found: bool = False           # KG 中是否有来源
    confidence: float = 0.0       # 匹配置信度 [0, 1]
    source: str = ""              # 来源 (灵忆 record ID / 灵知 KG 实体名)
    evidence: str = ""            # 检索到的证据文本
    completeness: float = 1.0     # 来源完整性 [0, 1] — 证据文本是否完整覆盖 claim
    error: str = ""


class ClaimExtractor:
    """从模型输出中提取 claim"""
    
    # 声明关键词模式
    _PATTERNS = [
        r"(?:已|已经)(?:完成|执行|处理|修复|解决|实现)(?:了)?",
        r"(?:使用|采用|调用)(?:了)?\w+",
        r"(?:确认|验证|检查)(?:了)?\w+",
        r"(?:发现|找到|识别)(?:了)?\w+",
        r"(?:不存在|没有|无需)\w*",
        r"\w+(?:搜索|检索|查询)了",
        r"完成(?:了)?\w+",
    ]
    
    @classmethod
    def extract(cls, text: str) -> list[Claim]:
        """提取事实性声明"""
        claims = []
        for pat in cls._PATTERNS:
            for m in re.finditer(pat, text):
                claims.append(Claim(
                    text=m.group(),
                    entity=m.group()[:16],
                    span=(m.start(), m.end()),
                ))
        # 去重
        seen = set()
        unique = []
        for c in claims:
            if c.text not in seen:
                seen.add(c.text)
                unique.append(c)
        return unique


class KGFactChecker:
    """事实校验器 — 通过灵知 KG 检索验证 claim

    优先调灵知 FactVerifier (异步, 22ms p95)。
    不可用时回退到 mock (始终返回 found=True)。

    Args:
        search_fn: 自定义检索函数 (测试/特殊场景).
        db_pool: asyncpg.Pool 实例 (生产推荐 — 复用调用方连接池).
                 不传则尝试用 get_db_pool() 创建 module-level 单例.
        db_url: PostgreSQL DSN (仅在 db_pool 为空且无 DATABASE_URL 时生效).
    """

    def __init__(
        self,
        search_fn: Callable[[str], list[dict]] | None = None,
        db_pool: Any | None = None,
        db_url: str | None = None,
    ):
        self._explicit_pool = db_pool
        self._db_url = db_url
        self._search_fn = search_fn or self._build_default_search()
        self._owns_pool = False  # 是否需要负责关闭

    def _build_default_search(self) -> Callable[[str], list[dict]]:
        """构建默认 search_fn: 优先灵知 (FactVerifier), 回退 mock"""
        if not _LINGZHI_AVAIL:
            return self._mock_search

        return _LingZhiSyncSearch(
            pool_provider=self._get_pool_sync,
            mock_fallback=self._mock_search,
        ).search

    def _get_pool_sync(self):
        """同步获取/创建 DB pool

        优先: 显式传入的 pool > module-level 单例 (从 DATABASE_URL 创建)
        失败: 抛 RuntimeError (由 _sync_search 捕获并回退 mock)
        """
        if self._explicit_pool is not None:
            return self._explicit_pool

        # 尝试从 module-level 单例拿 (在独立线程跑, 避开当前 loop)
        import concurrent.futures

        def _runner():
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(get_db_pool(self._db_url))
            finally:
                loop.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_runner)
            return future.result(timeout=15.0)

    def _mock_search(self, query: str) -> list[dict]:
        """P0.3 fail-closed: 原假阳性 mock 已移除。

        原 mock 始终返回 found=True，让幻觉审计形同虚设（V3 §三 E2）。
        现在显式失败：check() 会把本异常转成 found=False + error，
        调用方（l5_audit）按「事实校验失败」处理，不再伪造证据。
        """
        raise RuntimeError(
            "灵知 KG 不可用（mock 已按 P0.3 fail-closed 移除）。"
            "修复指引: 配置 DATABASE_URL 指向灵忆 KG，或显式注入 db_pool/"
            "search_fn；不要依赖 mock 证据。"
        )

    def check(self, claim: Claim) -> FactCheckResult:
        try:
            results = self._search_fn(claim.text)
            if results and results[0].get("confidence", 0) >= 0.5:
                best = results[0]
                evidence_text = best.get("text", "")
                # 来源完整性: claim 中的关键词在证据中被覆盖的比例
                claim_words = set(re.findall(r'\w{3,}', claim.text))
                ev_words = set(re.findall(r'\w{3,}', evidence_text))
                overlap = claim_words & ev_words
                completeness = len(overlap) / max(len(claim_words), 1) if claim_words else 1.0
                return FactCheckResult(
                    claim=claim,
                    found=True,
                    confidence=best.get("confidence", 1.0),
                    source=best.get("id", ""),
                    evidence=evidence_text,
                    completeness=completeness,
                )
            return FactCheckResult(
                claim=claim,
                found=False,
                confidence=results[0].get("confidence", 0) if results else 0.0,
            )
        except Exception as e:
            return FactCheckResult(claim=claim, error=str(e))


def audit_response(
    output: str,
    checker: KGFactChecker | None = None,
    extractor: type[ClaimExtractor] = ClaimExtractor,
) -> dict[str, Any]:
    """对模型输出做事实校验 (L5 round 2 可调)
    
    Returns:
        {"passed": bool, "claims": list[FactCheckResult], "warning": str}
    """
    checker = checker or KGFactChecker()
    claims = extractor.extract(output)
    results = [checker.check(c) for c in claims]
    failed = [r for r in results if not r.found]
    return {
        "passed": len(failed) == 0,
        "total": len(results),
        "failed": len(failed),
        "claims": [
            {"text": r.claim.text, "found": r.found,
             "confidence": r.confidence, "source": r.source,
             "completeness": r.completeness,
             "evidence": r.evidence[:120] if r.evidence else ""}
            for r in results
        ],
        "warning": f"{len(failed)}/{len(results)} claims 无可信来源" if failed else "",
        "incomplete": [c for c in results if c.found and c.completeness < 0.7],
    }
