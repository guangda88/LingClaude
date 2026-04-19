from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any

from lingclaude.engine.tools import ToolDefinition


class ToolCategory(str, Enum):
    CORE = "core"
    FILESYSTEM = "filesystem"
    SEARCH = "search"
    EXECUTION = "execution"
    GIT = "git"
    CODE_ANALYSIS = "code_analysis"
    OPTIMIZATION = "optimization"
    LEARNING = "learning"
    NETWORK = "network"
    COMMUNICATION = "communication"
    AUDIO = "audio"
    MCP = "mcp"
    SYSTEM = "system"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ToolManifest:
    name: str
    category: ToolCategory
    tags: tuple[str, ...] = ()
    priority: int = 5
    always_include: bool = False
    concurrency_safe: bool = True

    def matches_tag(self, tag: str) -> bool:
        return tag.lower() in self.tags


@dataclass
class RoutingResult:
    tools: tuple[dict[str, Any], ...]
    categories: tuple[ToolCategory, ...]
    total_available: int
    selected_count: int

    @property
    def reduction_ratio(self) -> float:
        if self.total_available == 0:
            return 0.0
        return 1.0 - (self.selected_count / self.total_available)


class ToolRouter:
    MAX_TOOLS_PER_REQUEST = 30

    # Minimal concept seeds — bootstrapping vocabulary, NOT per-tool config.
    # Real vocabulary grows dynamically from tool metadata via _learn().
    _CATEGORY_SEEDS: dict[ToolCategory, tuple[str, ...]] = {
        ToolCategory.FILESYSTEM: (
            "file", "read", "write", "edit", "path", "directory",
            "目录", "文件", "读取", "写入", "编辑",
        ),
        ToolCategory.SEARCH: (
            "search", "find", "grep", "glob", "pattern", "match",
            "搜索", "查找", "匹配",
        ),
        ToolCategory.EXECUTION: (
            "bash", "shell", "run", "execute", "command", "script",
            "命令", "执行", "运行", "脚本",
        ),
        ToolCategory.GIT: (
            "git", "commit", "branch", "diff", "blame", "merge",
            "提交", "分支", "合并",
        ),
        ToolCategory.CODE_ANALYSIS: (
            "ast", "parse", "function", "symbol", "index", "refactor",
            "函数", "分析", "重构", "索引",
        ),
        ToolCategory.OPTIMIZATION: (
            "optimize", "performance", "tune", "benchmark",
            "优化", "性能", "调优",
        ),
        ToolCategory.LEARNING: (
            "learn", "knowledge", "rule", "feedback",
            "学习", "知识", "规则", "反馈",
        ),
        ToolCategory.NETWORK: (
            "http", "fetch", "url", "web", "api", "download",
            "网络", "请求", "下载",
        ),
        ToolCategory.COMMUNICATION: (
            "message", "mail", "notify", "send", "post", "relay",
            "消息", "通知", "发送",
        ),
        ToolCategory.AUDIO: (
            "audio", "voice", "speech", "transcribe", "stt", "tts",
            "语音", "录音", "转录",
        ),
        ToolCategory.MCP: ("mcp", "remote", "proxy"),
        ToolCategory.SYSTEM: (
            "system", "env", "config", "daemon", "monitor",
            "系统", "环境", "监控",
        ),
    }

    def __init__(self, max_tools: int | None = None) -> None:
        self._max_tools = max_tools or self.MAX_TOOLS_PER_REQUEST
        self._always_include: set[str] = set()
        self._overrides: dict[str, ToolManifest] = {}
        self._vocab: dict[ToolCategory, set[str]] = defaultdict(set, {
            cat: set(seeds) for cat, seeds in self._CATEGORY_SEEDS.items()
        })

    def set_always_include(self, *names: str) -> None:
        self._always_include.update(names)

    def register_manifest(self, manifest: ToolManifest) -> None:
        self._overrides[manifest.name] = manifest
        if manifest.always_include:
            self._always_include.add(manifest.name)

    def register_tool(
        self,
        name: str,
        category: ToolCategory,
        *,
        tags: tuple[str, ...] = (),
        priority: int = 5,
        always_include: bool = False,
        concurrency_safe: bool = True,
    ) -> None:
        self.register_manifest(ToolManifest(
            name=name, category=category, tags=tags,
            priority=priority, always_include=always_include,
            concurrency_safe=concurrency_safe,
        ))

    def get_manifest(self, name: str) -> ToolManifest | None:
        return self._overrides.get(name)

    def list_manifests(self) -> tuple[ToolManifest, ...]:
        return tuple(self._overrides.values())

    def _infer_category(self, name: str, description: str) -> ToolCategory:
        text = f"{name} {description}".lower()
        best_cat = ToolCategory.UNKNOWN
        best_score = 0
        for cat, vocab in self._vocab.items():
            if cat == ToolCategory.CORE:
                continue
            score = sum(1 for kw in vocab if kw in text)
            if score > best_score:
                best_score = score
                best_cat = cat
        return best_cat

    def _learn(self, tool: ToolDefinition) -> None:
        cat = self._infer_category(tool.name, tool.description)
        tokens = re.findall(r'[a-z]{2,}', f"{tool.name} {tool.description}".lower())
        self._vocab[cat].update(tokens)

    def _detect_categories(self, query: str) -> list[ToolCategory]:
        q = query.lower()
        scores: dict[ToolCategory, int] = {}
        for cat, vocab in self._vocab.items():
            if cat == ToolCategory.CORE or cat == ToolCategory.UNKNOWN:
                continue
            score = sum(1 for kw in vocab if kw in q)
            if score > 0:
                scores[cat] = score
        if not scores:
            return [ToolCategory.CORE]
        threshold = max(scores.values()) * 0.5
        return [cat for cat, s in scores.items() if s >= threshold]

    def _tokenize_query(self, query: str) -> set[str]:
        tokens = set(re.findall(r'[a-z0-9]{2,}', query.lower()))
        tokens.update(re.findall(r'[\u4e00-\u9fff]+', query))
        return tokens

    def route(
        self,
        query: str,
        all_tools: tuple[ToolDefinition, ...],
        *,
        mode: str = "auto",
    ) -> RoutingResult:
        if mode == "all":
            return self._route_all(all_tools)
        if mode == "core":
            return self._route_core(all_tools)

        for t in all_tools:
            self._learn(t)

        query_tokens = self._tokenize_query(query)
        detected = self._detect_categories(query)
        scored = self._score_tools(all_tools, set(detected), query_tokens)
        selected = self._select_top(scored, self._max_tools)

        return RoutingResult(
            tools=tuple(t for t, _ in selected),
            categories=tuple(detected),
            total_available=len(all_tools),
            selected_count=len(selected),
        )

    def _score_tools(
        self,
        all_tools: tuple[ToolDefinition, ...],
        detected_set: set[ToolCategory],
        query_tokens: set[str],
    ) -> list[tuple[dict[str, Any], int]]:
        scored: list[tuple[dict[str, Any], int]] = []
        for tool in all_tools:
            if tool.name in self._always_include:
                scored.append((self._tool_to_dict(tool), 100))
                continue

            override = self._overrides.get(tool.name)
            if override:
                score = override.priority
                if override.category in detected_set:
                    score += 50
                for tag in override.tags:
                    if tag in tool.name.lower():
                        score += 10
                scored.append((self._tool_to_dict(tool), score))
                continue

            score = 5
            # Direct name↔query token match (strongest signal)
            name_tokens = set(re.findall(r'[a-z]{2,}', tool.name.lower()))
            score += len(query_tokens & name_tokens) * 15
            # Category match
            inferred = self._infer_category(tool.name, tool.description)
            if inferred in detected_set:
                score += 40
            # Description overlap with detected categories
            tool_text = f"{tool.name} {tool.description}".lower()
            for cat in detected_set:
                score += sum(1 for kw in self._vocab.get(cat, set()) if kw in tool_text)
            scored.append((self._tool_to_dict(tool), score))

        return scored

    def _select_top(
        self,
        scored: list[tuple[dict[str, Any], int]],
        max_tools: int,
    ) -> list[tuple[dict[str, Any], int]]:
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:max_tools]

    def _route_all(self, all_tools: tuple[ToolDefinition, ...]) -> RoutingResult:
        return RoutingResult(
            tools=tuple(self._tool_to_dict(t) for t in all_tools),
            categories=tuple(c for c in ToolCategory),
            total_available=len(all_tools),
            selected_count=len(all_tools),
        )

    def _route_core(self, all_tools: tuple[ToolDefinition, ...]) -> RoutingResult:
        if not self._always_include:
            return self._route_all(all_tools)
        selected = tuple(
            self._tool_to_dict(t) for t in all_tools
            if t.name in self._always_include
        )
        return RoutingResult(
            tools=selected,
            categories=(ToolCategory.CORE,),
            total_available=len(all_tools),
            selected_count=len(selected),
        )

    @staticmethod
    def _tool_to_dict(tool: ToolDefinition) -> dict[str, Any]:
        return {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": tool.parameters,
                "required": list(tool.parameters.keys()),
            },
        }


_CORE_TOOLS: tuple[str, ...] = ("bash", "read", "glob", "grep")


def create_default_router(max_tools: int | None = None) -> ToolRouter:
    router = ToolRouter(max_tools=max_tools)
    router.set_always_include(*_CORE_TOOLS)
    return router
