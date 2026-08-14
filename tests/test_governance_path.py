#!/usr/bin/env python3
"""linggit _is_governance_path 单元测试 — L6 changeset 覆盖灵克 governance/"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from linggit.bot import _is_governance_path


def test_governance_dir_matches():
    assert _is_governance_path("lingclaude/governance/__init__.py")
    assert _is_governance_path("lingclaude/governance/voter.py")
    assert _is_governance_path("lingclaude/governance/sub/x.py")


def test_non_governance_dirs_no_match():
    assert not _is_governance_path("lingclaude/other/foo.py")
    assert not _is_governance_path("tests/test_bot.py")
    assert not _is_governance_path("linggit/bot.py")


def test_prefix_collision_no_match():
    """lingclaude/governance_bak 不是 governance 目录。"""
    assert not _is_governance_path("lingclaude/governance_bak/x.py")
    assert not _is_governance_path("lingclaude/governance.py")


def test_empty_and_unrelated_no_match():
    assert not _is_governance_path("")
    assert not _is_governance_path("README.md")
    assert not _is_governance_path("governance/x.py")


def test_nested_path_match():
    """路径中含 governance/ 段也算。"""
    assert _is_governance_path("x/y/lingclaude/governance/z.py")
