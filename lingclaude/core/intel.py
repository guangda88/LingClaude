"""灵克情报系统 — Intelligence collection, daily digest, and relay to 灵依.

Gathers intel from behavior metrics, code patterns, errors, optimizations,
and project structure changes. Aggregates into daily digests and relays
to configured targets (e.g., 灵依).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from lingclaude.core.types import Result


class IntelCategory(str, Enum):
    FILE_CHANGE = "file_change"
    CODE_PATTERN = "code_pattern"
    BEHAVIOR = "behavior"
    ERROR = "error"
    OPTIMIZATION = "optimization"
    STRUCTURE = "structure"
    QUALITY = "quality"
    SECURITY = "security"


class IntelPriority(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class IntelItem:
    category: IntelCategory
    priority: IntelPriority
    source: str
    content: str
    timestamp: str
    metadata: tuple[tuple[str, str], ...] = ()

    @classmethod
    def create(
        cls,
        *,
        category: IntelCategory,
        priority: IntelPriority,
        source: str,
        content: str,
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> IntelItem:
        return cls(
            category=category,
            priority=priority,
            source=source,
            content=content,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "priority": self.priority.value,
            "source": self.source,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": {k: v for k, v in self.metadata},
        }


@dataclass(frozen=True)
class DailyDigest:
    report_date: str
    items: tuple[IntelItem, ...]
    summary: str
    key_findings: tuple[str, ...]
    recommendations: tuple[str, ...]
    category_counts: tuple[tuple[str, int], ...]
    priority_counts: tuple[tuple[str, int], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_date": self.report_date,
            "total_items": len(self.items),
            "summary": self.summary,
            "key_findings": list(self.key_findings),
            "recommendations": list(self.recommendations),
            "category_counts": {k: v for k, v in self.category_counts},
            "priority_counts": {k: v for k, v in self.priority_counts},
            "items": [item.to_dict() for item in self.items],
        }

    def to_markdown(self) -> str:
        lines: list[str] = [
            f"# 灵克情报日报 — {self.report_date}",
            "",
            f"共收集 {len(self.items)} 条情报。",
            "",
            "## 概要",
            "",
            self.summary,
            "",
        ]
        if self.key_findings:
            lines.append("## 关键发现")
            lines.append("")
            for finding in self.key_findings:
                lines.append(f"- {finding}")
            lines.append("")

        if self.recommendations:
            lines.append("## 建议")
            lines.append("")
            for rec in self.recommendations:
                lines.append(f"- {rec}")
            lines.append("")

        lines.append("## 分类统计")
        lines.append("")
        for cat, count in self.category_counts:
            lines.append(f"- **{cat}**: {count}")
        lines.append("")

        lines.append("## 优先级分布")
        lines.append("")
        for pri, count in self.priority_counts:
            lines.append(f"- **{pri}**: {count}")
        lines.append("")

        if self.items:
            lines.append("## 情报明细")
            lines.append("")
            for item in self.items:
                icon = {"info": "ℹ️", "warning": "⚠️", "critical": "🔴"}.get(
                    item.priority.value, "ℹ️"
                )
                lines.append(
                    f"### {icon} [{item.category.value}] {item.source}"
                )
                lines.append("")
                lines.append(item.content)
                lines.append("")
                if item.metadata:
                    for k, v in item.metadata:
                        lines.append(f"- {k}: {v}")
                    lines.append("")

        return "\n".join(lines)


@dataclass
class IntelCollector:
    items: list[IntelItem] = field(default_factory=list)

    def from_behavior(
        self,
        metrics: dict[str, Any],
    ) -> tuple[IntelItem, ...]:
        new_items: list[IntelItem] = []

        hallucination_risk = metrics.get("hallucination_risk", 0.0)
        if hallucination_risk > 0.3:
            new_items.append(IntelItem.create(
                category=IntelCategory.BEHAVIOR,
                priority=IntelPriority.WARNING,
                source="behavior_tracker",
                content=f"幻觉风险偏高: {hallucination_risk:.1%}，存在未读代码就回答问题的倾向",
                metadata=(
                    ("hallucination_risk", f"{hallucination_risk:.3f}"),
                    ("total_turns", str(metrics.get("total_turns", 0))),
                ),
            ))

        frustration_rate = metrics.get("frustration_rate", 0.0)
        if frustration_rate > 0.2:
            new_items.append(IntelItem.create(
                category=IntelCategory.BEHAVIOR,
                priority=IntelPriority.WARNING,
                source="behavior_tracker",
                content=f"用户挫败率偏高: {frustration_rate:.1%}，可能需要调整交互策略",
                metadata=(
                    ("frustration_rate", f"{frustration_rate:.3f}"),
                    ("frustration_count", str(metrics.get("frustration_count", 0))),
                ),
            ))

        tool_error_rate = metrics.get("tool_error_rate", 0.0)
        if tool_error_rate > 0.3:
            new_items.append(IntelItem.create(
                category=IntelCategory.ERROR,
                priority=IntelPriority.WARNING,
                source="behavior_tracker",
                content=f"工具错误率偏高: {tool_error_rate:.1%}，检查工具参数和路径",
                metadata=(
                    ("tool_error_rate", f"{tool_error_rate:.3f}"),
                    ("tool_error_count", str(metrics.get("tool_error_count", 0))),
                ),
            ))

        tool_use_rate = metrics.get("tool_use_rate", 0.0)
        if tool_use_rate < 0.2 and metrics.get("total_turns", 0) > 3:
            new_items.append(IntelItem.create(
                category=IntelCategory.BEHAVIOR,
                priority=IntelPriority.INFO,
                source="behavior_tracker",
                content=f"工具使用率偏低: {tool_use_rate:.1%}，可能需要更主动使用工具",
                metadata=(
                    ("tool_use_rate", f"{tool_use_rate:.3f}"),
                    ("total_turns", str(metrics.get("total_turns", 0))),
                ),
            ))

        corrections = metrics.get("corrections_received", 0)
        if corrections >= 2:
            new_items.append(IntelItem.create(
                category=IntelCategory.BEHAVIOR,
                priority=IntelPriority.CRITICAL,
                source="behavior_tracker",
                content=f"收到 {corrections} 次纠正，回答质量需要关注",
                metadata=(
                    ("corrections_received", str(corrections)),
                ),
            ))

        self.items.extend(new_items)
        return tuple(new_items)

    def from_file_change(
        self,
        path: str,
        change_type: str,
        *,
        lines_added: int = 0,
        lines_removed: int = 0,
    ) -> IntelItem:
        priority = IntelPriority.INFO
        if change_type in ("deleted", "security_sensitive"):
            priority = IntelPriority.WARNING

        item = IntelItem.create(
            category=IntelCategory.FILE_CHANGE,
            priority=priority,
            source="file_watcher",
            content=f"文件 {change_type}: {path}",
            metadata=(
                ("change_type", change_type),
                ("path", path),
                ("lines_added", str(lines_added)),
                ("lines_removed", str(lines_removed)),
            ),
        )
        self.items.append(item)
        return item

    def from_pattern(
        self,
        pattern_name: str,
        file_path: str,
        description: str,
        *,
        severity: str = "info",
    ) -> IntelItem:
        priority = IntelPriority.WARNING if severity in ("warning", "critical") else IntelPriority.INFO
        category = IntelCategory.SECURITY if "secret" in pattern_name.lower() else IntelCategory.CODE_PATTERN

        item = IntelItem.create(
            category=category,
            priority=priority,
            source="pattern_recognizer",
            content=f"{pattern_name}: {description} (in {file_path})",
            metadata=(
                ("pattern_name", pattern_name),
                ("file_path", file_path),
            ),
        )
        self.items.append(item)
        return item

    def from_error(
        self,
        error_type: str,
        message: str,
        *,
        tool_name: str = "",
        context: str = "",
    ) -> IntelItem:
        item = IntelItem.create(
            category=IntelCategory.ERROR,
            priority=IntelPriority.WARNING,
            source="error_tracker",
            content=f"{error_type}: {message}",
            metadata=(
                ("error_type", error_type),
                ("tool_name", tool_name),
                ("context", context),
            ),
        )
        self.items.append(item)
        return item

    def from_optimization(
        self,
        result_summary: str,
        *,
        improvement_score: float = 0.0,
        violations_before: int = 0,
        violations_after: int = 0,
    ) -> IntelItem:
        priority = IntelPriority.INFO if improvement_score > 0 else IntelPriority.WARNING
        item = IntelItem.create(
            category=IntelCategory.OPTIMIZATION,
            priority=priority,
            source="optimization_daemon",
            content=f"自优化周期完成: {result_summary}",
            metadata=(
                ("improvement_score", f"{improvement_score:.3f}"),
                ("violations_before", str(violations_before)),
                ("violations_after", str(violations_after)),
            ),
        )
        self.items.append(item)
        return item

    def from_structure(
        self,
        total_files: int,
        total_packages: int,
        *,
        violations: int = 0,
        max_complexity: int = 0,
    ) -> IntelItem:
        priority = IntelPriority.WARNING if violations > 5 else IntelPriority.INFO
        item = IntelItem.create(
            category=IntelCategory.STRUCTURE,
            priority=priority,
            source="structure_evaluator",
            content=f"项目结构: {total_files} 文件, {total_packages} 包, {violations} 违规, 最大复杂度 {max_complexity}",
            metadata=(
                ("total_files", str(total_files)),
                ("total_packages", str(total_packages)),
                ("violations", str(violations)),
                ("max_complexity", str(max_complexity)),
            ),
        )
        self.items.append(item)
        return item

    def from_quality(
        self,
        score: float,
        *,
        details: str = "",
    ) -> IntelItem:
        priority = (
            IntelPriority.CRITICAL if score < 40
            else IntelPriority.WARNING if score < 70
            else IntelPriority.INFO
        )
        item = IntelItem.create(
            category=IntelCategory.QUALITY,
            priority=priority,
            source="quality_evaluator",
            content=f"代码质量评分: {score:.1f}/100" + (f" — {details}" if details else ""),
            metadata=(
                ("quality_score", f"{score:.1f}"),
            ),
        )
        self.items.append(item)
        return item

    def collect_all(self) -> tuple[IntelItem, ...]:
        return tuple(self.items)

    def clear(self) -> None:
        self.items.clear()


class DailyDigestGenerator:
    @staticmethod
    def generate(
        items: tuple[IntelItem, ...],
        report_date: str | None = None,
    ) -> DailyDigest:
        target_date = report_date or date.today().isoformat()

        cat_counts: dict[str, int] = {}
        pri_counts: dict[str, int] = {}
        for item in items:
            cat_counts[item.category.value] = cat_counts.get(item.category.value, 0) + 1
            pri_counts[item.priority.value] = pri_counts.get(item.priority.value, 0) + 1

        key_findings = DailyDigestGenerator._extract_findings(items)
        recommendations = DailyDigestGenerator._generate_recommendations(items)
        summary = DailyDigestGenerator._build_summary(items, cat_counts, pri_counts)

        return DailyDigest(
            report_date=target_date,
            items=items,
            summary=summary,
            key_findings=tuple(key_findings),
            recommendations=tuple(recommendations),
            category_counts=tuple(sorted(cat_counts.items())),
            priority_counts=tuple(sorted(pri_counts.items())),
        )

    @staticmethod
    def _extract_findings(items: tuple[IntelItem, ...]) -> list[str]:
        findings: list[str] = []
        critical = [i for i in items if i.priority == IntelPriority.CRITICAL]
        if critical:
            findings.append(f"发现 {len(critical)} 条关键情报需要立即关注")

        warnings = [i for i in items if i.priority == IntelPriority.WARNING]
        if warnings:
            warning_cats = {w.category.value for w in warnings}
            findings.append(f"共 {len(warnings)} 条警告，涉及: {', '.join(sorted(warning_cats))}")

        behavior_items = [i for i in items if i.category == IntelCategory.BEHAVIOR]
        if behavior_items:
            findings.append(f"行为监控记录 {len(behavior_items)} 条观察")

        errors = [i for i in items if i.category == IntelCategory.ERROR]
        if errors:
            findings.append(f"捕获 {len(errors)} 个错误事件")

        optimizations = [i for i in items if i.category == IntelCategory.OPTIMIZATION]
        if optimizations:
            findings.append(f"完成 {len(optimizations)} 次自优化周期")

        return findings

    @staticmethod
    def _generate_recommendations(items: tuple[IntelItem, ...]) -> list[str]:
        recs: list[str] = []

        categories = {i.category for i in items}
        if IntelCategory.BEHAVIOR in categories:
            behavior_warnings = [
                i for i in items
                if i.category == IntelCategory.BEHAVIOR and i.priority in (IntelPriority.WARNING, IntelPriority.CRITICAL)
            ]
            if behavior_warnings:
                recs.append("检查行为指标，考虑调整交互策略或系统参数")

        if IntelCategory.ERROR in categories:
            recs.append("审查近期错误日志，排查工具参数和路径问题")

        if IntelCategory.SECURITY in categories:
            recs.append("立即处理安全相关发现，移除硬编码密钥")

        quality_items = [i for i in items if i.category == IntelCategory.QUALITY]
        if quality_items:
            low_quality = [i for i in quality_items if "质量评分" in i.content and float(i.metadata[0][1]) < 70 if i.metadata]
            if low_quality:
                recs.append("代码质量偏低，考虑进行结构优化")

        if not recs:
            recs.append("系统运行正常，继续保持当前工作模式")

        return recs

    @staticmethod
    def _build_summary(
        items: tuple[IntelItem, ...],
        cat_counts: dict[str, int],
        pri_counts: dict[str, int],
    ) -> str:
        if not items:
            return "今日无情报收集记录。"

        parts: list[str] = [f"共收集 {len(items)} 条情报。"]
        for cat, count in sorted(cat_counts.items()):
            parts.append(f"{cat}: {count} 条")
        critical_count = pri_counts.get(IntelPriority.CRITICAL.value, 0)
        if critical_count > 0:
            parts.append(f"其中 {critical_count} 条关键情报需要优先处理。")
        return " ".join(parts)


class IntelRelay:
    def __init__(self, output_dir: Path = Path(".lingclaude/intel")) -> None:
        self._output_dir = output_dir

    def relay(self, digest: DailyDigest) -> Result[Path]:
        try:
            self._output_dir.mkdir(parents=True, exist_ok=True)

            json_path = self._output_dir / f"digest_{digest.report_date}.json"
            json_path.write_text(
                json.dumps(digest.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            md_path = self._output_dir / f"digest_{digest.report_date}.md"
            md_path.write_text(digest.to_markdown(), encoding="utf-8")

            manifest_path = self._output_dir / "manifest.json"
            self._update_manifest(manifest_path, digest)

            return Result.ok(md_path)
        except Exception as e:
            return Result.fail(f"情报中继失败: {e}", code="INTEL_RELAY_ERROR")

    def load_digest(self, report_date: str) -> Result[DailyDigest]:
        json_path = self._output_dir / f"digest_{report_date}.json"
        if not json_path.exists():
            return Result.fail(f"日报不存在: {report_date}", code="DIGEST_NOT_FOUND")
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
            items = tuple(
                IntelItem(
                    category=IntelCategory(d["category"]),
                    priority=IntelPriority(d["priority"]),
                    source=d["source"],
                    content=d["content"],
                    timestamp=d["timestamp"],
                    metadata=tuple((k, v) for k, v in d.get("metadata", {}).items()),
                )
                for d in raw.get("items", [])
            )
            return Result.ok(DailyDigest(
                report_date=raw["report_date"],
                items=items,
                summary=raw["summary"],
                key_findings=tuple(raw.get("key_findings", [])),
                recommendations=tuple(raw.get("recommendations", [])),
                category_counts=tuple((k, v) for k, v in raw.get("category_counts", {}).items()),
                priority_counts=tuple((k, v) for k, v in raw.get("priority_counts", {}).items()),
            ))
        except Exception as e:
            return Result.fail(f"日报加载失败: {e}", code="DIGEST_LOAD_ERROR")

    def list_digests(self) -> Result[tuple[str, ...]]:
        if not self._output_dir.exists():
            return Result.ok(())
        try:
            dates = sorted(
                f.stem.replace("digest_", "")
                for f in self._output_dir.glob("digest_*.json")
            )
            return Result.ok(tuple(dates))
        except Exception as e:
            return Result.fail(f"列出日报失败: {e}", code="INTEL_LIST_ERROR")

    def _update_manifest(self, manifest_path: Path, digest: DailyDigest) -> None:
        manifest: dict[str, Any] = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                manifest = {}

        entries = manifest.get("entries", [])
        entry = {
            "date": digest.report_date,
            "total_items": len(digest.items),
            "json_file": f"digest_{digest.report_date}.json",
            "md_file": f"digest_{digest.report_date}.md",
        }
        entries = [e for e in entries if e.get("date") != digest.report_date]
        entries.append(entry)
        entries.sort(key=lambda e: e["date"], reverse=True)

        manifest["entries"] = entries
        manifest["last_updated"] = datetime.now(timezone.utc).isoformat()
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
