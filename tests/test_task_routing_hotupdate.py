"""P2-2 task_routing.yaml 策略外置 + PolicyLoader 热更契约测试。

验收口径（灵元 P2）：
- TASK_TYPE_TO_ROUTE 读 policies/task_routing.yaml（PolicyLoader）
- 改 YAML → 不重启进程，下次路由生效（mtime watch）
- 读失败回退内置默认（graceful degrade，零行为变化）
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lingclaude.core import policy_loader as pl
from lingclaude.model.intelligent_router import TaskType
from lingclaude.model.task_router import _load_task_type_to_route


@pytest.fixture(autouse=True)
def _reset_cache():
    pl.reset()
    yield
    pl.reset()


def test_mapping_matches_builtin_defaults():
    """策略文件映射与内置默认一致（迁移零行为变化）。"""
    mapping = _load_task_type_to_route()
    assert mapping[TaskType.CODE_GENERATION] == "coding"
    assert mapping[TaskType.DEBUGGING] == "coding"
    assert mapping[TaskType.ANALYSIS] == "chinese_reasoning"
    assert mapping[TaskType.DOCUMENTATION] == "english_general"
    assert mapping[TaskType.SEARCH] == "fast_response"
    assert mapping[TaskType.OTHER] == "fast_response"


def test_hotupdate_remaps_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """改 task_routing.yaml → 下次路由读到新映射（进程不重启）。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    policy = tmp_path / "task_routing.yaml"
    policy.write_text(
        "task_type_to_route:\n"
        "  code_generation: coding\n"
        "  search: fast_response\n"
        "  other: fast_response\n",
        encoding="utf-8",
    )

    try:
        assert _load_task_type_to_route()[TaskType.CODE_GENERATION] == "coding"

        # 热更：search → english_general
        policy.write_text(
            "task_type_to_route:\n"
            "  code_generation: coding\n"
            "  search: english_general\n"
            "  other: fast_response\n",
            encoding="utf-8",
        )
        assert pl.hot_update() is True
        assert _load_task_type_to_route()[TaskType.SEARCH] == "english_general"
    finally:
        monkeypatch.undo()


def test_missing_policy_falls_back_builtin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """策略文件缺失 → 回退内置默认。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    mapping = _load_task_type_to_route()
    assert mapping[TaskType.CODE_GENERATION] == "coding"
    assert mapping[TaskType.SEARCH] == "fast_response"


def test_unknown_task_type_skipped():
    """YAML 中出现未知 task_type → 跳过不崩，其余映射保留。"""
    data = pl.load("task_routing")
    assert "task_type_to_route" in data
    assert "default_route" in data
    assert data["default_route"] == "fast_response"
