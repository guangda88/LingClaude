"""回归测试：2026-09-11 架构审计修复（监督会话 W1/W2/W3/W5）。

对应修复：
- W1 exempt_review marker 原子认领（防并发双跑 → 曾实证 OOM-kill）
- W2 fact_checker pool 锁竞态 + 持锁跨 await 冻结事件循环
- W3 l7_cognitive get_cognitive 裸双检竞态
- W4 config 搜索顺序注释（纯文档，不测）
- W5 token_monitor TARGET_MODEL 常量化
"""
from __future__ import annotations

import asyncio
import inspect
import json
import threading
from pathlib import Path

import pytest


# ───────────────────── W1: exempt_review 认领机制 ─────────────────────


class TestClaimMarker:
    """_claim_marker: O_CREAT|O_EXCL 原子认领。"""

    @pytest.fixture(autouse=True)
    def _import_exempt(self, monkeypatch):
        monkeypatch.syspath_prepend("scripts")
        import exempt_review
        self.er = exempt_review

    def test_first_claim_wins_second_loses(self, tmp_path: Path):
        marker = tmp_path / "abc123_1.json"
        marker.write_text("{}", encoding="utf-8")
        assert self.er._claim_marker(marker) is True
        # 同一 marker 第二次认领必须失败（核心竞态防线）
        assert self.er._claim_marker(marker) is False

    def test_claim_file_isolation(self, tmp_path: Path):
        """claim 文件不匹配 check_stale 的 *.json glob，不干扰终态化扫描。"""
        marker = tmp_path / "abc.json"
        marker.write_text("{}", encoding="utf-8")
        assert self.er._claim_marker(marker) is True
        claim = marker.with_suffix(marker.suffix + ".claim")
        assert claim.exists()
        # check_stale 扫描逻辑等价：glob("*.json")
        scanned = [p.name for p in tmp_path.glob("*.json")]
        assert scanned == ["abc.json"]

    def test_watch_yields_when_claimed(self, tmp_path: Path, monkeypatch):
        """已被并发者认领的 marker：watch() 让位返回 0，绝不重复跑 pytest。"""
        marker = tmp_path / "abc.json"
        marker.write_text(json.dumps({
            "commit": "abc", "log": str(tmp_path / "x.log"),
            "root": str(tmp_path), "ts": 0,
        }), encoding="utf-8")
        assert self.er._claim_marker(marker) is True

        calls = {"n": 0}

        def _must_not_run(*a, **k):
            calls["n"] += 1
            raise AssertionError("已认领 marker 不应再执行 pytest")

        monkeypatch.setattr(self.er, "_run_pytest_with_retry", _must_not_run)
        assert self.er.watch(marker) == 0
        assert calls["n"] == 0


# ───────────────────── W2: fact_checker pool 锁 ─────────────────────


class TestDbPoolLock:
    def test_lock_is_eager_module_level(self):
        """锁必须 import 期就存在 — lazy 初始化自身有竞态（两线程各建锁）。"""
        from lingclaude.core import fact_checker as fc
        assert isinstance(fc._POOL_LOCK, type(threading.Lock()))
        assert fc._get_pool_lock() is fc._POOL_LOCK

    def test_get_db_pool_is_async(self):
        from lingclaude.core.fact_checker import get_db_pool
        assert inspect.iscoroutinefunction(get_db_pool)

    def test_concurrent_create_single_pool(self, monkeypatch):
        """并发冷启动：一个 loop 内 8 协程同抢，只创建 1 个 pool。

        原实现持同步锁跨 await 会冻结事件循环（gather 永不返回）；
        现实现非阻塞 acquire + yield，能正常并发且单例唯一。
        """
        import asyncpg
        from lingclaude.core import fact_checker as fc

        created: list[object] = []
        sentinel = object()

        async def fake_create_pool(*a, **k):
            await asyncio.sleep(0.01)  # 放大窗口
            created.append(sentinel)
            return sentinel

        monkeypatch.setattr(asyncpg, "create_pool", fake_create_pool)
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:test@localhost/x")
        monkeypatch.setattr(fc, "_POOL_SINGLETON", None)
        monkeypatch.setattr(fc, "_POOL_LOOP", None)

        async def main():
            pools = await asyncio.gather(*[fc.get_db_pool() for _ in range(8)])
            return pools

        pools = asyncio.run(main())
        assert all(p is sentinel for p in pools)
        assert len(created) == 1, f"pool 被创建了 {len(created)} 次（竞态未修复）"

    def test_reset_pool(self):
        from lingclaude.core.fact_checker import reset_db_pool
        reset_db_pool()  # 不抛异常即可


# ───────────────────── W3: l7_cognitive 单例锁 ─────────────────────


class TestGetCognitive:
    def test_concurrent_init_single_instance(self, monkeypatch):
        """并发首调：只构造一次 L7Cognitive（原裸双检会构造多个）。"""
        from lingclaude.core import l7_cognitive as l7
        # 2026-09-14 灵元减薄：get_cognitive 迁至 l7_cognitive_facade，
        # monkeypatch 需打到 facade 模块命名空间（l7 仅 re-export）。
        from lingclaude.core import l7_cognitive_facade as facade

        created: list[object] = []

        class FakeCognitive:
            def __init__(self, *a, **k):
                import time
                time.sleep(0.01)  # 放大竞态窗口
                created.append(self)

        monkeypatch.setattr(facade, "L7Cognitive", FakeCognitive)
        monkeypatch.setattr(facade, "_global", None)
        # 保持 l7 模块的 get_cognitive 指向 facade 的实现（re-export 同一函数对象）
        monkeypatch.setattr(l7, "get_cognitive", facade.get_cognitive)

        N = 4  # 共享沙箱线程配额有限(实证 8 连失败), 4 已足以构成竞态窗口
        barrier = threading.Barrier(N)
        results: list[object] = []

        def worker():
            barrier.wait(timeout=10)  # 超时自救, 防线程永久悬挂
            results.append(l7.get_cognitive())

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(N)]
        started: list[threading.Thread] = []
        try:
            for t in threads:
                t.start()
                started.append(t)
        except RuntimeError:
            # 连 4 线程都起不来 = 沙箱配额耗尽, 非逻辑问题
            for t in started:
                t.join(timeout=15)
            pytest.skip("沙箱线程配额不足, 无法构造并发窗口")
        for t in started:
            t.join(timeout=15)

        assert len(created) == 1, f"L7Cognitive 被构造了 {len(created)} 次（竞态未修复）"
        assert len(results) == N
        assert all(r is results[0] for r in results)

    def test_returns_same_instance(self):
        from lingclaude.core.l7_cognitive import get_cognitive
        assert get_cognitive() is get_cognitive()


# ───────────────────── W5: token_monitor TARGET_MODEL ─────────────────────


class TestTargetModel:
    def test_target_model_constant_exists(self):
        from lingclaude.core.token_monitor import TARGET_MODEL
        assert isinstance(TARGET_MODEL, str) and TARGET_MODEL

    def test_ratio_computed_from_target_model(self, tmp_path: Path):
        """glm_4_7_ratio 必须按 TARGET_MODEL 键聚合（原硬编码 "GLM-4.7"）。"""
        from lingclaude.core.token_monitor import TokenMonitor, TARGET_MODEL

        m = TokenMonitor(db_path=tmp_path / "t.db")
        m.record_usage(model=TARGET_MODEL, task_type="code_generation",
                       total_tokens=100, input_tokens=40, output_tokens=60)
        m.record_usage(model="other-model", task_type="analysis",
                       total_tokens=100, input_tokens=40, output_tokens=60)

        em = m.get_efficiency_metrics()
        assert em.glm_4_7_ratio == pytest.approx(0.5)

    def test_markdown_report_refers_target_model(self, tmp_path: Path):
        from lingclaude.core.token_monitor import TokenMonitor, TARGET_MODEL

        m = TokenMonitor(db_path=tmp_path / "t.db")
        m.record_usage(model=TARGET_MODEL, task_type="code_generation",
                       total_tokens=1000, input_tokens=400, output_tokens=600)
        out = tmp_path / "report.md"  # 显式落 tmp, 默认路径指向只读 ~/.lingclaude
        m.generate_markdown_report(output_path=out)
        assert TARGET_MODEL in out.read_text(encoding="utf-8")
