"""P2: Agent 声明化支持——.lingclaude/agents/*.md 即 agent 定义。

参考实现：learn-claude-code/agents/s04_subagent.py（Claude Code .claude/agents/*.md 格式）
+ Claude Code Agent SDK 官方文档（modelcontextprotocol/specification 仓库 AGENTS.md）

设计原则：
- 声明式：markdown + YAML frontmatter = agent 定义，无需写代码
- 分层：用户级 ~/.lingclaude/agents/（全局）+ 项目级 .lingclaude/agents/（覆盖）
- 无运行时依赖：纯扫描解析，无 import 开销
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import yaml

logger = logging.getLogger(__name__)

# ── 搜索路径 ──────────────────────────────────────────────────────────────

USER_AGENT_DIR = Path.home() / ".lingclaude" / "agents"
PROJECT_AGENT_DIR = Path(".lingclaude") / "agents"

# 测试隔离用：pytest fixture 可以 monkeypatch 这些
_test_user_dir: Path | None = None
_test_proj_dir: Path | None = None


def _set_test_dirs(user_dir: Path | None = None, proj_dir: Path | None = None) -> None:
    """测试用：临时覆盖搜索路径（不暴露给外部 API）。"""
    global _test_user_dir, _test_proj_dir
    _test_user_dir = user_dir
    _test_proj_dir = proj_dir


def _agent_search_dirs(cwd: Path | None = None) -> list[Path]:
    """agent 搜索路径（用户级先，同名时项目级后加载覆盖）。"""
    if cwd is None:
        cwd = Path.cwd()
    dirs = []
    # 测试路径优先（pytest monkeypatch 注入）
    user = _test_user_dir or USER_AGENT_DIR
    if user.is_dir():
        dirs.append(user)
    proj = _test_proj_dir or (cwd / PROJECT_AGENT_DIR)
    if proj.is_dir():
        dirs.append(proj)
    return dirs


# ── 数据模型 ──────────────────────────────────────────────────────────────

@dataclass
class AgentDef:
    """单个 agent 定义（对应一个 .md 文件）。"""
    name: str                           # slug，路径键
    description: str                    # 人类可读描述（触发条件）
    instruction: str                    # markdown body（agent 指令）
    model: str | None = None            # 指定模型档位（sonnet/opus/haiku）
    color: str | None = None            # UI 配色
    tools: list[str] = field(default_factory=list)       # 显式工具白名单
    disallowed_tools: list[str] = field(default_factory=list)  # 显式工具黑名单
    skills: list[str] = field(default_factory=list)      # 关联 skill 列表
    hooks: list[str] = field(default_factory=list)       # 关联 hook 列表
    auto: bool = False                  # 自动触发（无需显式调用）
    source_file: Path | None = None     # 来源文件（用于台账）

    @property
    def slug(self) -> str:
        return self.name

    def match(self, query: str) -> float:
        """简单关键词匹配得分（0.0~1.0）。"""
        q = query.lower()
        score = 0.0
        if q in self.name.lower():
            score += 0.5
        if q in self.description.lower():
            score += 0.3
        if any(q in kw.lower() for kw in (self.skills or [])):
            score += 0.2
        return min(score, 1.0)


# ── YAML frontmatter 解析 ──────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(raw: str) -> dict:
    """从 markdown 文件解析 YAML frontmatter。"""
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError as e:
        logger.warning("agent_registry: frontmatter 解析失败 %s: %s", raw[:80], e)
        return {}


def _parse_tools_field(val: str | list | None) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(v) for v in val]
    if isinstance(val, str):
        return [t.strip() for t in val.replace(",", " ").split() if t.strip()]
    return []


# ── 扫描与加载 ────────────────────────────────────────────────────────────

_SKIP_NAMES = frozenset({"readme", "index", "readme-zh", "readme-ja"})


def _scan_dir(dir_path: Path) -> Iterator[AgentDef]:
    """扫描单个目录，产生所有 .md 文件的 AgentDef。"""
    if not dir_path.is_dir():
        return
    for md_file in sorted(dir_path.glob("*.md")):
        # 跳过 README 等非 agent 定义文件
        stem = md_file.stem.lower()
        if stem in _SKIP_NAMES:
            continue
        try:
            raw = md_file.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("agent_registry: 读取 %s 失败: %s", md_file, e)
            continue

        fm = _parse_frontmatter(raw)
        name = fm.get("name") or md_file.stem
        if not name:
            continue
        if not str(name).strip():
            continue

        body_start = _FRONTMATTER_RE.match(raw)
        instruction = raw[body_start.end():] if body_start else raw

        yield AgentDef(
            name=str(name).strip(),
            description=str(fm.get("description", "")).strip(),
            instruction=instruction.strip(),
            model=str(fm["model"]) if fm.get("model") else None,
            color=str(fm["color"]) if fm.get("color") else None,
            tools=_parse_tools_field(fm.get("tools")),
            disallowed_tools=_parse_tools_field(fm.get("disallowedTools")),
            skills=_parse_tools_field(fm.get("skills")),
            hooks=_parse_tools_field(fm.get("hooks")),
            auto=bool(fm.get("auto", False)),
            source_file=md_file,
        )


def load_agents(cwd: Path | None = None) -> dict[str, AgentDef]:
    """加载所有 agent 定义（用户级先，同名时项目级后加载覆盖）。"""
    agents: dict[str, AgentDef] = {}
    for dir_path in _agent_search_dirs(cwd):
        for agent in _scan_dir(dir_path):
            agents[agent.name] = agent
    return agents


def match_agents(query: str, agents: dict[str, AgentDef] | None = None, top_k: int = 3) -> list[AgentDef]:
    """语义匹配 agents（当前简单关键词，后续可升级 embedding）。"""
    if agents is None:
        agents = load_agents()
    if not query or not agents:
        return []
    scored = [(a, a.match(query)) for a in agents.values() if a.match(query) > 0]
    scored.sort(key=lambda x: -x[1])
    return [a for a, _ in scored[:top_k]]


# ── 集成点（lazy 单例） ───────────────────────────────────────────────────

_agents: dict[str, AgentDef] | None = None


def get_agents() -> dict[str, AgentDef]:
    global _agents
    if _agents is None:
        _agents = load_agents()
    return _agents


def invalidate():
    """热更用：清除缓存，下次 get_agents() 重扫。"""
    global _agents
    _agents = None
