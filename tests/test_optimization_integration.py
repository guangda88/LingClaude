"""Integration tests for GLM token optimization components."""
from __future__ import annotations

import tempfile
from pathlib import Path

from lingclaude.core.query_engine import QueryEngine
from lingclaude.model.intelligent_router import IntelligentRouter
from lingclaude.core.context_cache import ContextCache
from lingclaude.core.token_monitor import TokenMonitor


class TestOptimizationIntegration:
    """Test that optimization components are properly integrated into QueryEngine."""

    def test_query_engine_initializes_optimizations(self):
        """Verify QueryEngine initializes all optimization components."""
        engine = QueryEngine()

        assert hasattr(engine, "_router"), "Router not initialized"
        assert hasattr(engine, "_cache"), "Cache not initialized"
        assert hasattr(engine, "_monitor"), "Monitor not initialized"

    def test_optimization_components_are_correct_types(self):
        """Verify optimization components are correct types."""
        engine = QueryEngine()

        assert isinstance(engine._router, IntelligentRouter)
        assert isinstance(engine._cache, ContextCache)
        assert isinstance(engine._monitor, TokenMonitor)

    def test_router_selects_models_correctly(self):
        """Verify router selects GLM-4.7 for simple tasks."""
        engine = QueryEngine()

        # Test simple code task
        decision = engine._router.route("写一个hello world函数")
        assert decision.model.value == "GLM-4.7", f"Expected GLM-4.7, got {decision.model.value}"

        # Test simple analysis task
        decision = engine._router.route("分析这段代码")
        assert decision.model.value == "GLM-4.7", f"Expected GLM-4.7, got {decision.model.value}"

    def test_cache_can_store_and_retrieve_files(self):
        """Verify cache can store and retrieve file contents."""
        engine = QueryEngine()

        # Create a test file
        test_file = Path("/tmp/test_cache_file.txt")
        test_file.write_text("Test content", encoding="utf-8")

        # Read file (may be cached or not)
        content1, hit1 = engine._cache.read_file(str(test_file))
        assert content1 == "Test content", "Content mismatch"

        # Invalidate cache to ensure clean state
        engine._cache.invalidate(str(test_file))

        # First read (cache miss after invalidation)
        content2, hit2 = engine._cache.read_file(str(test_file))
        assert not hit2, "First read should be cache miss"
        assert content2 == "Test content", "Content mismatch"

        # Second read (cache hit)
        content3, hit3 = engine._cache.read_file(str(test_file))
        assert hit3, "Second read should be cache hit"
        assert content3 == "Test content", "Content mismatch"

        # Cleanup
        test_file.unlink()

    def test_monitor_can_record_token_usage(self):
        """Verify monitor can record token usage."""
        # P2.c seam 实战：注入隔离 monitor（独立 SQLite），消除全量并发
        # 与真实 db ~/.lingclaude/token_monitor.db 的共享状态竞态
        engine = QueryEngine(wiring_overrides={
            "_monitor": TokenMonitor(db_path=Path(tempfile.mkdtemp()) / "monitor.db"),
        })
        engine._monitor.record_usage(
            model="GLM_4_7",
            task_type="code_generation",
            total_tokens=1000,
            input_tokens=600,
            output_tokens=400,
        )

        # Verify usage increased
        stats_after = engine._monitor.get_daily_stats()
        assert stats_after.total_tokens == 1000, "Token count should be 1000"
        assert stats_after.input_tokens == 600, "Input tokens should be 600"
        assert stats_after.output_tokens == 400, "Output tokens should be 400"

    def test_integration_workflow(self):
        """Test end-to-end workflow with all optimizations."""
        # P2.c seam：隔离 monitor db，消除与真实 ~/.lingclaude/token_monitor.db
        # 的并发竞态（长时 run -i 进程实时写该库，全量跑时 delta 断言被污染）
        engine = QueryEngine(wiring_overrides={
            "_monitor": TokenMonitor(db_path=Path(tempfile.mkdtemp()) / "monitor.db"),
        })

        # Get initial stats
        stats_before = engine._monitor.get_daily_stats()

        # 1. Router selects model
        decision = engine._router.route("写一个排序函数")
        assert decision.model.value == "GLM-4.7", "Should use GLM-4.7 for simple task"

        # 2. Monitor tracks usage（D5 2026-10-09：原第2步 aggregator 记录段随死码拆除）
        engine._monitor.record_usage(
            model=str(decision.model.value),
            task_type=str(decision.task_type.value),
            total_tokens=1500,
            input_tokens=900,
            output_tokens=600,
        )

        # Verify monitor tracked usage
        stats_after = engine._monitor.get_daily_stats()
        assert stats_after.total_tokens == stats_before.total_tokens + 1500, "Monitor should track usage"



class TestOptimizationConfiguration:
    """Test optimization component configuration."""

    def test_router_can_be_configured(self):
        """Verify router can be configured with custom settings."""
        router = IntelligentRouter()
        decision = router.route("写一个函数")
        assert decision.model.value == "GLM-4.7", "Default should be GLM-4.7"

    def test_cache_can_be_configured(self):
        """Verify cache can be configured with custom settings."""
        cache = ContextCache(cache_size=50, ttl_hours=12)
        assert cache.cache_size == 50, "Cache size should be 50"
        assert cache.ttl_hours == 12, "TTL should be 12 hours"

    def test_monitor_creates_database(self):
        """Verify monitor creates database on initialization."""
        monitor = TokenMonitor()  # noqa: F841
        # Database should be created at ~/.lingclaude/token_monitor.db
        db_path = Path.home() / ".lingclaude" / "token_monitor.db"
        assert db_path.exists(), "Database should be created"

class TestOptimizationDataflow:
    """Test data flow between optimization components."""

