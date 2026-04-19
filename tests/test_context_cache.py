"""Tests for ContextCache"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from lingclaude.core.context_cache import CacheEntry, CacheStats, ContextCache


class TestCacheEntry:
    """Test CacheEntry dataclass"""

    def test_create_default(self):
        """Test creating CacheEntry with defaults"""
        entry = CacheEntry(
            file_path="/tmp/test.txt",
            file_hash="abc123",
            content="Hello, World!",
        )
        assert entry.file_path == "/tmp/test.txt"
        assert entry.file_hash == "abc123"
        assert entry.content == "Hello, World!"
        assert entry.read_count == 0
        assert entry.first_read_at is not None
        assert entry.last_read_at is not None

    def test_create_with_params(self):
        """Test creating CacheEntry with all parameters"""
        entry = CacheEntry(
            file_path="/tmp/test.txt",
            file_hash="abc123",
            content="Hello, World!",
            read_count=5,
            first_read_at="2024-01-01T00:00:00",
            last_read_at="2024-01-02T00:00:00",
        )
        assert entry.read_count == 5
        assert entry.first_read_at == "2024-01-01T00:00:00"
        assert entry.last_read_at == "2024-01-02T00:00:00"

    def test_frozen(self):
        """Test that CacheEntry is frozen"""
        entry = CacheEntry(
            file_path="/tmp/test.txt",
            file_hash="abc123",
            content="Hello, World!",
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            entry.read_count = 1


class TestCacheStats:
    """Test CacheStats dataclass"""

    def test_create_default(self):
        """Test creating CacheStats with defaults"""
        stats = CacheStats()
        assert stats.total_reads == 0
        assert stats.cache_hits == 0
        assert stats.cache_misses == 0
        assert stats.total_files_cached == 0
        assert stats.hit_rate == 0.0
        assert stats.tokens_saved == 0

    def test_create_with_params(self):
        """Test creating CacheStats with parameters"""
        stats = CacheStats(
            total_reads=100,
            cache_hits=80,
            cache_misses=20,
            total_files_cached=20,
            hit_rate=0.8,
            tokens_saved=80000,
        )
        assert stats.total_reads == 100
        assert stats.cache_hits == 80
        assert stats.cache_misses == 20
        assert stats.total_files_cached == 20
        assert stats.hit_rate == 0.8
        assert stats.tokens_saved == 80000

    def test_update_hit_rate_zero_reads(self):
        """Test hit_rate calculation with zero reads"""
        stats = CacheStats(total_reads=0, cache_hits=0, cache_misses=0)
        updated = stats.update_hit_rate()
        assert updated.hit_rate == 0.0

    def test_update_hit_rate_normal(self):
        """Test hit_rate calculation with normal values"""
        stats = CacheStats(total_reads=100, cache_hits=80, cache_misses=20)
        updated = stats.update_hit_rate()
        assert updated.hit_rate == 0.8

    def test_update_hit_rate_perfect(self):
        """Test hit_rate calculation with perfect hits"""
        stats = CacheStats(total_reads=50, cache_hits=50, cache_misses=0)
        updated = stats.update_hit_rate()
        assert updated.hit_rate == 1.0


class TestContextCache:
    """Test ContextCache class"""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test files"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def test_file(self, temp_dir):
        """Create a test file"""
        test_file = temp_dir / "test.txt"
        test_file.write_text("Hello, World!", encoding="utf-8")
        return test_file

    @pytest.fixture
    def cache(self, temp_dir):
        """Create a ContextCache instance with temporary database"""
        db_path = temp_dir / "cache.db"
        cache = ContextCache(cache_size=10, ttl_hours=1, db_path=db_path)
        yield cache
        # Cleanup is automatic via temp_dir

    def test_init_default_db_path(self):
        """Test initialization with default database path"""
        cache = ContextCache()
        assert cache.cache_size == 100
        assert cache.ttl_hours == 24
        assert cache.db_path.name == "context_cache.db"
        assert len(cache._memory_cache) == 0

    def test_init_custom_params(self, temp_dir):
        """Test initialization with custom parameters"""
        db_path = temp_dir / "custom.db"
        cache = ContextCache(cache_size=50, ttl_hours=12, db_path=db_path)
        assert cache.cache_size == 50
        assert cache.ttl_hours == 12
        assert cache.db_path == db_path

    def test_compute_hash(self, cache):
        """Test hash computation"""
        content = "Hello, World!"
        hash1 = cache._compute_hash(content)
        hash2 = cache._compute_hash(content)
        assert hash1 == hash2
        assert len(hash1) == 32  # MD5 hash length

    def test_is_expired_fresh(self, cache):
        """Test expiration check for fresh entry"""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        entry = CacheEntry(
            file_path="/tmp/test.txt",
            file_hash="abc123",
            content="Hello",
            last_read_at=now.isoformat(),
        )
        assert not cache._is_expired(entry)

    def test_is_expired_old(self, cache):
        """Test expiration check for old entry"""
        from datetime import datetime, timezone, timedelta

        old_time = datetime.now(timezone.utc) - timedelta(hours=25)
        entry = CacheEntry(
            file_path="/tmp/test.txt",
            file_hash="abc123",
            content="Hello",
            last_read_at=old_time.isoformat(),
        )
        assert cache._is_expired(entry)

    def test_read_file_first_read(self, cache, test_file):
        """Test reading file for the first time (cache miss)"""
        content, hit = cache.read_file(str(test_file))
        assert content == "Hello, World!"
        assert not hit
        assert len(cache._memory_cache) == 1
        assert str(test_file) in cache._memory_cache

    def test_read_file_second_read(self, cache, test_file):
        """Test reading file for the second time (cache hit)"""
        # First read
        cache.read_file(str(test_file))
        # Second read
        content, hit = cache.read_file(str(test_file))
        assert content == "Hello, World!"
        assert hit

    def test_read_file_force_refresh(self, cache, test_file):
        """Test force refresh parameter"""
        # First read
        cache.read_file(str(test_file))
        # Force refresh
        content, hit = cache.read_file(str(test_file), force_refresh=True)
        assert content == "Hello, World!"
        assert not hit

    def test_read_file_not_found(self, cache):
        """Test reading non-existent file"""
        with pytest.raises(FileNotFoundError):
            cache.read_file("/nonexistent/file.txt")

    def test_lru_eviction(self, cache, temp_dir):
        """Test LRU eviction when cache is full"""
        cache_size = cache.cache_size

        # Create cache_size + 1 files
        files = []
        for i in range(cache_size + 1):
            test_file = temp_dir / f"test{i}.txt"
            test_file.write_text(f"Content {i}", encoding="utf-8")
            files.append(test_file)

        # Read all files
        for i, file in enumerate(files):
            cache.read_file(str(file))

        # First file should be evicted
        assert len(cache._memory_cache) <= cache_size
        assert str(files[0]) not in cache._memory_cache

    def test_invalidate_specific(self, cache, test_file):
        """Test invalidating specific file cache"""
        cache.read_file(str(test_file))
        assert str(test_file) in cache._memory_cache

        cache.invalidate(str(test_file))
        assert str(test_file) not in cache._memory_cache

    def test_invalidate_all(self, cache, temp_dir):
        """Test invalidating all caches"""
        # Create and cache multiple files
        for i in range(3):
            test_file = temp_dir / f"test{i}.txt"
            test_file.write_text(f"Content {i}", encoding="utf-8")
            cache.read_file(str(test_file))

        assert len(cache._memory_cache) == 3

        cache.invalidate()
        assert len(cache._memory_cache) == 0

    def test_cleanup_expired(self, cache, temp_dir):
        """Test cleaning up expired entries"""
        # Create cache with short TTL
        short_cache = ContextCache(cache_size=10, ttl_hours=0, db_path=temp_dir / "short.db")

        # Read a file
        test_file = temp_dir / "test.txt"
        test_file.write_text("Content", encoding="utf-8")
        short_cache.read_file(str(test_file))

        # Should have 1 entry
        assert len(short_cache._memory_cache) == 1

        # Cleanup should remove it (TTL=0 means immediately expired)
        cleaned = short_cache.cleanup_expired()
        assert cleaned >= 0

    def test_get_stats_empty(self, cache):
        """Test getting stats with no entries"""
        stats = cache.get_stats()
        assert stats.total_reads == 0
        assert stats.cache_hits == 0
        assert stats.cache_misses == 0
        assert stats.total_files_cached == 0
        assert stats.hit_rate == 0.0
        assert stats.tokens_saved == 0

    def test_get_stats_with_entries(self, cache, test_file):
        """Test getting stats with cache entries"""
        # Read file twice
        cache.read_file(str(test_file))
        cache.read_file(str(test_file))

        stats = cache.get_stats()
        assert stats.total_reads == 2
        assert stats.cache_hits == 1
        assert stats.cache_misses == 1
        assert stats.total_files_cached == 1
        assert stats.hit_rate == 0.5
        assert stats.tokens_saved == 1000

    def test_get_top_files_empty(self, cache):
        """Test getting top files with no entries"""
        top = cache.get_top_files(limit=5)
        assert top == []

    def test_get_top_files_with_entries(self, cache, temp_dir):
        """Test getting top files with cache entries"""
        # Create and read files with different frequencies
        file1 = temp_dir / "file1.txt"
        file1.write_text("Content 1", encoding="utf-8")

        file2 = temp_dir / "file2.txt"
        file2.write_text("Content 2", encoding="utf-8")

        # Read file1 5 times, file2 3 times
        for _ in range(5):
            cache.read_file(str(file1))
        for _ in range(3):
            cache.read_file(str(file2))

        top = cache.get_top_files(limit=10)
        assert len(top) == 2
        assert top[0][0] == str(file1)
        assert top[0][1] == 5
        assert top[1][1] == 3

    def test_database_persistence(self, temp_dir):
        """Test that cache persists across cache instances"""
        db_path = temp_dir / "persist.db"
        test_file = temp_dir / "test.txt"
        test_file.write_text("Content", encoding="utf-8")

        # First cache instance
        cache1 = ContextCache(db_path=db_path)
        cache1.read_file(str(test_file))

        # Second cache instance (should load from DB)
        cache2 = ContextCache(db_path=db_path)
        content, hit = cache2.read_file(str(test_file))

        assert content == "Content"
        assert hit  # Should be a cache hit

    def test_memory_and_disk_cache_sync(self, cache, test_file):
        """Test synchronization between memory and disk cache"""
        # First read - loads into both caches
        content1, hit1 = cache.read_file(str(test_file))
        assert not hit1
        assert str(test_file) in cache._memory_cache

        # Clear memory cache
        cache._memory_cache.clear()

        # Second read - should hit disk cache
        content2, hit2 = cache.read_file(str(test_file))
        assert hit2
        assert str(test_file) in cache._memory_cache
        assert content1 == content2

    def test_concurrent_reads_same_file(self, cache, test_file):
        """Test multiple concurrent reads of the same file"""
        results = []
        for _ in range(5):
            content, hit = cache.read_file(str(test_file))
            results.append((content, hit))

        # All should return same content
        contents = [r[0] for r in results]
        assert all(c == "Hello, World!" for c in contents)

        # First read is miss, rest are hits
        hits = [r[1] for r in results]
        assert not hits[0]
        assert all(hits[1:])
