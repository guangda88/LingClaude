"""P7 (2026-09-14): PolicyLoader 粒度增强 — (st_mtime_ns, st_size) 组合判据。

验收口径（灵元 P1 热更可靠性增强）:
- get() 的文件变更检测用 (st_mtime_ns, st_size) 组合判据
  - st_mtime_ns：纳秒级，比秒级 st_mtime 精确
  - st_size：第二判据，捕获同 jiffy（ext4 mtime 实际粒度 ~4ms）内
    快速连续写入中长度不同的改写（mtime_ns 相同但 size 不同）
- hot_update() 强制重读语义不变（不依赖 mtime 判定，变更即捕获；
  同长度改写由 hot_update 内容比较兜底，bd64a11）

背景:
  bd64a11 修掉 mtime 粒度漏检依赖 hot_update() 强制重读兜底；
  P7 让 get() 自身在纳秒+size 粒度下即可检出同 jiffy 内长度不同的变更。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lingclaude.core import policy_loader as pl


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch: pytest.MonkeyPatch):
    pl.reset()
    yield
    pl.reset()


def test_rapid_diff_size_write_detected_via_get(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """同 jiffy 内快速连续写入（长度不同）：get() 组合判据可检出，不依赖 hot_update。

    秒级 st_mtime 在 <1s 内两次写入相同 → 漏检；
    即使纳秒 mtime_ns 同 jiffy 相同，size 不同也能捕获。
    """
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    target = tmp_path / "rapid.yaml"
    target.write_text("key: v1\n", encoding="utf-8")

    try:
        # 首次加载缓存
        assert pl.get("rapid") == {"key": "v1"}

        # 不 sleep：同一 jiffy 内立刻重写（长度不同）
        target.write_text("key: v222\n", encoding="utf-8")

        # 组合判据：mtime_ns 可能相同，但 size 不同 → _changed 检出
        assert pl._changed(target) is True, "(mtime_ns, size) 组合应检出同 jiffy 变长改写"

        # 完整链路：清掉节流时间戳，get() 自行发现变更并重读
        key = str(target.resolve())
        pl._LAST_CHECK_BY_PATH.pop(key, None)
        assert pl.get("rapid") == {"key": "v222"}
    finally:
        monkeypatch.undo()


def test_mtime_cache_stores_ns_size_tuple(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """_MTIME_CACHE 存的是 (st_mtime_ns, st_size) 元组。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    target = tmp_path / "ns.yaml"
    target.write_text("a: 1\n", encoding="utf-8")

    try:
        pl.get("ns")
        key = str(target.resolve())
        cached = pl._MTIME_CACHE.get(key)
        assert cached is not None
        assert isinstance(cached, tuple) and len(cached) == 2
        st = target.stat()
        assert cached == (st.st_mtime_ns, st.st_size)
    finally:
        monkeypatch.undo()


def test_same_size_rewrite_caught_by_hot_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """同长度改写（内容变、长度不变）：mtime_ns+size 均相同，由 hot_update 内容比较兜底。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    target = tmp_path / "same.yaml"
    # 两个内容长度相同（v1 / v9 都 2 字节值）
    target.write_text("key: v1\n", encoding="utf-8")

    try:
        assert pl.get("same") == {"key": "v1"}

        # 同长度改写（'v1' -> 'v9'）
        target.write_text("key: v9\n", encoding="utf-8")

        # 组合判据可能无法区分（同 jiffy + 同 size）→ 不强求 _changed True
        # 但 hot_update 内容比较必须捕获
        assert pl.hot_update() is True
        assert pl.get("same") == {"key": "v9"}
    finally:
        monkeypatch.undo()


def test_hot_update_semantics_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """hot_update() 强制重读语义不变（不依赖 mtime 判定，变更即捕获）。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    target = tmp_path / "hu.yaml"
    target.write_text("x: 1\n", encoding="utf-8")

    try:
        assert pl.get("hu") == {"x": 1}
        # 同秒内重写，不 sleep
        target.write_text("x: 2\n", encoding="utf-8")
        assert pl.hot_update() is True
        assert pl.get("hu") == {"x": 2}
    finally:
        monkeypatch.undo()


def test_no_change_returns_false_for_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """文件未变时 _changed 判定 False（组合比较不误报）。"""
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    target = tmp_path / "stable.yaml"
    target.write_text("v: 1\n", encoding="utf-8")

    try:
        pl.get("stable")
        # 未改动 → 不变化（连续两次 stat 不误报）
        assert pl._changed(target) is False
        assert pl._changed(target) is False
    finally:
        monkeypatch.undo()
