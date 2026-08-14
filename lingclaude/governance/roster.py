from __future__ import annotations
"""灵族成员权威 ID 映射

唯一权威来源: /home/ai/lingmessage/灵族成员表.md

所有代码中引用灵族成员 ID 时，必须从此模块导入，不允许硬编码。
如果成员表文件不可读，使用内嵌的 fallback 映射。
"""

import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

ROSTER_PATH = Path.home() / "lingmessage" / "灵族成员表.md"

# Fallback: 与成员表保持一致的映射
# 只有在成员表文件不可读时才使用
# 英文名 -> (中文名, 项目目录)
_FALLBACK_ROSTER: Dict[str, dict[str, str]] = {
    "lingflow": {"cn": "灵通", "dir": "lingflow"},
    "lingclaude": {"cn": "灵克", "dir": "lingclaude"},
    "lingresearch": {"cn": "灵研", "dir": "lingresearch"},
    "lingzhi": {"cn": "灵知", "dir": "lingzhi"},
    "lingtongask": {"cn": "灵通问道", "dir": "lingtongask"},
    "lingflow_plus": {"cn": "灵通+", "dir": "lingflow_plus"},
    "lingxi": {"cn": "灵犀", "dir": "lingxi"},
    "lingmessage": {"cn": "灵信", "dir": "lingmessage"},
    "lingweb": {"cn": "灵网", "dir": "lingweb"},
    "lingminopt": {"cn": "灵极优", "dir": "lingminopt"},
    "lingyang": {"cn": "灵扬", "dir": "lingyang"},
    "zhibridge": {"cn": "智桥", "dir": "zhibridge"},
    "lingcreate": {"cn": "灵创", "dir": "lingcreate"},
}


def _parse_roster(content: str) -> Dict[str, dict[str, str]]:
    lines = content.splitlines()
    roster: Dict[str, dict[str, str]] = {}
    for line in lines:
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 6:
            continue
        cn_name = parts[2].strip()
        en_name = parts[3].strip()
        proj_dir = parts[4].strip().replace(str(Path.home()) + "/", "")
        if not en_name or not cn_name or en_name in ("英文名", "---"):
            continue
        if en_name.startswith("Ling") or en_name.startswith("Zhi"):
            roster[en_name] = {"cn": cn_name, "dir": proj_dir}
    return roster


_roster: Optional[Dict[str, dict[str, str]]] = None


def get_roster() -> Dict[str, dict[str, str]]:
    global _roster
    if _roster is not None:
        return _roster

    if ROSTER_PATH.exists():
        try:
            content = ROSTER_PATH.read_text(encoding="utf-8")
            parsed = _parse_roster(content)
            if parsed:
                _roster = parsed
                logger.info("成员表已加载: %d 名成员 from %s", len(_roster), ROSTER_PATH)
                return _roster
            logger.warning("成员表解析结果为空，使用 fallback")
        except Exception as e:
            logger.warning("成员表读取失败: %s，使用 fallback", e)
    else:
        logger.warning("成员表不存在: %s，使用 fallback", ROSTER_PATH)

    _roster = dict(_FALLBACK_ROSTER)
    return _roster


def get_member_ids() -> list[str]:
    return list(get_roster().keys())


def get_cn_name(agent_id: str) -> str:
    roster = get_roster()
    entry = roster.get(agent_id)
    if entry:
        return entry["cn"]
    return agent_id


COUNCIL_MEMBER_IDS = [
    "lingflow",
    "lingclaude",
    "lingresearch",
    "lingtongask",
    "lingflow_plus",
    "lingminopt",
    "lingyang",
    "zhibridge",
    "lingweb",
    "lingcreate",
]
