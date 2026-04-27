from __future__ import annotations

import pytest
from pathlib import Path
import tempfile

from lingclaude.core.skill_parser import (
    parse_skill_md,
    Skill,
    SkillMeta,
    SkillRegistry,
    is_read_only_tool,
    _READ_ONLY_TOOLS,
)
from lingclaude.core.types import Result


class TestParseSkillMd:
    def test_full_frontmatter(self) -> None:
        content = """---
name: my-skill
description: A test skill
version: "2.0.0"
author: lingke
category: testing
tags: unit, integration
priority: 8
enabled: true
---

# My Skill Instructions
Do the thing.
"""
        result = parse_skill_md(content)
        assert result.is_ok
        skill = result.data
        assert skill.meta.name == "my-skill"
        assert skill.meta.description == "A test skill"
        assert skill.meta.version == "2.0.0"
        assert skill.meta.author == "lingke"
        assert skill.meta.category == "testing"
        assert skill.meta.tags == ("unit", "integration")
        assert skill.meta.priority == 8
        assert skill.meta.enabled is True
        assert "# My Skill Instructions" in skill.body

    def test_no_frontmatter_fallback(self) -> None:
        content = "# Just Markdown\n\nSome instructions."
        source = Path("/tmp/test-skill/SKILL.md")
        result = parse_skill_md(content, source_path=source)
        assert result.is_ok
        assert result.data.meta.name == "SKILL"
        assert result.data.body == content.strip()

    def test_empty_content(self) -> None:
        result = parse_skill_md("   \n  \n")
        assert result.is_error
        assert result.code == "EMPTY_SKILL"

    def test_frontmatter_empty_body_when_matched(self) -> None:
        content = "---\nname: empty\n---\n\nSome trailing text after empty."
        result = parse_skill_md(content)
        assert result.is_ok
        assert result.data.body == "Some trailing text after empty."

    def test_partial_frontmatter(self) -> None:
        content = "---\nname: partial\ndescription: Only name and desc\n---\nBody here."
        result = parse_skill_md(content)
        assert result.is_ok
        skill = result.data
        assert skill.meta.name == "partial"
        assert skill.meta.version == "1.0.0"
        assert skill.meta.priority == 5
        assert skill.meta.enabled is True

    def test_enabled_false(self) -> None:
        content = "---\nname: disabled-skill\nenabled: false\n---\nBody."
        result = parse_skill_md(content)
        assert result.is_ok
        assert result.data.meta.enabled is False

    def test_source_path_defaults_to_stem(self) -> None:
        content = "No frontmatter body"
        result = parse_skill_md(content, source_path=Path("/path/to/my-cool-skill.md"))
        assert result.is_ok
        assert result.data.meta.name == "my-cool-skill"

    def test_tags_comma_separated(self) -> None:
        content = '---\nname: tagged\ntags: "a, b, c"\n---\nBody.'
        result = parse_skill_md(content)
        assert result.is_ok
        assert result.data.meta.tags == ("a", "b", "c")


class TestSkill:
    def test_context_text_format(self) -> None:
        meta = SkillMeta(name="test", description="desc", tags=("t1", "t2"))
        skill = Skill(meta=meta, body="Do something.")
        ctx = skill.context_text
        assert "# Skill: test" in ctx
        assert "> desc" in ctx
        assert "> Tags: t1, t2" in ctx
        assert "Do something." in ctx

    def test_to_dict(self) -> None:
        meta = SkillMeta(name="x", version="3.0", tags=("a",))
        skill = Skill(meta=meta, body="hello", source_path=Path("/tmp/x.md"))
        d = skill.to_dict()
        assert d["name"] == "x"
        assert d["version"] == "3.0"
        assert d["tags"] == ["a"]
        assert d["source"] == "/tmp/x.md"
        assert d["body_length"] == 5


class TestSkillRegistry:
    def test_load_from_file(self, tmp_path: Path) -> None:
        skill_file = tmp_path / "my-skill.md"
        skill_file.write_text(
            "---\nname: file-skill\ndescription: From file\n---\nFile body."
        )
        registry = SkillRegistry()
        result = registry.load_from_file(skill_file)
        assert result.is_ok
        assert result.data.name == "file-skill"
        assert registry.get("file-skill") is not None

    def test_load_from_file_not_found(self) -> None:
        registry = SkillRegistry()
        result = registry.load_from_file(Path("/nonexistent/SKILL.md"))
        assert result.is_error
        assert result.code == "NOT_FOUND"

    def test_load_from_directory(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()

        (skills_dir / "SKILL.md").write_text(
            "---\nname: dir-skill\n---\nBody."
        )
        sub = skills_dir / "sub"
        sub.mkdir()
        (sub / "SKILL.md").write_text(
            "---\nname: sub-skill\n---\nSub body."
        )

        registry = SkillRegistry()
        loaded, failed = registry.load_from_directory(skills_dir)
        assert loaded == 2
        assert failed == 0
        assert registry.get("dir-skill") is not None
        assert registry.get("sub-skill") is not None

    def test_load_from_directory_skips_readme(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "README.md").write_text("# Not a skill")
        (skills_dir / "real.md").write_text("---\nname: real\n---\nBody.")

        registry = SkillRegistry()
        loaded, _ = registry.load_from_directory(skills_dir)
        assert registry.get("real") is not None

    def test_unload(self) -> None:
        registry = SkillRegistry()
        meta = SkillMeta(name="removeme")
        registry.load_skill(Skill(meta=meta, body="x"))
        assert registry.unload("removeme") is True
        assert registry.unload("removeme") is False

    def test_list_skills_sorted_by_priority(self) -> None:
        registry = SkillRegistry()
        for name, pri in [("low", 1), ("high", 9), ("mid", 5)]:
            registry.load_skill(Skill(
                meta=SkillMeta(name=name, priority=pri), body="x"
            ))
        names = [s.name for s in registry.list_skills()]
        assert names == ["high", "mid", "low"]

    def test_get_all_context_respects_max_chars(self) -> None:
        registry = SkillRegistry()
        for i in range(5):
            registry.load_skill(Skill(
                meta=SkillMeta(name=f"s{i}", priority=10 - i),
                body="x" * 500,
            ))
        ctx = registry.get_all_context(max_chars=800)
        assert len(ctx) <= 1200

    def test_get_context_for_category(self) -> None:
        registry = SkillRegistry()
        registry.load_skill(Skill(
            meta=SkillMeta(name="a", category="api"), body="API skill"
        ))
        registry.load_skill(Skill(
            meta=SkillMeta(name="b", category="dev"), body="Dev skill"
        ))
        ctx = registry.get_context_for_category("api")
        assert "API skill" in ctx
        assert "Dev skill" not in ctx

    def test_get_context_for_category_disabled(self) -> None:
        registry = SkillRegistry()
        registry.load_skill(Skill(
            meta=SkillMeta(name="off", category="api", enabled=False), body="Off"
        ))
        ctx = registry.get_context_for_category("api")
        assert ctx == ""

    def test_stats(self) -> None:
        registry = SkillRegistry()
        registry.load_skill(Skill(
            meta=SkillMeta(name="a", category="api"), body="x"
        ))
        registry.load_skill(Skill(
            meta=SkillMeta(name="b", category="api", enabled=False), body="x"
        ))
        s = registry.stats()
        assert s["total"] == 2
        assert s["enabled"] == 1
        assert s["categories"]["api"] == 2

    def test_nonexistent_directory(self) -> None:
        registry = SkillRegistry()
        loaded, failed = registry.load_from_directory(Path("/nonexistent"))
        assert loaded == 0
        assert failed == 0


class TestIsReadOnlyTool:
    def test_all_known_read_only_tools(self) -> None:
        expected = {
            "glob", "grep", "ls", "view", "read_file",
            "search_code", "git_status", "git_log", "git_diff", "git_blame",
            "list_functions", "index_project", "analyze_full",
            "knowledge_search", "session_list", "check_triggers",
            "get_advice", "evaluate_code",
        }
        assert _READ_ONLY_TOOLS == expected

    def test_read_only_tools_return_true(self) -> None:
        for tool in ("glob", "grep", "ls", "view", "git_status"):
            assert is_read_only_tool(tool) is True

    def test_write_tools_return_false(self) -> None:
        for tool in ("edit", "write", "bash", "delete", "mkdir", "move"):
            assert is_read_only_tool(tool) is False

    def test_unknown_tool_returns_false(self) -> None:
        assert is_read_only_tool("totally_unknown_tool") is False
