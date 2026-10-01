"""tests/test_agent_registry.py

门禁纪律（pre_report_gate）：汇报前必须全绿。
本文件测试 agent 声明化（P2）的核心功能：
  - YAML frontmatter 解析（含冒号描述必须引号）
  - 目录扫描（项目级覆盖用户级）
  - 关键词匹配
  - 热更缓存失效
  - 工具字段双格式支持（list / str）
  - README/无 name 文件静默跳过
"""
from __future__ import annotations

import pytest
from pathlib import Path

from lingclaude.core.agent_registry import (
    AgentDef,
    load_agents,
    match_agents,
    get_agents,
    invalidate,
    _parse_tools_field,
    _parse_frontmatter,
    _scan_dir,
    _set_test_dirs,
)


# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_test_dirs():
    """每个测试前后重置测试路径，隔离测试。"""
    _set_test_dirs(None, None)
    yield
    _set_test_dirs(None, None)


@pytest.fixture
def user_dir(tmp_path):
    d = tmp_path / "user" / ".lingclaude" / "agents"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def proj_dir(tmp_path):
    d = tmp_path / "proj" / ".lingclaude" / "agents"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def sample_agent_md():
    return """---
name: code-reviewer
description: "Use when code needs quality review. Ask to review, audit, or assess code quality."
model: sonnet
color: blue
tools:
  - Read
  - Grep
  - Glob
  - Bash
disallowedTools:
  - Write
  - Edit
skills:
  - code-review
  - security
hooks:
  - pre-commit
auto: false
---
# Code Reviewer Agent

You are an expert code reviewer. When invoked:

1. Read the target files
2. Analyze for: bugs, security issues, performance problems, style violations
3. Provide actionable feedback with specific line references

Always cite specific file:line references in your feedback.
"""


# ── frontmatter 解析 ──────────────────────────────────────────────────────

class TestFrontmatterParsing:
    def test_valid_frontmatter(self, sample_agent_md):
        fm = _parse_frontmatter(sample_agent_md)
        assert fm["name"] == "code-reviewer"
        assert fm["model"] == "sonnet"
        assert fm["color"] == "blue"
        assert fm["auto"] is False
        assert "Read" in fm["tools"]
        assert "Write" in fm["disallowedTools"]

    def test_description_with_colon_quoted(self, sample_agent_md):
        fm = _parse_frontmatter(sample_agent_md)
        assert "Use when code" in fm["description"]

    def test_no_frontmatter(self):
        raw = "# Just a note\n\nSome content here."
        fm = _parse_frontmatter(raw)
        assert fm == {}

    def test_malformed_yaml(self):
        raw = "---\nname: test\n  invalid: yaml: here\n---\nbody"
        fm = _parse_frontmatter(raw)
        assert fm == {}

    def test_tools_list_format(self):
        raw = "---\nname: test\ntools:\n  - Read\n  - Write\n---\n"
        fm = _parse_frontmatter(raw)
        assert fm["tools"] == ["Read", "Write"]

    def test_tools_string_format(self):
        raw = "---\nname: test\ntools: Read, Write, Bash\n---\n"
        fm = _parse_frontmatter(raw)
        assert fm["tools"] == "Read, Write, Bash"


class TestParseToolsField:
    def test_list_input(self):
        assert _parse_tools_field(["Read", "Bash"]) == ["Read", "Bash"]

    def test_string_input(self):
        result = _parse_tools_field("Read, Write, Bash")
        assert set(result) == {"Read", "Write", "Bash"}

    def test_string_with_spaces(self):
        result = _parse_tools_field("Read  Write   Bash")
        assert set(result) == {"Read", "Write", "Bash"}

    def test_none_input(self):
        assert _parse_tools_field(None) == []

    def test_empty_list(self):
        assert _parse_tools_field([]) == []


# ── 目录扫描 ──────────────────────────────────────────────────────────────

class TestScanDir:
    def test_loads_single_file(self, user_dir, sample_agent_md):
        (user_dir / "code-reviewer.md").write_text(sample_agent_md, encoding="utf-8")
        agents = {a.name: a for a in _scan_dir(user_dir)}
        assert "code-reviewer" in agents
        a = agents["code-reviewer"]
        assert a.model == "sonnet"
        assert a.color == "blue"
        assert "Read" in a.tools
        assert "Write" in a.disallowed_tools
        assert "code-review" in a.skills
        assert a.auto is False
        assert "Code Reviewer Agent" in a.instruction

    def test_skips_readme(self, user_dir):
        (user_dir / "README.md").write_text("# Agent Library\n\nCollection.", encoding="utf-8")
        agents = {a.name: a for a in _scan_dir(user_dir)}
        assert "README" not in agents
        assert len(agents) == 0

    def test_name_from_filename(self, user_dir):
        (user_dir / "test-agent.md").write_text("---\ndescription: test\n---\n", encoding="utf-8")
        agents = {a.name: a for a in _scan_dir(user_dir)}
        assert "test-agent" in agents

    def test_multiple_files(self, user_dir):
        content1 = "---\nname: agent-a\ndescription: A\n---\nA body"
        content2 = "---\nname: agent-b\ndescription: B\n---\nB body"
        (user_dir / "agent-a.md").write_text(content1, encoding="utf-8")
        (user_dir / "agent-b.md").write_text(content2, encoding="utf-8")
        agents = {a.name: a for a in _scan_dir(user_dir)}
        assert len(agents) == 2
        assert "agent-a" in agents and "agent-b" in agents

    def test_invalid_file_continues(self, user_dir):
        (user_dir / "valid.md").write_text("---\nname: valid\n---\nV", encoding="utf-8")
        agents = {a.name: a for a in _scan_dir(user_dir)}
        assert "valid" in agents


# ── 分层加载 ──────────────────────────────────────────────────────────────

class TestLayeredLoading:
    def test_proj_overrides_user(self, user_dir, proj_dir):
        _set_test_dirs(user_dir, proj_dir)
        user_md = "---\nname: reviewer\ndescription: user version\n---\nuser body"
        proj_md = "---\nname: reviewer\ndescription: project version\nmodel: opus\n---\nproject body"
        (user_dir / "reviewer.md").write_text(user_md, encoding="utf-8")
        (proj_dir / "reviewer.md").write_text(proj_md, encoding="utf-8")

        agents = load_agents(cwd=proj_dir.parent)
        assert agents["reviewer"].description == "project version"
        assert agents["reviewer"].model == "opus"

    def test_proj_extra_agents_kept(self, user_dir, proj_dir):
        _set_test_dirs(user_dir, proj_dir)
        user_md = "---\nname: user-agent\ndescription: U\n---\nU"
        proj_md = "---\nname: proj-agent\ndescription: P\n---\nP"
        (user_dir / "user-agent.md").write_text(user_md, encoding="utf-8")
        (proj_dir / "proj-agent.md").write_text(proj_md, encoding="utf-8")

        agents = load_agents(cwd=proj_dir.parent)
        assert "user-agent" in agents
        assert "proj-agent" in agents


# ── 关键词匹配 ────────────────────────────────────────────────────────────

class TestMatching:
    def test_name_match_scores_highest(self, user_dir):
        _set_test_dirs(user_dir)
        content = '---\nname: code-reviewer\ndescription: Code quality analysis\n---\n'
        (user_dir / "code-reviewer.md").write_text(content, encoding="utf-8")
        agents = load_agents(cwd=user_dir.parent)
        results = match_agents("code-reviewer", agents)
        assert results[0].name == "code-reviewer"
        assert results[0].match("code-reviewer") == 0.5

    def test_description_match(self, user_dir):
        _set_test_dirs(user_dir)
        content = '---\nname: security-audit\ndescription: Security vulnerability scanning\n---\n'
        (user_dir / "security-audit.md").write_text(content, encoding="utf-8")
        agents = load_agents(cwd=user_dir.parent)
        # "security" 在 description 中，匹配得分 0.3
        results = match_agents("security", agents)
        assert len(results) > 0
        assert results[0].name == "security-audit"

    def test_skill_match(self, user_dir):
        _set_test_dirs(user_dir)
        content = "---\nname: test-gen\ndescription: Generate tests\nskills:\n  - pytest\n  - testing\n---\n"
        (user_dir / "test-gen.md").write_text(content, encoding="utf-8")
        agents = load_agents(cwd=user_dir.parent)
        results = match_agents("pytest", agents)
        assert len(results) > 0

    def test_no_match_returns_empty(self, user_dir):
        _set_test_dirs(user_dir)
        content = '---\nname: reviewer\ndescription: Code review\n---\n'
        (user_dir / "reviewer.md").write_text(content, encoding="utf-8")
        agents = load_agents(cwd=user_dir.parent)
        results = match_agents("completely unrelated query xyz", agents)
        assert results == []

    def test_top_k_limit(self, user_dir):
        _set_test_dirs(user_dir)
        for i in range(5):
            (user_dir / f"agent-{i}.md").write_text(
                f"---\nname: agent-{i}\ndescription: agent {i}\n---\n", encoding="utf-8"
            )
        agents = load_agents(cwd=user_dir.parent)
        results = match_agents("agent", agents, top_k=2)
        assert len(results) == 2


# ── 热更缓存 ──────────────────────────────────────────────────────────────

class TestCacheInvalidation:
    def test_invalidate_then_reload(self, user_dir):
        _set_test_dirs(user_dir)
        content = "---\nname: cached-agent\ndescription: V1\n---\n"
        (user_dir / "cached-agent.md").write_text(content, encoding="utf-8")

        invalidate()
        agents1 = load_agents()

        # 修改文件
        (user_dir / "cached-agent.md").write_text(
            "---\nname: cached-agent\ndescription: V2\n---\n", encoding="utf-8"
        )

        # invalidate 后重新扫描，V2 生效
        invalidate()
        agents2 = load_agents()
        agent2 = agents2.get("cached-agent")
        assert agent2 is not None
        assert agent2.description == "V2"


# ── AgentDef 数据模型 ─────────────────────────────────────────────────────

class TestAgentDef:
    def test_slug_property(self):
        a = AgentDef(name="my-agent", description="test", instruction="")
        assert a.slug == "my-agent"

    def test_match_scores(self):
        a = AgentDef(
            name="test",
            description="code analysis",
            instruction="",
            skills=["security", "code-review"],
        )
        assert a.match("test") == 0.5
        assert a.match("code analysis") == 0.3
        assert a.match("security") == 0.2
        assert a.match("unrelated") == 0.0
        assert a.match("test code") <= 1.0
