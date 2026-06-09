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
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, TypeAlias

from lingclaude.core.safe_db import safe_commit, safe_connect

logger = logging.getLogger(__name__)

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

    def __init__(self, db_path: str | Path | None = None):
        """初始化监控器

        Args:
            db_path: SQLite 数据库路径，默认为 ~/.lingclaude/token_monitor.db
        """
        if db_path is None:
            db_path = Path.home() / ".lingclaude" / "token_monitor.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

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

        # GLM-4.7 使用率
        glm_4_7_tokens = stats.model_distribution.get("GLM-4.7", 0)
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

    def generate_html_report(self, output_path: str | Path | None = None) -> str:
        """生成 HTML 报告

        Args:
            output_path: 输出路径，默认为 ~/.lingclaude/reports/token_report.html

        Returns:
            报告路径
        """
        if output_path is None:
            output_path = Path.home() / ".lingclaude" / "reports" / "token_report.html"

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 获取数据
        stats = self.get_daily_stats()
        metrics = self.get_efficiency_metrics()

        # 生成 HTML
        html = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GLM Token 使用报告 - {stats.date}</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        h1 {{
            color: #333;
            border-bottom: 2px solid #4CAF50;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #555;
            margin-top: 30px;
        }}
        .metrics {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin: 20px 0;
        }}
        .metric-card {{
            background: white;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .metric-value {{
            font-size: 2em;
            font-weight: bold;
            color: #4CAF50;
        }}
        .metric-label {{
            color: #666;
            margin-top: 5px;
        }}
        .chart {{
            background: white;
            border-radius: 8px;
            padding: 20px;
            margin: 20px 0;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 12px;
            text-align: left;
        }}
        th {{
            background-color: #4CAF50;
            color: white;
        }}
        tr:nth-child(even) {{
            background-color: #f2f2f2;
        }}
        .status-good {{
            color: #4CAF50;
            font-weight: bold;
        }}
        .status-warning {{
            color: #ff9800;
            font-weight: bold;
        }}
        .status-bad {{
            color: #f44336;
            font-weight: bold;
        }}
        .progress-bar {{
            width: 100%;
            height: 20px;
            background-color: #ddd;
            border-radius: 10px;
            overflow: hidden;
        }}
        .progress-fill {{
            height: 100%;
            background-color: #4CAF50;
            transition: width 0.3s;
        }}
    </style>
</head>
<body>
    <h1>📊 GLM Token 使用报告</h1>
    <p><strong>日期：</strong>{stats.date}</p>
    <p><strong>生成时间：</strong>{datetime.now(timezone.utc).isoformat()}</p>

    <h2>📈 核心指标</h2>
    <div class="metrics">
        <div class="metric-card">
            <div class="metric-value">{stats.total_tokens:,}</div>
            <div class="metric-label">总 Token 数</div>
        </div>
        <div class="metric-card">
            <div class="metric-value">{stats.prompt_count:,}</div>
            <div class="metric-label">Prompt 数量</div>
        </div>
        <div class="metric-card">
            <div class="metric-value">{metrics.token_efficiency:,.0f}</div>
            <div class="metric-label">Token/Prompt</div>
        </div>
        <div class="metric-card">
            <div class="metric-value">{metrics.avg_input_tokens:,}</div>
            <div class="metric-label">平均输入 Token</div>
        </div>
    </div>

    <h2>🤖 模型分布</h2>
    <div class="chart">
        <table>
            <tr>
                <th>模型</th>
                <th>Token 数</th>
                <th>占比</th>
                <th>状态</th>
            </tr>
"""

        for model, tokens in sorted(stats.model_distribution.items(), key=lambda x: x[1], reverse=True):
            ratio = tokens / stats.total_tokens if stats.total_tokens > 0 else 0
            status = "status-good" if model == "GLM-4.7" else "status-warning"
            html += f"""
            <tr>
                <td>{model}</td>
                <td>{tokens:,}</td>
                <td>
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: {ratio * 100}%"></div>
                    </div>
                    {ratio * 100:.1f}%
                </td>
                <td class="{status}">✓</td>
            </tr>
"""

        html += """
        </table>
    </div>

    <h2>📋 任务类型分布</h2>
    <div class="chart">
        <table>
            <tr>
                <th>任务类型</th>
                <th>Token 数</th>
                <th>占比</th>
            </tr>
"""

        for task_type, tokens in sorted(stats.task_distribution.items(), key=lambda x: x[1], reverse=True):
            ratio = tokens / stats.total_tokens if stats.total_tokens > 0 else 0
            html += f"""
            <tr>
                <td>{task_type}</td>
                <td>{tokens:,}</td>
                <td>{ratio * 100:.1f}%</td>
            </tr>
"""

        html += """
        </table>
    </div>

    <h2>⚡ 效率指标</h2>
    <div class="metrics">
        <div class="metric-card">
            <div class="metric-value" style="font-size: 1.5em;">{metrics.glm_4_7_ratio * 100:.1f}%</div>
            <div class="metric-label">GLM-4.7 使用率</div>
            <div style="margin-top: 5px; color: {'#4CAF50' if metrics.glm_4_7_ratio >= 0.8 else '#ff9800'}">
                {'✓ 目标达成' if metrics.glm_4_7_ratio >= 0.8 else '⚠️ 需优化'}
            </div>
        </div>
        <div class="metric-card">
            <div class="metric-value" style="font-size: 1.5em;">{metrics.duplicate_read_ratio * 100:.1f}%</div>
            <div class="metric-label">重复读取率</div>
            <div style="margin-top: 5px; color: {'#4CAF50' if metrics.duplicate_read_ratio <= 0.15 else '#ff9800'}">
                {'✓ 状态良好' if metrics.duplicate_read_ratio <= 0.15 else '⚠️ 需优化'}
            </div>
        </div>
        <div class="metric-card">
            <div class="metric-value" style="font-size: 1.5em;">{stats.duplicate_reads:,}</div>
            <div class="metric-label">重复读取次数</div>
        </div>
    </div>

    <h2>💡 优化建议</h2>
    <div class="chart">
"""

        # 生成优化建议
        suggestions = []

        if metrics.glm_4_7_ratio < 0.8:
            suggestions.append(f"• GLM-4.7 使用率仅 {metrics.glm_4_7_ratio * 100:.1f}%，建议提升到 80% 以上")

        if metrics.duplicate_read_ratio > 0.15:
            suggestions.append(f"• 重复读取率 {metrics.duplicate_read_ratio * 100:.1f}% 较高，建议实施上下文缓存")

        if metrics.token_efficiency > 100000:
            suggestions.append(f"• 平均 Token/Prompt {metrics.token_efficiency:,.0f} 较高，检查是否有超长上下文")

        if not suggestions:
            suggestions.append("✓ 当前指标良好，继续保持！")

        html += "\n".join(f"<p>{s}</p>" for s in suggestions)

        html += """
    </div>

    <h2>📅 最近 7 天趋势</h2>
    <div class="chart">
        <table>
            <tr>
                <th>日期</th>
                <th>总 Token</th>
                <th>Prompt 数</th>
                <th>GLM-4.7 使用率</th>
                <th>效率评分</th>
            </tr>
"""

        # 获取最近 7 天数据
        conn = safe_connect(self.db_path)
        cursor = conn.cursor()

        for i in range(6, -1, -1):
            date = (datetime.now(timezone.utc) - timedelta(days=i)).date().isoformat()
            cursor.execute("""
                SELECT SUM(total_tokens), SUM(prompt_count)
                FROM usage_records
                WHERE DATE(timestamp) = ?
            """, (date,))
            result = cursor.fetchone()
            if result[0]:
                cursor.execute("""
                    SELECT SUM(total_tokens)
                    FROM usage_records
                    WHERE DATE(timestamp) = ? AND model = 'GLM-4.7'
                """, (date,))
                glm_4_7 = cursor.fetchone()[0] or 0
                ratio = glm_4_7 / result[0] if result[0] > 0 else 0

                efficiency_score = (ratio * 50) + (50 if result[1] > 0 else 0)

                html += f"""
            <tr>
                <td>{date}</td>
                <td>{result[0]:,}</td>
                <td>{result[1]:,}</td>
                <td>{ratio * 100:.1f}%</td>
                <td>{efficiency_score:.1f}</td>
            </tr>
"""

        conn.close()

        html += """
        </table>
    </div>

    <footer>
        <p style="text-align: center; color: #666; margin-top: 50px;">
            生成时间：{datetime.now(timezone.utc).isoformat()} | <a href="https://github.com/lingclaude/lingclaude">lingclaude Project</a>
        </p>
    </footer>
</body>
</html>
"""

        output_path.write_text(html, encoding="utf-8")

        return str(output_path)

    def generate_markdown_report(self, output_path: str | Path | None = None) -> str:
        """生成 Markdown 报告

        Args:
            output_path: 输出路径，默认为 ~/.lingclaude/reports/token_report.md

        Returns:
            报告路径
        """
        if output_path is None:
            output_path = Path.home() / ".lingclaude" / "reports" / "token_report.md"

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # 获取数据
        stats = self.get_daily_stats()
        metrics = self.get_efficiency_metrics()

        # 生成 Markdown
        markdown = f"""# GLM Token 使用报告

**日期**：{stats.date}
**生成时间**：{datetime.now(timezone.utc).isoformat()}

---

## 📊 核心指标

| 指标 | 数值 |
|------|------|
| **总 Token 数** | {stats.total_tokens:,} |
| **输入 Token 数** | {stats.input_tokens:,} |
| **输出 Token 数** | {stats.output_tokens:,} |
| **Prompt 数量** | {stats.prompt_count:,} |
| **平均 Token/Prompt** | {metrics.token_efficiency:,.0f} |
| **平均输入 Token** | {metrics.avg_input_tokens:,} |
| **平均输出 Token** | {metrics.avg_output_tokens:,} |

---

## 🤖 模型分布

| 模型 | Token 数 | 占比 | 状态 |
|------|----------|------|------|
"""

        for model, tokens in sorted(stats.model_distribution.items(), key=lambda x: x[1], reverse=True):
            ratio = tokens / stats.total_tokens if stats.total_tokens > 0 else 0
            status = "✓" if model == "GLM-4.7" else "⚠️"
            markdown += f"| {model} | {tokens:,} | {ratio * 100:.1f}% | {status} |\n"

        markdown += f"""

**GLM-4.7 使用率**：{metrics.glm_4_7_ratio * 100:.1f}%
- {'✓ 目标达成（≥80%）' if metrics.glm_4_7_ratio >= 0.8 else '⚠️ 需优化（目标：≥80%）'}

---

## 📋 任务类型分布

| 任务类型 | Token 数 | 占比 |
|----------|----------|------|
"""

        for task_type, tokens in sorted(stats.task_distribution.items(), key=lambda x: x[1], reverse=True):
            ratio = tokens / stats.total_tokens if stats.total_tokens > 0 else 0
            markdown += f"| {task_type} | {tokens:,} | {ratio * 100:.1f}% |\n"

        markdown += f"""

---

## ⚡ 效率指标

| 指标 | 数值 | 状态 |
|------|------|------|
| **GLM-4.7 使用率** | {metrics.glm_4_7_ratio * 100:.1f}% | {'✓ 良好' if metrics.glm_4_7_ratio >= 0.8 else '⚠️ 需优化'} |
| **重复读取率** | {metrics.duplicate_read_ratio * 100:.1f}% | {'✓ 良好（≤15%）' if metrics.duplicate_read_ratio <= 0.15 else '⚠️ 需优化（目标：≤15%）'} |
| **重复读取次数** | {stats.duplicate_reads:,} | - |

---

## 💡 优化建议

"""

        # 生成优化建议
        suggestions = []

        if metrics.glm_4_7_ratio < 0.8:
            suggestions.append(f"- [ ] GLM-4.7 使用率仅 {metrics.glm_4_7_ratio * 100:.1f}%，建议提升到 80% 以上")

        if metrics.duplicate_read_ratio > 0.15:
            suggestions.append(f"- [ ] 重复读取率 {metrics.duplicate_read_ratio * 100:.1f}% 较高，建议实施上下文缓存")

        if metrics.token_efficiency > 100000:
            suggestions.append(f"- [ ] 平均 Token/Prompt {metrics.token_efficiency:,.0f} 较高，检查是否有超长上下文")

        if not suggestions:
            suggestions.append("✓ 当前指标良好，继续保持！")

        markdown += "\n".join(suggestions)

        markdown += """

---

## 📅 最近 7 天趋势

| 日期 | 总 Token | Prompt 数 | GLM-4.7 使用率 | 效率评分 |
|------|----------|-----------|----------------|----------|
"""

        # 获取最近 7 天数据
        conn = safe_connect(self.db_path)
        cursor = conn.cursor()

        for i in range(6, -1, -1):
            date = (datetime.now(timezone.utc) - timedelta(days=i)).date().isoformat()
            cursor.execute("""
                SELECT SUM(total_tokens), SUM(prompt_count)
                FROM usage_records
                WHERE DATE(timestamp) = ?
            """, (date,))
            result = cursor.fetchone()
            if result[0]:
                cursor.execute("""
                    SELECT SUM(total_tokens)
                    FROM usage_records
                    WHERE DATE(timestamp) = ? AND model = 'GLM-4.7'
                """, (date,))
                glm_4_7 = cursor.fetchone()[0] or 0
                ratio = glm_4_7 / result[0] if result[0] > 0 else 0

                efficiency_score = (ratio * 50) + (50 if result[1] > 0 else 0)

                markdown += f"| {date} | {result[0]:,} | {result[1]:,} | {ratio * 100:.1f}% | {efficiency_score:.1f} |\n"

        conn.close()

        markdown += f"""

---

**生成时间**：{datetime.now(timezone.utc).isoformat()}
**生成工具**：lingclaude Token Monitor
"""

        output_path.write_text(markdown, encoding="utf-8")

        return str(output_path)


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
        model="GLM-4.7",
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
        model="GLM-4.7",
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
    print(f"  GLM-4.7 使用率：{metrics.glm_4_7_ratio * 100:.1f}%")
    print(f"  重复读取率：{metrics.duplicate_read_ratio * 100:.1f}%")

    print("\n" + "=" * 80)
    print("✅ 监控报告生成完成！")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(main())
