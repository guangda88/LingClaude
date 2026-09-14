"""P8 (2026-09-14): webui_seam 统一查询视图 —— 能力缝同步到进程内 SeamRegistry。

验收口径（灵元 P5 延伸）:
- register_capability_seams() 把 fs/shell/llm/subagent 能力缝同步注册到
  lingclaude/core/seam.py 的进程内 SeamRegistry（SeamType.PROVIDER 槽位）
- 进程内 SeamRegistry.get(PROVIDER, "fs") 可查到能力提供者（统一查询视图）
- 与 lingflow 声明层（register_service）两套体系并存，互不冲突
- api._register_webui_seam_safe() fail-soft（lingflow 不可用时不影响启动）
"""
from __future__ import annotations

import pytest

from lingclaude.core.seam import SeamRegistry, SeamType


@pytest.fixture(autouse=True)
def _reset_seam(monkeypatch: pytest.MonkeyPatch):
    SeamRegistry.reset()
    yield
    SeamRegistry.reset()


def test_register_capability_seams_syncs_to_inprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """register_capability_seams() 后，进程内 SeamRegistry 可查到 4 个能力缝。"""
    from lingclaude.webui_seam import register_capability_seams

    # 真注册（lingflow 可用 + 默认 provider 注册）
    register_capability_seams()

    # 统一查询视图：进程内 SeamRegistry 可查 4 个能力缝
    for name in ("fs", "shell", "llm", "subagent"):
        seam = SeamRegistry.get(SeamType.PROVIDER, name)
        assert seam is not None
        assert seam.name == name
        assert "methods" in seam.interface

    # lingflow 声明层也注册了（两套并存）
    from lingflow.coordination.seam_registry import get_seam_registry

    lf_reg = get_seam_registry()
    snapshot = lf_reg.snapshot()
    for name in ("fs", "shell", "llm", "subagent"):
        assert any(name in str(s) for s in (snapshot if isinstance(snapshot, list) else [snapshot])), \
            f"lingflow 声明层应含 {name}"


def test_inprocess_seam_provider_executable():
    """进程内 SeamRegistry 存的是可调用实例（CapabilitySeam），不是纯声明。"""
    from lingclaude.webui_seam import register_capability_seams

    register_capability_seams()

    fs_seam = SeamRegistry.get(SeamType.PROVIDER, "fs")
    # CapabilitySeam 是 Provider 注册表（可注册/获取 provider）
    assert hasattr(fs_seam, "register_provider")
    assert hasattr(fs_seam, "get_provider")


def test_webui_seam_registration_fail_soft(monkeypatch: pytest.MonkeyPatch):
    """api._register_webui_seam_safe() fail-soft：lingflow 不可用只警告不抛。"""
    import importlib

    # 模拟 lingflow 不可用
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "lingflow.coordination.seam_registry":
            raise ImportError("lingflow 不可用（模拟）")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    from lingclaude import api

    # fail-soft：不抛异常，返回 None
    api._register_webui_seam_safe()  # 不抛即通过
