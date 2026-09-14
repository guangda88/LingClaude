"""Token 报告生成（纯函数，灵元 1.0：砍到最薄 —— 从 TokenMonitor 剥离的独立职责）。

2026-09-14: 由 lingclaude/core/token_monitor.py 的 generate_html_report /
generate_markdown_report 方法迁移而来（原 460 行，占 token_monitor 一半）。
迁移为模块级纯函数：入参 (stats, metrics, db_path, output_path)，
不持有 TokenMonitor 实例状态 —— 报告生成与监控器解耦，可独立测试。

用法:
    from lingclaude.core.token_reports import generate_html_report
    path = generate_html_report(stats, metrics, db_path)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from lingclaude.core.safe_db import safe_connect
from lingclaude.core.token_monitor import TARGET_MODEL, _default_report_path


from typing import Any


def generate_html_report(
    stats: Any,
    metrics: Any,
    db_path: str | Path,
    output_path: str | Path | None = None,
) -> str:
    """生成 HTML 报告

    Args:
        output_path: 输出路径，默认为 ~/.lingclaude/reports/token_report.html

    Returns:
        报告路径
    """
    if output_path is None:
        output_path = _default_report_path("token_report.html")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 获取数据
    # stats/metrics 由调用方注入（纯函数）
    # （stats/metrics 由调用方注入）

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
        status = "status-good" if model.casefold() == TARGET_MODEL.casefold() else "status-warning"
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
        <div class="metric-label">{TARGET_MODEL} 使用率</div>
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
        suggestions.append(f"• {TARGET_MODEL} 使用率仅 {metrics.glm_4_7_ratio * 100:.1f}%，建议提升到 80% 以上")

    if metrics.duplicate_read_ratio > 0.15:
        suggestions.append(f"• 重复读取率 {metrics.duplicate_read_ratio * 100:.1f}% 较高，建议实施上下文缓存")

    if metrics.token_efficiency > 100000:
        suggestions.append(f"• 平均 Token/Prompt {metrics.token_efficiency:,.0f} 较高，检查是否有超长上下文")

    if not suggestions:
        suggestions.append("✓ 当前指标良好，继续保持！")

    html += "\n".join(f"<p>{s}</p>" for s in suggestions)

    html += f"""
</div>

<h2>📅 最近 7 天趋势</h2>
<div class="chart">
    <table>
        <tr>
            <th>日期</th>
            <th>总 Token</th>
            <th>Prompt 数</th>
            <th>{TARGET_MODEL} 使用率</th>
            <th>效率评分</th>
        </tr>
"""

    # 获取最近 7 天数据
    conn = safe_connect(db_path)
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
                WHERE DATE(timestamp) = ? AND LOWER(model) = LOWER(?)
            """, (date, TARGET_MODEL))
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



def generate_markdown_report(
    stats: Any,
    metrics: Any,
    db_path: str | Path,
    output_path: str | Path | None = None,
) -> str:
    """生成 Markdown 报告

    Args:
        output_path: 输出路径，默认为 ~/.lingclaude/reports/token_report.md

    Returns:
        报告路径
    """
    if output_path is None:
        output_path = _default_report_path("token_report.md")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 获取数据
    # stats/metrics 由调用方注入（纯函数）
    # （stats/metrics 由调用方注入）

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
        status = "✓" if model.casefold() == TARGET_MODEL.casefold() else "⚠️"
        markdown += f"| {model} | {tokens:,} | {ratio * 100:.1f}% | {status} |\n"

    markdown += f"""

**{TARGET_MODEL} 使用率**：{metrics.glm_4_7_ratio * 100:.1f}%
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
| **{TARGET_MODEL} 使用率** | {metrics.glm_4_7_ratio * 100:.1f}% | {'✓ 良好' if metrics.glm_4_7_ratio >= 0.8 else '⚠️ 需优化'} |
| **重复读取率** | {metrics.duplicate_read_ratio * 100:.1f}% | {'✓ 良好（≤15%）' if metrics.duplicate_read_ratio <= 0.15 else '⚠️ 需优化（目标：≤15%）'} |
| **重复读取次数** | {stats.duplicate_reads:,} | - |

---

## 💡 优化建议

"""

    # 生成优化建议
    suggestions = []

    if metrics.glm_4_7_ratio < 0.8:
        suggestions.append(f"- [ ] {TARGET_MODEL} 使用率仅 {metrics.glm_4_7_ratio * 100:.1f}%，建议提升到 80% 以上")

    if metrics.duplicate_read_ratio > 0.15:
        suggestions.append(f"- [ ] 重复读取率 {metrics.duplicate_read_ratio * 100:.1f}% 较高，建议实施上下文缓存")

    if metrics.token_efficiency > 100000:
        suggestions.append(f"- [ ] 平均 Token/Prompt {metrics.token_efficiency:,.0f} 较高，检查是否有超长上下文")

    if not suggestions:
        suggestions.append("✓ 当前指标良好，继续保持！")

    markdown += "\n".join(suggestions)

    markdown += f"""

---

## 📅 最近 7 天趋势

| 日期 | 总 Token | Prompt 数 | {TARGET_MODEL} 使用率 | 效率评分 |
|------|----------|-----------|----------------|----------|
"""

    # 获取最近 7 天数据
    conn = safe_connect(db_path)
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
                WHERE DATE(timestamp) = ? AND LOWER(model) = LOWER(?)
            """, (date, TARGET_MODEL))
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


