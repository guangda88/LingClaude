"""Skill索引 — 灵元上阵前清点弹药。

不加载、不执行、不spawn。只做三件事：
1. 扫描所有SKILL.md的frontmatter
2. 索引name + load_trigger + path
3. 遇到需求→返回匹配的SKILL.md路径（灵元自己读自己执行）
"""

import re
from pathlib import Path
from typing import Optional


class SkillIndex:
    """纯索引。灵元自己决定读不读。"""

    def __init__(self, skills_dirs: list[Path] = None) -> None:
        # 默认扫两个目录：成员自己的 + 全族共享的
        if skills_dirs is None:
            skills_dirs = [
                Path(".lingfamily/skills"),           # 成员自己的
                Path("/home/ai/.lingfamily/skills"),   # 全族共享的
            ]
        self.skills_dirs = skills_dirs
        self._index: list[dict] = []

    def scan(self) -> list[dict]:
        """扫描所有SKILL.md，构建索引。返回索引列表。"""
        self._index.clear()
        seen_paths = set()

        for sdir in self.skills_dirs:
            if not sdir.exists():
                continue
            for sf in sorted(sdir.glob("*/SKILL.md")):
                real = str(sf.resolve())
                if real in seen_paths:
                    continue
                seen_paths.add(real)

                try:
                    text = sf.read_text(encoding="utf-8")
                    m = re.match(r'^---\n(.*?)\n---', text, re.DOTALL)
                    fm = m.group(1) if m else ''

                    name = self._val(fm, "name") or sf.parent.name
                    triggers_raw = self._val(fm, "load_trigger") or ""
                    triggers = [t.strip().strip('"\'') for t in triggers_raw.split(",")] if triggers_raw else []
                    desc = (self._val(fm, "description") or "")[:80]

                    self._index.append({
                        "name": name,
                        "triggers": triggers,
                        "desc": desc,
                        "path": str(sf),
                    })
                except Exception:
                    continue

        return self._index

    def match(self, prompt: str) -> list[dict]:
        """按需求匹配Skill。返回匹配列表（按命中数排序）。灵元自己读path执行。"""
        if not self._index:
            self.scan()

        hits = []
        prompt_lower = prompt.lower()
        for s in self._index:
            matched = sum(1 for t in s["triggers"] if t.lower() in prompt_lower)
            if matched > 0:
                hits.append({**s, "score": matched})

        hits.sort(key=lambda x: -x["score"])
        return hits

    def list_all(self) -> list[dict]:
        """返回全部索引。"""
        if not self._index:
            self.scan()
        return self._index

    @staticmethod
    def _val(yaml_text: str, key: str) -> Optional[str]:
        m = re.search(rf'^{key}:\s*(.+)$', yaml_text, re.MULTILINE)
        return m.group(1).strip().strip('"\'') if m else None
