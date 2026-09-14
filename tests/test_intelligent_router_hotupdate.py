"""P6: intelligent_router 策略热更契约测试。

背景（2026-09-14, 以灵元 1.0 为尺）：
  P1-1 接入 PolicyLoader 后，4 处 _load_policy() 中有 3 处收敛为调用时
  实时读（behavior_aware_router / l10_a_post_audit / wiring）；唯独
  intelligent_router 仍保留模块级一次性 `_POLICY = _load_policy()`：
  - 导入时读一次，之后 _task_keywords/_complexity_keywords 永远读旧数据
  - 改 router_keywords.yaml → 进程内不生效，违反"策略是 data，改 YAML 不重启"

P6 (本轮):
  - 删除模块级 _POLICY，改为调用时 _load_policy()（内部 PolicyLoader 缓存 +
    mtime watch + 30s 节流，开销可忽略）
  - 本测试验证：改 YAML → hot_update() → 下个 from_query 实时生效

验收口径（灵元 P6）：
  - 改 YAML 不重启进程，下个调用生效（mtime watch + hot_update）
  - 读失败回退内置默认（graceful degrade，零行为变化）
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lingclaude.core import policy_loader as pl
from lingclaude.model.intelligent_router import TaskType


@pytest.fixture(autouse=True)
def _reset_cache():
    pl.reset()
    yield
    pl.reset()


def _write_router_keywords(path: Path, generation_kws: list[str]) -> None:
    """写入最小可用的 router_keywords.yaml（含空 complexity 段，兼容 .get()）。"""
    path.write_text(
        yaml.safe_dump(
            {
                "task_keywords": {
                    "code_generation": generation_kws,
                },
                "complexity_keywords": {},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def test_task_type_from_query_reads_live_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """改 router_keywords.yaml 关键词 → from_query 分类实时生效（不再读模块级旧缓存）。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    policy = tmp_path / "router_keywords.yaml"

    # 首次：code_generation 命中 "写"
    _write_router_keywords(policy, ["写"])
    assert TaskType.from_query("写一个函数") == TaskType.CODE_GENERATION

    # 热更：关键词换成 "构建"（"写" 不再命中）→ 下个调用实时生效
    _write_router_keywords(policy, ["构建"])
    assert pl.hot_update() is True
    assert TaskType.from_query("写一个函数") != TaskType.CODE_GENERATION
    assert TaskType.from_query("构建一个系统") == TaskType.CODE_GENERATION


def test_complexity_keywords_reads_live_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """复杂度关键词同样实时读策略（命中 complex 关键词 → 不落入内置默认空档）。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    policy = tmp_path / "router_keywords.yaml"

    policy.write_text(
        yaml.safe_dump(
            {
                "task_keywords": {},
                "complexity_keywords": {
                    "complex": ["特殊复杂度词"],
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    from lingclaude.model.intelligent_router import _complexity_keywords

    assert "特殊复杂度词" in _complexity_keywords("complex")

    # 热更移除 → 回退内置默认
    policy.write_text(
        yaml.safe_dump(
            {"task_keywords": {}, "complexity_keywords": {}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    assert pl.hot_update() is True
    assert "特殊复杂度词" not in _complexity_keywords("complex")


def test_missing_policy_falls_back_builtin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """策略文件缺失 → from_query 仍可用内置默认分类（graceful degrade）。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    assert TaskType.from_query("写一个函数") == TaskType.CODE_GENERATION
    assert TaskType.from_query("你好") == TaskType.OTHER


def test_real_router_keywords_yaml_present():
    """真实策略文件存在且结构完整（迁移零行为变化）。"""
    data = pl.load("router_keywords")
    assert "task_keywords" in data
    assert "complexity_keywords" in data
    assert "code_generation" in data["task_keywords"]
    assert len(data["task_keywords"]["code_generation"]) > 0
