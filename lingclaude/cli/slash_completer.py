# -*- coding: utf-8 -*-
"""斜杠命令补全器（A 2026-10-02）——注册表单源派生 + 注释渲染。

借鉴 atomcode Tab 补全机制（docs/atomcode_slash_command_reference.md §三/§五）：
  • 候选集合 = SLASH_REGISTRY 派生（hidden 除外）——与 /help、handle() 同源，
    根除「补全/help 漏登」两账本缺陷。
  • 候选项双列渲染：`/name  desc`（desc 缺失则只报名），arg_hint 非空再追加
    ` → <arg_hint>` 第三段。display_meta 走 PT 补全浮层灰字通道（meta 列），
    与 atomcode「名称+注释」双列同构。
  • 补全触发语义：仅当整词恰为命令名（`/mo` → `/model`，start_position=负词长，
    整词替换）；命令名后已有空格则不再补命令名（交给各命令的参数补全，
    本期不实现参数级补全，见 docs §五 P2 之外的后续）。
"""
from __future__ import annotations

from typing import Any

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from lingclaude.cli.commands import SLASH_REGISTRY


class SlashCompleter(Completer):
    """斜杠命令补全：注册表单源 + desc/arg_hint 双列注释（atomcode 同构）。"""

    def __init__(self) -> None:
        self._cache: tuple[int, list[Completion]] | None = None

    def get_completions(self, document: Document, complete_event: Any) -> Any:
        line = document.text_before_cursor
        # 只在「行首命令词」语境补全：非 / 开头直接退出（普通文本输入零打扰）。
        stripped = line.lstrip()
        if not stripped.startswith("/"):
            return
        # 光标必须仍在第一个 token 内（命令词未完）才补命令名；
        # 命令词后已空格 → 参数语境，本期无参数级补全，退出。
        first_token_end = line.find(" ")
        if first_token_end != -1 and document.cursor_position > first_token_end:
            return
        token = line[: document.cursor_position]
        # 光标截断在命令词中间也算（/mo|del → 补 /model）；斜杠后零散字符可以
        if " " in token:
            return
        prefix = token.lower()

        entries = self._sorted_entries()
        # 别名归组视图：与 /help 的 handler 同体归组同构（commands.py:309）——
        # 别名可补全，但注释列标「→ 主名」防三行同文案噪音（用户反馈 2026-10-02）。
        canon = self._canonical_names()
        for entry in entries:
            if not entry.name.lower().startswith(prefix):
                continue
            # 注释列：别名条目降级为指向注释；主条目 desc + arg_hint 追加
            main = canon.get(entry.name)
            if main and main != entry.name:
                meta = f"→ {main}（同义）"
            else:
                meta = entry.desc
                if entry.arg_hint:
                    meta = f"{meta} → {entry.arg_hint}"
            yield Completion(
                entry.name,
                start_position=-len(token),
                display=entry.name,
                display_meta=meta,
            )

    def _sorted_entries(self):
        """按注册表插入序输出（与 /help 表序一致），带失效缓存。"""
        stamp = len(SLASH_REGISTRY)
        if self._cache is None or self._cache[0] != stamp:
            self._cache = (
                stamp,
                [
                    e for e in SLASH_REGISTRY.values()
                    if not e.hidden
                ],
            )
        return self._cache[1]

    def _canonical_names(self) -> dict[str, str]:
        """别名 → 主名映射（handler 同体归组，与 /help 309 同构）。

        /quit /exit 是独立注册（handler 均 None，靠名字特判），handler 同体
        归组会把两条 None 条目互认——这里按注册序取首个作主名：/quit 为主，
        /exit 标「→ /quit（同义）」，与 /help 单条视图一致。
        """
        stamp = len(SLASH_REGISTRY)
        if self._cache is None or self._cache[0] != stamp:
            self._sorted_entries()  # 复用同一失效点
        canon: dict[str, str] = {}
        seen: dict[int, str] = {}
        for entry in SLASH_REGISTRY.values():
            if entry.hidden:
                continue
            key = id(entry.handler)
            if key in seen:
                canon[entry.name] = seen[key]
            else:
                seen[key] = entry.name
                canon[entry.name] = entry.name
        return canon
