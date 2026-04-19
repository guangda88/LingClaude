"""Context Cache for File Read Optimization

功能：
- 缓存文件内容，减少重复读取
- 智能过期策略
- 上下文复用
- 预期节省：25% tokens
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CacheEntry:
    """缓存条目"""
    file_path: str
    file_hash: str
    content: str
    read_count: int = 0
    first_read_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_read_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class CacheStats:
    """缓存统计"""
    total_reads: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    total_files_cached: int = 0
    hit_rate: float = 0.0
    tokens_saved: int = 0  # 估算节省的 tokens

    def update_hit_rate(self) -> "CacheStats":
        """更新命中率"""
        if self.total_reads == 0:
            hit_rate = 0.0
        else:
            hit_rate = self.cache_hits / self.total_reads

        return CacheStats(
            total_reads=self.total_reads,
            cache_hits=self.cache_hits,
            cache_misses=self.cache_misses,
            total_files_cached=self.total_files_cached,
            hit_rate=hit_rate,
            tokens_saved=self.tokens_saved,
        )


class ContextCache:
    """上下文缓存"""

    def __init__(
        self,
        cache_size: int = 100,
        ttl_hours: int = 24,
        db_path: str | Path | None = None,
    ):
        """初始化缓存

        Args:
            cache_size: 最大缓存文件数
            ttl_hours: 缓存过期时间（小时）
            db_path: 数据库路径
        """
        self.cache_size = cache_size
        self.ttl_hours = ttl_hours

        if db_path is None:
            db_path = Path.home() / ".lingclaude" / "context_cache.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

        # 内存缓存（LRU）
        self._memory_cache: OrderedDict[str, CacheEntry] = OrderedDict()

    def _init_db(self) -> None:
        """初始化数据库"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cache_entries (
                file_path TEXT PRIMARY KEY,
                file_hash TEXT NOT NULL,
                content TEXT NOT NULL,
                read_count INTEGER NOT NULL DEFAULT 1,
                first_read_at TEXT NOT NULL,
                last_read_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_last_read_at
            ON cache_entries(last_read_at)
        """)

        conn.commit()
        conn.close()

    def _compute_hash(self, content: str) -> str:
        """计算文件内容的哈希值

        Args:
            content: 文件内容

        Returns:
            MD5 哈希值
        """
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    def _is_expired(self, entry: CacheEntry) -> bool:
        """检查缓存是否过期

        Args:
            entry: 缓存条目

        Returns:
            是否过期
        """
        last_read = datetime.fromisoformat(entry.last_read_at)
        now = datetime.now(timezone.utc)
        return (now - last_read) > timedelta(hours=self.ttl_hours)

    def read_file(
        self,
        file_path: str,
        force_refresh: bool = False,
    ) -> tuple[str, bool]:
        """读取文件（带缓存）

        Args:
            file_path: 文件路径
            force_refresh: 是否强制刷新

        Returns:
            (文件内容, 是否命中缓存)
        """
        file_path = str(file_path)

        # 检查内存缓存
        if not force_refresh and file_path in self._memory_cache:
            entry = self._memory_cache[file_path]

            if not self._is_expired(entry):
                # 命中内存缓存
                self._update_read_count(file_path)
                self._memory_cache.move_to_end(file_path)  # LRU
                return entry.content, True

        # 检查磁盘缓存
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        if not force_refresh:
            cursor.execute("""
                SELECT file_hash, content, read_count, first_read_at, last_read_at
                FROM cache_entries
                WHERE file_path = ?
            """, (file_path,))
            row = cursor.fetchone()

            if row:
                entry = CacheEntry(
                    file_path=file_path,
                    file_hash=row[0],
                    content=row[1],
                    read_count=row[2],
                    first_read_at=row[3],
                    last_read_at=row[4],
                )

                if not self._is_expired(entry):
                    # 命中磁盘缓存
                    self._update_read_count(file_path)
                    self._memory_cache[file_path] = entry
                    self._memory_cache.move_to_end(file_path)
                    conn.close()
                    return entry.content, True

        # 缓存未命中，读取文件
        try:
            content = Path(file_path).read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, UnicodeDecodeError) as e:
            conn.close()
            raise FileNotFoundError(f"无法读取文件 {file_path}: {e}")

        file_hash = self._compute_hash(content)

        # 更新或创建缓存条目
        now = datetime.now(timezone.utc).isoformat()

        cursor.execute("""
            INSERT OR REPLACE INTO cache_entries
            (file_path, file_hash, content, read_count, first_read_at, last_read_at)
            VALUES (?, ?, ?, COALESCE((SELECT read_count FROM cache_entries WHERE file_path = ?), 0) + 1,
                    COALESCE((SELECT first_read_at FROM cache_entries WHERE file_path = ?), ?), ?)
        """, (file_path, file_hash, content, file_path, file_path, now, now))

        conn.commit()
        conn.close()

        # 更新内存缓存
        entry = CacheEntry(
            file_path=file_path,
            file_hash=file_hash,
            content=content,
            read_count=1,
            first_read_at=now,
            last_read_at=now,
        )

        # LRU 管理
        if len(self._memory_cache) >= self.cache_size:
            self._memory_cache.popitem(last=False)
        self._memory_cache[file_path] = entry

        return content, False

    def _update_read_count(self, file_path: str) -> None:
        """更新读取计数

        Args:
            file_path: 文件路径
        """
        now = datetime.now(timezone.utc).isoformat()

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE cache_entries
            SET read_count = read_count + 1, last_read_at = ?
            WHERE file_path = ?
        """, (now, file_path))

        conn.commit()
        conn.close()

    def invalidate(self, file_path: str | None = None) -> None:
        """使缓存失效

        Args:
            file_path: 文件路径，None 表示清空所有缓存
        """
        if file_path is None:
            # 清空所有缓存
            self._memory_cache.clear()

            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            cursor.execute("DELETE FROM cache_entries")
            conn.commit()
            conn.close()
        else:
            file_path = str(file_path)

            # 移除指定文件的缓存
            if file_path in self._memory_cache:
                del self._memory_cache[file_path]

            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            cursor.execute("DELETE FROM cache_entries WHERE file_path = ?", (file_path,))
            conn.commit()
            conn.close()

    def cleanup_expired(self) -> int:
        """清理过期缓存

        Returns:
            清理的缓存条目数
        """
        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=self.ttl_hours)).isoformat()

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute("""
            SELECT file_path FROM cache_entries WHERE last_read_at < ?
        """, (cutoff_time,))
        expired_files = [row[0] for row in cursor.fetchall()]

        cursor.execute("""
            DELETE FROM cache_entries WHERE last_read_at < ?
        """, (cutoff_time,))

        conn.commit()
        conn.close()

        # 从内存缓存中移除
        for file_path in expired_files:
            if file_path in self._memory_cache:
                del self._memory_cache[file_path]

        return len(expired_files)

    def get_stats(self) -> CacheStats:
        """获取缓存统计

        Returns:
            缓存统计
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # 读取计数
        cursor.execute("SELECT SUM(read_count) FROM cache_entries")
        total_reads = cursor.fetchone()[0] or 0

        # 缓存文件数
        cursor.execute("SELECT COUNT(*) FROM cache_entries")
        total_files_cached = cursor.fetchone()[0] or 0

        # 估算：缓存命中数 = 总读取数 - 缓存文件数（每个文件至少读取一次）
        cache_misses = total_files_cached
        cache_hits = total_reads - cache_misses

        # 估算节省的 tokens（假设平均每次文件读取节省 1000 tokens）
        tokens_saved = cache_hits * 1000

        conn.close()

        stats = CacheStats(
            total_reads=total_reads,
            cache_hits=cache_hits,
            cache_misses=cache_misses,
            total_files_cached=total_files_cached,
            hit_rate=0.0,
            tokens_saved=tokens_saved,
        )

        return stats.update_hit_rate()

    def get_top_files(self, limit: int = 10) -> list[tuple[str, int]]:
        """获取最常读取的文件

        Args:
            limit: 返回数量限制

        Returns:
            [(文件路径, 读取次数), ...]
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute("""
            SELECT file_path, read_count
            FROM cache_entries
            ORDER BY read_count DESC
            LIMIT ?
        """, (limit,))

        result = [(row[0], row[1]) for row in cursor.fetchall()]
        conn.close()

        return result


def main():
    """主函数：测试上下文缓存"""
    print("=" * 80)
    print("💾 上下文缓存测试")
    print("=" * 80)

    # 创建缓存
    cache = ContextCache(cache_size=50, ttl_hours=24)

    # 测试文件
    test_file = Path("/home/ai/LingClaude/README.md")

    print("\n📖 第一次读取（缓存未命中）...")
    content, hit = cache.read_file(str(test_file))
    print(f"  文件长度: {len(content)} 字符")
    print(f"  缓存命中: {hit}")
    print(f"  内存缓存大小: {len(cache._memory_cache)}")

    print("\n📖 第二次读取（缓存命中）...")
    content2, hit2 = cache.read_file(str(test_file))
    print(f"  文件长度: {len(content2)} 字符")
    print(f"  缓存命中: {hit2}")
    print(f"  内容一致: {content == content2}")

    print("\n📖 第三次读取（缓存命中）...")
    content3, hit3 = cache.read_file(str(test_file))
    print(f"  缓存命中: {hit3}")

    # 统计
    print("\n" + "=" * 80)
    print("📊 缓存统计")
    print("=" * 80)
    stats = cache.get_stats()
    print(f"  总读取次数: {stats.total_reads}")
    print(f"  缓存命中: {stats.cache_hits}")
    print(f"  缓存未命中: {stats.cache_misses}")
    print(f"  命中率: {stats.hit_rate * 100:.1f}%")
    print(f"  缓存文件数: {stats.total_files_cached}")
    print(f"  估算节省 tokens: {stats.tokens_saved:,}")

    # 常用文件
    print("\n📋 常用文件:")
    for file_path, read_count in cache.get_top_files(5):
        print(f"  {file_path}: {read_count} 次")

    print("\n" + "=" * 80)
    print("✅ 缓存测试完成！")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
