"""P0-B (2026-09-22) 沙箱 Landlock/Seatland + NOT_GOOD_AT 字段定向回归。

覆盖：
- LandlockSandboxProvider.available / probe_reason / wrap 生成形态
- SeatlandSandboxProvider 非 darwin 恒不可用
- create_default_sandbox_provider 三级降级链（bwrap 缺席 → landlock → noop）
- NOT_GOOD_AT seam 字段语义（只读 grep 类不进沙箱）
- 已有 bwrap 路径不回归（BwrapSandboxProvider 原行为保留）
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

import pytest

import lingclaude.engine.sandbox_provider as _sp
from lingclaude.engine.sandbox_provider import (
    BwrapSandboxProvider,
    LandlockSandboxProvider,
    NoopSandboxProvider,
    SeatlandSandboxProvider,
    NOT_GOOD_AT,
    create_default_sandbox_provider,
    _landlock_probe,
    _bwrap_probe,
    _bwrap_probe_cache,
    _landlock_probe_cache,
)


def _reset_caches() -> None:
    global _bwrap_probe_cache, _landlock_probe_cache
    _bwrap_probe_cache = None
    _landlock_probe_cache = None


@pytest.fixture(autouse=True)
def _clean_caches():
    _reset_caches()
    yield
    _reset_caches()


# ── NOT_GOOD_AT 字段语义 ──

def test_not_good_at_contains_readonly_diag():
    """NOT_GOOD_AT 至少声明 readonly_diag（只读 grep 类不进沙箱）。"""
    assert "readonly_diag" in NOT_GOOD_AT
    assert "只读" in NOT_GOOD_AT["readonly_diag"]


def test_not_good_at_is_dict():
    assert isinstance(NOT_GOOD_AT, dict)
    for k, v in NOT_GOOD_AT.items():
        assert isinstance(k, str) and isinstance(v, str)


# ── Landlock provider 形态 ──

def test_landlock_provider_has_protocol_attrs():
    p = LandlockSandboxProvider()
    assert p.name == "landlock"
    assert hasattr(p, "available") and callable(p.available)
    assert hasattr(p, "wrap") and callable(p.wrap)


def test_landlock_wrap_unavailable_returns_command_as_is():
    """Landlock 不可用时 wrap 原样返回命令（noop 语义，不阻断）。"""
    p = LandlockSandboxProvider()
    with patch.object(type(p), "available", return_value=False):
        out = p.wrap("echo hello", working_dir=None, allow_network=False)
        assert out == "echo hello"


def test_landlock_wrap_generates_helper_call_when_available():
    """Landlock 可用时 wrap 生成 python3 helper + 目标命令链。"""
    p = LandlockSandboxProvider()
    with patch.object(type(p), "available", return_value=True):
        out = p.wrap("echo hello", working_dir="/home/ai", allow_network=False,
                     extra_writable_dirs=["/home/ai/lingclaude"])
    assert "python3" in out
    assert "_landlock_helper.py" in out
    assert "--writable" in out
    assert "/home/ai" in out
    assert "/tmp" in out
    assert "echo hello" in out


from contextlib import contextmanager

import lingclaude.engine.sandbox_provider as _sp


@contextmanager
def _probe_isolated():
    """探针缓存隔离：置空 → 调用方探测 → 恢复原值（防跨文件缓存污染）。"""
    saved = _sp._landlock_probe_cache
    try:
        _sp._landlock_probe_cache = None
        yield
    finally:
        _sp._landlock_probe_cache = saved


def test_landlock_probe_non_linux_unavailable():
    """非 Linux 平台 Landlock 探测恒 False（Linux-only）。

    2026-09-22 加缓存隔离：原实现未清缓存，任何先跑过的探针测试会让本测试
    直接返回缓存值而假绿/假红（顺序敏感缺陷）。
    """
    with patch("sys.platform", "darwin"), _probe_isolated():
        ok, reason = _landlock_probe()
        assert ok is False
        assert reason


def test_landlock_probe_linux_cache_consistency():
    """Linux 平台探测结果缓存一致（两次调用同值）。

    2026-09-22 加缓存隔离：先清缓存再连调两次，杜绝「缓存非空时同义反复」。
    """
    with patch("sys.platform", "linux"), _probe_isolated():
        a = _landlock_probe()
        b = _landlock_probe()
        assert a == b


# ── Seatland provider 形态 ──

def test_seatland_provider_has_protocol_attrs():
    p = SeatlandSandboxProvider()
    assert p.name == "seatland"
    assert hasattr(p, "available") and callable(p.available)
    assert hasattr(p, "wrap") and callable(p.wrap)


def test_seatland_unavailable_on_linux():
    """Linux 上 Seatland 恒不可用（macOS-only 后端）。"""
    p = SeatlandSandboxProvider()
    with patch("sys.platform", "linux"):
        assert p.available() is False
        assert p.probe_reason()


def test_seatland_wrap_unavailable_returns_command():
    p = SeatlandSandboxProvider()
    with patch.object(type(p), "available", return_value=False):
        out = p.wrap("echo x", working_dir="/tmp", allow_network=True)
        assert out == "echo x"


# ── 三级降级链 create_default_sandbox_provider ──

def test_default_provider_bwrap_available_uses_bwrap():
    """bwrap 可用 → 默认后端是 BwrapSandboxProvider。"""
    with patch.object(BwrapSandboxProvider, "available", return_value=True):
        prov = create_default_sandbox_provider()
        assert isinstance(prov, BwrapSandboxProvider)


def test_default_provider_bwrap_unavailable_landlock_available_uses_landlock():
    """bwrap 缺席 + Landlock 可用（Linux）→ 默认后端是 LandlockSandboxProvider。"""
    with patch("sys.platform", "linux"), \
         patch.object(BwrapSandboxProvider, "available", return_value=False), \
         patch.object(LandlockSandboxProvider, "available", return_value=True), \
         patch.object(LandlockSandboxProvider, "probe_reason", return_value="n/a"), \
         patch.object(BwrapSandboxProvider, "probe_reason", return_value="bwrap 缺席"):
        prov = create_default_sandbox_provider()
        assert isinstance(prov, LandlockSandboxProvider)


def test_default_provider_all_unavailable_falls_to_noop():
    """bwrap 缺席 + Landlock 缺席（Linux）→ 降级 NoopSandboxProvider（fail-safe）。"""
    with patch("sys.platform", "linux"), \
         patch.object(BwrapSandboxProvider, "available", return_value=False), \
         patch.object(LandlockSandboxProvider, "available", return_value=False), \
         patch.object(LandlockSandboxProvider, "probe_reason", return_value="landlock 缺席"), \
         patch.object(BwrapSandboxProvider, "probe_reason", return_value="bwrap 缺席"):
        prov = create_default_sandbox_provider()
        assert isinstance(prov, NoopSandboxProvider)
        assert prov.available() is True  # noop 恒可用


def test_default_provider_macos_seatland_available_uses_seatland():
    """macOS：bwrap 缺席 + seatland 可用 → SeatlandSandboxProvider。"""
    with patch("sys.platform", "darwin"), \
         patch.object(BwrapSandboxProvider, "available", return_value=False), \
         patch.object(SeatlandSandboxProvider, "available", return_value=True):
        prov = create_default_sandbox_provider()
        assert isinstance(prov, SeatlandSandboxProvider)


# ── bwrap 原行为不回归 ──

def test_bwrap_wrap_includes_unshare_net_by_default():
    p = BwrapSandboxProvider()
    with patch.object(type(p), "available", return_value=True), \
         patch.object(type(p), "wrap", wraps=None) as _:
        pass
    # 直接测 bwrap wrap 生成形态（不真跑 bwrap）：
    p2 = BwrapSandboxProvider()
    p2._bwrap = "bwrap"
    with patch("lingclaude.engine.sandbox_provider._bwrap_probe", return_value=(True, None)):
        out = p2.wrap("echo hi", working_dir="/home/ai")
    assert "bwrap" in out
    assert "--unshare-net" in out
    assert "echo hi" in out


def test_bwrap_wrap_allow_network_drops_unshare_net():
    p = BwrapSandboxProvider()
    p._bwrap = "bwrap"
    with patch("lingclaude.engine.sandbox_provider._bwrap_probe", return_value=(True, None)):
        out = p.wrap("git fetch", working_dir="/home/ai", allow_network=True)
    assert "--unshare-net" not in out
    assert "git fetch" in out
