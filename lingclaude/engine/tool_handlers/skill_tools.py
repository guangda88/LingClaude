"""SkillToolsMixin — skill_search / skill_read 工具面。

把 data/skills_index.json（全族 75 skills / 9 落点，2026-09-27 建）接进 lc 引擎
工具面：模型可查"灵族有什么 skill、怎么用"，按需读 SKILL.md 全文（用后随轮次
丢弃，无常驻注册面——文件即接缝）。

J1：只读检索，不写任何状态；J4 无 run 态（纯查询，无状态机）；
hermetic：索引缺失时 fail-soft 返回可读错误，不抛穿透。
"""
from __future__ import annotations

import json
from pathlib import Path

# repo 根的 data/skills_index.json（skill_tools.py 位于 lingclaude/engine/tool_handlers/，
# parents[1] = lingclaude/engine → repo 根需 parents[2]... 实测 parents 链：
# [0]=tool_handlers, [1]=engine, [2]=lingclaude(包), [3]=repo根）
INDEX_PATH = Path(__file__).parents[3] / "data" / "skills_index.json"


def _load_index() -> list[dict]:
    with open(INDEX_PATH, encoding="utf-8") as f:
        return json.load(f)["skills"]


def skill_search_impl(keyword: str, limit: int = 10) -> str:
    """按关键词检索全族 skill（name/desc 子串匹配）。返回格式化清单文本。"""
    try:
        skills = _load_index()
    except Exception as e:  # noqa: BLE001 —— 索引缺失 fail-soft
        return f"[skill_search] 索引不可用: {type(e).__name__}: {e}（可跑 scripts/skills_index.py 重建）"
    k = (keyword or "").strip().lower()
    if not k:
        hits = skills[:limit]
    else:
        hits = [s for s in skills
                if k in s.get("name", "").lower() or k in s.get("desc", "").lower()]
        hits = hits[:limit]
    if not hits:
        return f"[skill_search] 无命中: '{keyword}'（索引共 {len(skills)} skills）"
    lines = [f"[skill_search] 命中 {len(hits)}/{len(skills)}（关键词 '{keyword}'）"]
    for s in hits:
        lines.append(f"- {s['name']} [{s['member']}] {s.get('desc', '')[:80]}")
        lines.append(f"  path: {s['path']}")
    return "\n".join(lines)


def skill_read_impl(name: str) -> str:
    """读指定 skill 的 SKILL.md 全文（按需加载，用后随轮次丢弃）。"""
    try:
        skills = _load_index()
    except Exception as e:  # noqa: BLE001
        return f"[skill_read] 索引不可用: {type(e).__name__}: {e}"
    hits = [s for s in skills if s.get("name") == (name or "").strip()]
    if not hits:
        return f"[skill_read] 未索引: '{name}'（先 skill_search 确认名称）"
    path = Path(hits[0]["path"])
    if not path.is_file():
        return f"[skill_read] SKILL.md 缺失: {path}（该 skill 可能已迁移/清退）"
    try:
        body = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:  # noqa: BLE001
        return f"[skill_read] 读取失败: {type(e).__name__}: {e}"
    return f"[skill_read] {hits[0]['name']} [{hits[0]['member']}]\n{body}"


class SkillToolsMixin:
    """skill_search / skill_read 的 runtime handler（register_all_tools 按名绑定）。"""

    def _skill_search_handler(
        self,
        keyword: str = "",
        limit: int = 10,
    ) -> dict:
        text = skill_search_impl(keyword, limit)
        return {"content": text, "ok": True}

    def _skill_read_handler(
        self,
        name: str = "",
    ) -> dict:
        text = skill_read_impl(name)
        return {"content": text, "ok": True}
