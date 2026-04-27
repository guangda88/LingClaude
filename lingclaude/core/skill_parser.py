from __future__ import annotations

"""SKILL.md parser — 兼容 Open Agent Skills 生态系统 (skills.sh)。

解析 SKILL.md 格式（YAML frontmatter + Markdown body），
将 skill 内容注入 agent 上下文。

支持:
- 本地 skills 目录扫描
- ClawHub/skills.sh 格式兼容
- skill 优先级和分类
- 动态加载/卸载
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lingclaude.core.types import Result

logger = logging.getLogger(__name__)

_SKILL_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL
)


@dataclass(frozen=True)
class SkillMeta:
    name: str
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    category: str = ""
    tags: tuple[str, ...] = ()
    priority: int = 5
    enabled: bool = True


@dataclass
class Skill:
    meta: SkillMeta
    body: str
    source_path: Path | None = None

    @property
    def name(self) -> str:
        return self.meta.name

    @property
    def context_text(self) -> str:
        tag_str = ", ".join(self.meta.tags) if self.meta.tags else ""
        header = f"# Skill: {self.meta.name}"
        if self.meta.description:
            header += f"\n> {self.meta.description}"
        if tag_str:
            header += f"\n> Tags: {tag_str}"
        return f"{header}\n\n{self.body}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.meta.name,
            "description": self.meta.description,
            "version": self.meta.version,
            "author": self.meta.author,
            "category": self.meta.category,
            "tags": list(self.meta.tags),
            "priority": self.meta.priority,
            "enabled": self.meta.enabled,
            "body_length": len(self.body),
            "source": str(self.source_path) if self.source_path else None,
        }


def parse_skill_md(content: str, source_path: Path | None = None) -> Result[Skill]:
    """Parse a SKILL.md file into a Skill object.

    Format:
        ---
        name: skill-name
        description: What it does
        version: "1.0.0"
        ---
        # Instructions for the agent
        ...
    """
    match = _SKILL_FRONTMATTER_RE.match(content.strip())
    if not match:
        raw_name = source_path.stem if source_path else "unknown"
        body = content.strip()
        if not body:
            return Result.fail("Empty skill content", code="EMPTY_SKILL")
        meta = SkillMeta(name=raw_name)
        return Result.ok(Skill(meta=meta, body=body, source_path=source_path))

    frontmatter_str = match.group(1)
    body = match.group(2).strip()

    if not body:
        return Result.fail(f"Skill has no body: {frontmatter_str[:50]}", code="EMPTY_BODY")

    meta_dict: dict[str, Any] = {}
    for line in frontmatter_str.split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'")
        if not value:
            continue
        if key in ("tags",):
            meta_dict[key] = tuple(t.strip() for t in value.split(",") if t.strip())
        elif key in ("priority",):
            try:
                meta_dict[key] = int(value)
            except ValueError:
                pass
        elif key in ("enabled",):
            meta_dict[key] = value.lower() in ("true", "yes", "1")
        else:
            meta_dict[key] = value

    name = meta_dict.get("name", (source_path.stem if source_path else "unnamed"))
    meta = SkillMeta(
        name=name,
        description=meta_dict.get("description", ""),
        version=meta_dict.get("version", "1.0.0"),
        author=meta_dict.get("author", ""),
        category=meta_dict.get("category", ""),
        tags=meta_dict.get("tags", ()),
        priority=meta_dict.get("priority", 5),
        enabled=meta_dict.get("enabled", True),
    )

    return Result.ok(Skill(meta=meta, body=body, source_path=source_path))


class SkillRegistry:
    """Manages loaded skills — scan, load, inject into context."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def load_skill(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def load_from_file(self, path: Path) -> Result[Skill]:
        if not path.exists():
            return Result.fail(f"Skill file not found: {path}", code="NOT_FOUND")
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            return Result.fail(f"Failed to read skill: {e}", code="READ_ERROR")

        result = parse_skill_md(content, source_path=path)
        if result.is_ok and result.data:
            self.load_skill(result.data)
        return result

    def load_from_directory(self, dir_path: Path) -> tuple[int, int]:
        """Load all SKILL.md files from a directory. Returns (loaded, failed)."""
        loaded = 0
        failed = 0
        if not dir_path.exists():
            return 0, 0

        for skill_file in sorted(dir_path.rglob("SKILL.md")):
            result = self.load_from_file(skill_file)
            if result.is_ok:
                loaded += 1
            else:
                failed += 1
                logger.debug("Failed to load skill %s: %s", skill_file, result.error)

        for skill_file in sorted(dir_path.glob("*.md")):
            if skill_file.name == "SKILL.md":
                continue
            if skill_file.name.upper() in (
                "README.md", "CHANGELOG.md", "AGENTS.md",
                "CRUSH.md", "CLAUDE.md",
            ):
                continue
            result = self.load_from_file(skill_file)
            if result.is_ok and result.data.name not in self._skills:
                loaded += 1

        return loaded, failed

    def unload(self, name: str) -> bool:
        return self._skills.pop(name, None) is not None

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_skills(self) -> list[Skill]:
        return sorted(self._skills.values(), key=lambda s: s.meta.priority, reverse=True)

    def get_context_for_category(self, category: str) -> str:
        skills = [s for s in self._skills.values() if s.meta.category == category and s.meta.enabled]
        if not skills:
            return ""
        parts = [s.context_text for s in sorted(skills, key=lambda s: s.meta.priority, reverse=True)]
        return "\n\n---\n\n".join(parts)

    def get_all_context(self, max_chars: int = 8000) -> str:
        enabled = [s for s in self._skills.values() if s.meta.enabled]
        enabled.sort(key=lambda s: s.meta.priority, reverse=True)
        parts: list[str] = []
        total = 0
        for skill in enabled:
            ctx = skill.context_text
            if total + len(ctx) > max_chars:
                remaining = max_chars - total
                if remaining > 100:
                    parts.append(ctx[:remaining] + "\n... (truncated)")
                break
            parts.append(ctx)
            total += len(ctx)
        return "\n\n---\n\n".join(parts)

    def stats(self) -> dict[str, Any]:
        categories: dict[str, int] = {}
        for s in self._skills.values():
            cat = s.meta.category or "uncategorized"
            categories[cat] = categories.get(cat, 0) + 1
        return {
            "total": len(self._skills),
            "enabled": sum(1 for s in self._skills.values() if s.meta.enabled),
            "categories": categories,
        }


_READ_ONLY_TOOLS = frozenset({
    "glob", "grep", "ls", "view", "read_file",
    "search_code", "git_status", "git_log", "git_diff", "git_blame",
    "list_functions", "index_project", "analyze_full",
    "knowledge_search", "session_list", "check_triggers",
    "get_advice", "evaluate_code",
})


def is_read_only_tool(tool_name: str) -> bool:
    return tool_name in _READ_ONLY_TOOLS
