#!/usr/bin/env python3
"""Token Usage Monitoring Dashboard

功能：
- 收集 token 使用数据
- 生成可视化报告（HTML/Markdown）
- 每日自动生成报告
- 监控模型选择比例
- 识别重复读取
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, TypeAlias

from lingclaude.core.safe_db import safe_commit, safe_connect

logger = logging.getLogger(__name__)

# 目标「优选模型」— 建议/评分/趋势 SQL 的过滤名。
# 2026-09-11: 原 19 处硬编码 "GLM-4.7" 收敛到此常量(config.yaml 生产模型
# 已是 glm-5.3-flash, 旧报告的"提升 GLM-4.7 到 80%"建议已误导)。
# W5+1: 默认值动态跟进 config.yaml 生产模型, LINGCLAUDE_TARGET_MODEL env 可覆盖
# (换模型/供应商时无需改代码); 匹配处用 casefold 大小写不敏感, 兼容库内既有键形态。
# 注意: 历史库数据中旧模型名的聚合口径会随本值迁移, 属预期语义(「当前目标模型」)。
# 2026-09-11 晚: 默认值改从 config.yaml 读取(此前注释说"跟进"但实为静态写死,
# 换 deepseek-v4-flash 后报告会再次误导)。fail-soft: config 不可读时退静态兜底。


def _default_target_model() -> str:
    try:
        from lingclaude.core.config import find_config_path, load_config

        path = find_config_path()
        if path:
            name = getattr(load_config(path).model, "model", "") or ""
            if name:
                return name
    except Exception:  # noqa: BLE001 — config 损坏/只读环境不阻断 token_monitor
        pass
    return "glm-5.3-flash"


TARGET_MODEL = os.environ.get("LINGCLAUDE_TARGET_MODEL") or _default_target_model()


def _default_report_path(name: str) -> Path:
    """默认报告路径带可写性回退 — ~/.lingclaude/reports/ 不可写时(只读根 FS,
    V3 沙箱常态)退到系统临时目录, 报告生成不因存储只读而崩(2026-09-11 实测)。"""
    primary = Path.home() / ".lingclaude" / "reports" / name
    try:
        primary.parent.mkdir(parents=True, exist_ok=True)
        probe = primary.parent / ".probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return primary
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "lingclaude_reports"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback / name

# 数据类型
UsageRecord: TypeAlias = dict[str, Any]


@dataclass(frozen=True)
class TokenMetrics:
    """Token 使用指标"""
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    prompt_count: int = 0
    model: str = ""
    task_type: str = ""
    timestamp: str = ""

    @classmethod
    def from_dict(cls, data: UsageRecord) -> "TokenMetrics":
        """从字典创建指标"""
        return cls(
            total_tokens=data.get("total_tokens", 0),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            prompt_count=data.get("prompt_count", 1),
            model=data.get("model", "unknown"),
            task_type=data.get("task_type", "unknown"),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )


@dataclass(frozen=True)
class DailyStats:
    """每日统计"""
    date: str
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    prompt_count: int = 0
    model_distribution: dict[str, int] = field(default_factory=dict)
    task_distribution: dict[str, int] = field(default_factory=dict)
    duplicate_reads: int = 0


@dataclass(frozen=True)
class EfficiencyMetrics:
    """效率指标"""
    date: str
    token_efficiency: float = 0.0  # tokens/prompt
    avg_input_tokens: int = 0
    avg_output_tokens: int = 0
    glm_4_7_ratio: float = 0.0  # GLM-4.7 使用率
    duplicate_read_ratio: float = 0.0  # 重复读取率


class TokenMonitor:
    """Token 使用监控器"""

    def __init__(self, db_path: str | Path | None = None,
                 legacy_sink: Any | None = None):
        """初始化监控器

        Args:
            db_path: SQLite 数据库路径，默认为 ~/.lingclaude/token_monitor.db
            legacy_sink: P3.4 灵忆双写旁观者（LingMemoryTokenSink，可选）。
                主路权威不变；sink 故障只降级 warning，不影响本路。
        """
        if db_path is None:
            db_path = Path.home() / ".lingclaude" / "token_monitor.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._legacy_sink = legacy_sink
        # P3.4 自指卫兵（thread-local）：重入=同调用栈概念，跨线程并发不是重入
        self._emit_tls = threading.local()

        # 文件读取缓存（检测重复读取）
        self._file_cache: dict[str, tuple[str, str]] = {}  # path -> (content, last_read_time)
        self._read_count: dict[str, int] = defaultdict(int)

    def _init_db(self) -> None:
        """初始化数据库"""
        conn = safe_connect(self.db_path)
        cursor = conn.cursor()

        # 创建使用记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usage_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model TEXT NOT NULL,
                task_type TEXT NOT NULL,
                total_tokens INTEGER NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                prompt_count INTEGER NOT NULL,
                metadata TEXT,
                UNIQUE(timestamp, model, task_type)
            )
        """)

        # 创建文件读取记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_reads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                read_count INTEGER NOT NULL,
                UNIQUE(timestamp, file_path)
            )
        """)

        safe_commit(conn)
        conn.close()

    def record_usage(
        self,
        model: str,
        task_type: str,
        total_tokens: int,
        input_tokens: int,
        output_tokens: int,
        prompt_count: int = 1,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """记录 token 使用

        Args:
            model: 模型名称（GLM-4.7, GLM-5.1, etc.）
            task_type: 任务类型（code_generation, analysis, search, etc.）
            total_tokens: 总 token 数
            input_tokens: 输入 token 数
            output_tokens: 输出 token 数
            prompt_count: 提示词数量
            metadata: 额外元数据
        """
        conn = safe_connect(self.db_path)
        cursor = conn.cursor()

        timestamp = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)

        cursor.execute("""
            INSERT OR REPLACE INTO usage_records
            (timestamp, model, task_type, total_tokens, input_tokens, output_tokens, prompt_count, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (timestamp, model, task_type, total_tokens, input_tokens, output_tokens, prompt_count, metadata_json))

        safe_commit(conn)
        conn.close()
        self._last_model = model
        self._last_task_type = task_type
        self._last_input_tokens = input_tokens
        self._last_output_tokens = output_tokens
        self._last_total_tokens = total_tokens
        self._last_metadata = metadata
        self._emit_legacy_sink()

    def _emit_legacy_sink(self) -> None:
        """P3.4 旁路镜像（best-effort）：永不抛、永不递归、永不影响主路。

        逐字段调用 on_usage（主路权威结构，桥侧自行判定缺省）；
        任何异常吞掉只留 warning —— 可观测组件不得破坏主流程（库纪律）。
        """
        sink = getattr(self, "_legacy_sink", None)
        if sink is None or getattr(self._emit_tls, "on", False):
            return
        self._emit_tls.on = True
        try:
            # 单次完整 dict：三个 token 字段在 record_usage 签名层必填，
            # 逐字段 emit 无真实场景且让自定义 sink 收到 3 次回调
            sink.on_usage({
                "model": self._last_model,
                "task_type": self._last_task_type,
                "input_tokens": self._last_input_tokens,
                "output_tokens": self._last_output_tokens,
                "total_tokens": self._last_total_tokens,
                "metadata": self._last_metadata,
            })
        except Exception as e:  # noqa: BLE001 — 旁路纪律，见 _emit_legacy_sink
            logging.getLogger(__name__).warning(
                "[token_monitor] legacy_sink 镜像失败（已忽略）: %s", e)
        finally:
            self._emit_tls.on = False

    def record_file_read(self, file_path: str, file_content: str) -> bool:
        """记录文件读取

        Args:
            file_path: 文件路径
            file_content: 文件内容

        Returns:
            是否重复读取
        """
        import hashlib

        file_hash = hashlib.md5(file_content.encode(), usedforsecurity=False).hexdigest()
        is_duplicate = False

        conn = safe_connect(self.db_path)
        cursor = conn.cursor()

        timestamp = datetime.now(timezone.utc).isoformat()

        # 检查是否重复
        cursor.execute("""
            SELECT COUNT(*) FROM file_reads
            WHERE file_path = ? AND file_hash = ? AND timestamp > ?
        """, (file_path, file_hash, (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()))

        if cursor.fetchone()[0] > 0:
            is_duplicate = True

        # 记录读取
        cursor.execute("""
            INSERT OR REPLACE INTO file_reads
            (timestamp, file_path, file_hash, read_count)
            VALUES (?, ?, ?, COALESCE((SELECT read_count FROM file_reads WHERE file_path = ?), 0) + 1)
        """, (timestamp, file_path, file_hash, file_path))

        safe_commit(conn)
        conn.close()

        return is_duplicate

    def get_daily_stats(self, date: str | None = None) -> DailyStats:
        """获取每日统计

        Args:
            date: 日期（YYYY-MM-DD），默认为今天

        Returns:
            每日统计
        """
        if date is None:
            date = datetime.now(timezone.utc).date().isoformat()

        conn = safe_connect(self.db_path)
        cursor = conn.cursor()

        # 总计
        cursor.execute("""
            SELECT SUM(total_tokens), SUM(input_tokens), SUM(output_tokens), SUM(prompt_count)
            FROM usage_records
            WHERE DATE(timestamp) = ?
        """, (date,))
        result = cursor.fetchone()
        total_tokens = result[0] or 0
        input_tokens = result[1] or 0
        output_tokens = result[2] or 0
        prompt_count = result[3] or 0

        # 模型分布
        cursor.execute("""
            SELECT model, SUM(total_tokens)
            FROM usage_records
            WHERE DATE(timestamp) = ?
            GROUP BY model
        """, (date,))
        model_distribution = {row[0]: row[1] for row in cursor.fetchall()}

        # 任务类型分布
        cursor.execute("""
            SELECT task_type, SUM(total_tokens)
            FROM usage_records
            WHERE DATE(timestamp) = ?
            GROUP BY task_type
        """, (date,))
        task_distribution = {row[0]: row[1] for row in cursor.fetchall()}

        # 重复读取
        cursor.execute("""
            SELECT COUNT(*)
            FROM file_reads
            WHERE DATE(timestamp) = ? AND read_count > 1
        """, (date,))
        duplicate_reads = cursor.fetchone()[0] or 0

        conn.close()

        return DailyStats(
            date=date,
            total_tokens=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt_count=prompt_count,
            model_distribution=model_distribution,
            task_distribution=task_distribution,
            duplicate_reads=duplicate_reads,
        )

    def get_efficiency_metrics(self, date: str | None = None) -> EfficiencyMetrics:
        """获取效率指标

        Args:
            date: 日期（YYYY-MM-DD），默认为今天

        Returns:
            效率指标
        """
        stats = self.get_daily_stats(date)

        # Token 效率（tokens/prompt）
        token_efficiency = stats.total_tokens / stats.prompt_count if stats.prompt_count > 0 else 0.0

        # 平均输入/输出 tokens
        avg_input_tokens = stats.input_tokens / stats.prompt_count if stats.prompt_count > 0 else 0
        avg_output_tokens = stats.output_tokens / stats.prompt_count if stats.prompt_count > 0 else 0

        # 目标模型使用率（casefold: 库内历史键可能为任意大小写形态）
        glm_4_7_tokens = sum(
            tokens for name, tokens in stats.model_distribution.items()
            if name.casefold() == TARGET_MODEL.casefold()
        )
        glm_4_7_ratio = glm_4_7_tokens / stats.total_tokens if stats.total_tokens > 0 else 0.0

        # 重复读取率
        duplicate_read_ratio = stats.duplicate_reads / stats.prompt_count if stats.prompt_count > 0 else 0.0

        return EfficiencyMetrics(
            date=stats.date,
            token_efficiency=token_efficiency,
            avg_input_tokens=int(avg_input_tokens),
            avg_output_tokens=int(avg_output_tokens),
            glm_4_7_ratio=glm_4_7_ratio,
            duplicate_read_ratio=duplicate_read_ratio,
        )

    # ── 报告生成（灵元 1.0：剥离为纯函数，本方法仅薄委托）────────────
    # 2026-09-14: 原 460 行报告逻辑迁至 token_reports.py（独立职责，可独立测试）。
    def generate_html_report(self, output_path: str | Path | None = None) -> str:
        """生成 HTML 报告（委托 token_reports.generate_html_report）。"""
        from lingclaude.core.token_reports import generate_html_report as _gen

        return _gen(
            stats=self.get_daily_stats(),
            metrics=self.get_efficiency_metrics(),
            db_path=self.db_path,
            output_path=output_path,
        )

    def generate_markdown_report(self, output_path: str | Path | None = None) -> str:
        """生成 Markdown 报告（委托 token_reports.generate_markdown_report）。"""
        from lingclaude.core.token_reports import generate_markdown_report as _gen

        return _gen(
            stats=self.get_daily_stats(),
            metrics=self.get_efficiency_metrics(),
            db_path=self.db_path,
            output_path=output_path,
        )


def main():
    """主函数：生成报告"""

    print("=" * 80)
    print("📊 GLM Token 使用监控")
    print("=" * 80)

    # 创建监控器
    monitor = TokenMonitor()

    # 添加示例数据（仅用于演示）
    print("\n📝 添加示例数据...")
    monitor.record_usage(
        model=TARGET_MODEL,
        task_type="code_generation",
        total_tokens=15000,
        input_tokens=5000,
        output_tokens=10000,
    )
    monitor.record_usage(
        model="GLM-5.1",
        task_type="analysis",
        total_tokens=25000,
        input_tokens=10000,
        output_tokens=15000,
    )
    monitor.record_usage(
        model=TARGET_MODEL,
        task_type="search",
        total_tokens=8000,
        input_tokens=3000,
        output_tokens=5000,
    )

    # 记录文件读取
    monitor.record_file_read("/path/to/file1.py", "content1")
    monitor.record_file_read("/path/to/file1.py", "content1")  # 重复
    monitor.record_file_read("/path/to/file2.py", "content2")

    # 生成报告
    print("\n📈 生成 HTML 报告...")
    html_path = monitor.generate_html_report()
    print(f"✓ HTML 报告：{html_path}")

    print("\n📝 生成 Markdown 报告...")
    md_path = monitor.generate_markdown_report()
    print(f"✓ Markdown 报告：{md_path}")

    # 显示统计
    print("\n📊 今日统计：")
    stats = monitor.get_daily_stats()
    metrics = monitor.get_efficiency_metrics()

    print(f"  总 Token 数：{stats.total_tokens:,}")
    print(f"  Prompt 数量：{stats.prompt_count:,}")
    print(f"  {TARGET_MODEL} 使用率：{metrics.glm_4_7_ratio * 100:.1f}%")
    print(f"  重复读取率：{metrics.duplicate_read_ratio * 100:.1f}%")

    print("\n" + "=" * 80)
    print("✅ 监控报告生成完成！")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
