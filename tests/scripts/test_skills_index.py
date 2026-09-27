# -*- coding: utf-8 -*-
"""tests/scripts/test_skills_index.py -- skills_index.py 契约测试.

文件即接缝: data/skills_index.json 是唯一事实来源, 测试通过临时索引注入,
覆盖 search/read/list/rebuild 四个子命令的核心语义:
- search: name/desc 大小写不敏感子串匹配
- read: 精确名命中, 未索引 -> SystemExit
- rebuild: 缺失路径校验输出 (名为 rebuild 实为校验 -- 契约以此为准)
"""
import importlib.util
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SPEC = importlib.util.spec_from_file_location(
    "skills_index_under_test",
    os.path.join(REPO_ROOT, "scripts", "skills_index.py"),
)
si = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("skills_index_under_test", si)
_SPEC.loader.exec_module(si)

_SKILLS = [
    {
        "name": "tui-render",
        "member": "lingclaude",
        "path": "/nonexistent/tui/SKILL.md",
        "desc": "TUI rendering seam plugin",
    },
    {
        "name": "graph-ingest",
        "member": "yitang",
        "path": "/nonexistent/graph/SKILL.md",
        "desc": "Knowledge graph ingestion",
    },
]


@pytest.fixture()
def index(monkeypatch, tmp_path):
    """临时索引文件, 不碰真实 data/skills_index.json."""
    idx = tmp_path / "skills_index.json"
    idx.write_text(json.dumps({"skills": _SKILLS}), encoding="utf-8")
    monkeypatch.setattr(si, "INDEX", idx)
    return idx


def test_search_matches_name_case_insensitive(index):
    hits = si.search("TUI")
    assert len(hits) == 1 and hits[0]["name"] == "tui-render"


def test_search_matches_desc(index):
    hits = si.search("GRAPH")
    assert len(hits) == 1 and hits[0]["name"] == "graph-ingest"


def test_search_no_hit_returns_empty(index):
    assert si.search("zzz-nope") == []


def test_read_returns_file_content(index, tmp_path):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# demo skill", encoding="utf-8")
    data = json.loads(index.read_text(encoding="utf-8"))
    data["skills"][0]["path"] = str(skill_file)
    index.write_text(json.dumps(data), encoding="utf-8")
    assert si.read("tui-render") == "# demo skill"


def test_read_unknown_name_raises_systemexit(index):
    with pytest.raises(SystemExit):
        si.read("no-such-skill")


def test_rebuild_reports_missing_paths(index, capsys):
    si.rebuild()
    out = capsys.readouterr().out
    assert "skills=2" in out
    assert "缺失=2" in out
    assert "MISS /nonexistent/tui/SKILL.md" in out


def test_rebuild_all_present(index, tmp_path, capsys):
    for i, s in enumerate(_SKILLS):
        f = tmp_path / ("s%d.md" % i)
        f.write_text("x", encoding="utf-8")
        s["path"] = str(f)
    index.write_text(json.dumps({"skills": _SKILLS}), encoding="utf-8")
    si.rebuild()
    out = capsys.readouterr().out
    assert "缺失=0" in out
