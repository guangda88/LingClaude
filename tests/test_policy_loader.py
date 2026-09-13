"""P1-1 PolicyLoader 测试：统一 YAML 策略加载 + mtime watch 热更。

回归网（灵元 P1 验收口径）：
- 加载 4 个现有策略文件，数据与各消费方现状一致
- 改 YAML → 不重启，下个调用生效（mtime watch）
- 读失败 → 返回 {}（graceful degrade，调用方回退内置默认）
- 非法策略名/目录穿越 → 拒绝
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from lingclaude.core import policy_loader as pl

POLICIES = Path(__file__).resolve().parent.parent / "lingclaude" / "core" / "policies"


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch):
    pl.reset()
    yield
    pl.reset()


def test_load_behavior_router():
    data = pl.load("behavior_router")
    assert data.get("default_strategy") == "standard"
    assert data["error_rate"]["high_threshold"] == 0.4
    assert data["hallucination"]["high_threshold"] == 0.7


def test_load_claim_patterns():
    data = pl.load("claim_patterns")
    patterns = data.get("patterns", [])
    assert len(patterns) == 15
    assert patterns[0]["pattern_id"] == "notified"
    assert all("regex" in p for p in patterns)


def test_load_router_keywords():
    data = pl.load("router_keywords")
    assert "code_generation" in data.get("task_keywords", {})
    assert "生成" in data["task_keywords"]["code_generation"]
    assert "complexity_keywords" in data


def test_load_wiring_manifest():
    data = pl.load("wiring_manifest")
    manifest = data.get("manifest", [])
    assert len(manifest) >= 50
    assert all("attr" in it for it in manifest)


def test_get_caches_and_watch_hotreload(tmp_path: Path):
    """get(): 首次加载缓存 → 改文件 → hot_update 生效。"""
    # 用 tmp_path 替代策略目录，构造一个可写策略文件
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    target = tmp_path / "test_policy.yaml"
    target.write_text("key: v1\n", encoding="utf-8")

    try:
        # 首次加载
        assert pl.get("test_policy") == {"key": "v1"}

        # 改文件（mtime 变化）
        time.sleep(0.02)
        target.write_text("key: v2\n", encoding="utf-8")

        # 强制 watch（绕过节流）
        assert pl._changed(target) is True
        assert pl.hot_update() is True
        assert pl.get("test_policy") == {"key": "v2"}
    finally:
        monkeypatch.undo()


def test_missing_file_returns_empty():
    assert pl.load("no_such_policy") == {}
    assert pl.get("no_such_policy") == {}


def test_invalid_name_rejected():
    assert pl.load("../secret") == {}
    assert pl.load("../../etc/passwd") == {}
    assert pl.load("a/b") == {}
    assert pl.load("") == {}
    assert pl.load(".") == {}
    assert pl.load("..") == {}


def test_path_traversal_guard(tmp_path: Path):
    """目录穿越双保险：即使解析到 policies 外也拒绝。"""
    monkeypatch = pytest.MonkeyPatch()
    outside = tmp_path / "outside.yaml"
    outside.write_text("evil: 1\n", encoding="utf-8")
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)

    try:
        # a/../outside 解析后越界 → 拒绝
        assert pl.load("a/../outside") == {}
    finally:
        monkeypatch.undo()


def test_corrupt_yaml_returns_empty(tmp_path: Path):
    """坏 YAML → {}（graceful degrade）。"""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    bad = tmp_path / "bad.yaml"
    bad.write_text(": : : not yaml [[[\n", encoding="utf-8")

    try:
        assert pl.load("bad") == {}
    finally:
        monkeypatch.undo()


def test_hot_update_noop_when_unchanged(tmp_path: Path):
    """文件未变 → hot_update 返回 False。"""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    target = tmp_path / "p.yaml"
    target.write_text("k: 1\n", encoding="utf-8")

    try:
        assert pl.get("p") == {"k": 1}
        assert pl.hot_update() is False
        assert pl.get("p") == {"k": 1}
    finally:
        monkeypatch.undo()
