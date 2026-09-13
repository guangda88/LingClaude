"""P1-2 wiring.py 接入 PolicyLoader + hot_update 热更契约测试。

验收口径（灵元 P1）：
- manifest YAML 修改 → 不重启进程，下次装配生效（mtime watch）
- 读失败回退代码内 WIRING_MANIFEST（graceful degrade）
- 既有装配行为零变化（回归网 test_p2a_wiring_manifest 已覆盖）
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from lingclaude.core import policy_loader as pl
from lingclaude.core.wiring import WiringContext, assemble


@pytest.fixture(autouse=True)
def _reset_policy_cache():
    pl.reset()
    yield
    pl.reset()


def test_hot_update_reloads_manifest_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """改 wiring_manifest.yaml → hot_update → 下次装配生效（进程不重启）。"""
    # 用 tmp 策略目录构造一个可写 manifest（最小 2 条：state + collaborator）
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)
    manifest_yaml = tmp_path / "wiring_manifest.yaml"
    manifest_yaml.write_text(
        "manifest:\n"
        "  - attr: _probe_state\n"
        "    factory: \n"
        "    phase: state\n"
        "    note: 探测条目\n"
        "  - attr: _probe_behavior\n"
        "    factory: _make_probe\n"
        "    phase: collaborator\n"
        "    note: 探测协作者\n",
        encoding="utf-8",
    )

    # 构造裸引擎 + 最小 WiringContext
    class BareEngine:
        pass

    engine = BareEngine()
    ctx = WiringContext(engine=engine, session_manager=None, provider=None)

    try:
        # 首次装配：走 PolicyLoader 加载 YAML
        wired = assemble(ctx)
        assert "_probe_state" in wired
        assert "_probe_behavior" in wired
        assert getattr(engine, "_probe_state") is None
        assert getattr(engine, "_probe_behavior") is None

        # 修改 manifest：新增一条 state
        time.sleep(0.02)
        manifest_yaml.write_text(
            "manifest:\n"
            "  - attr: _probe_state\n"
            "    factory: \n"
            "    phase: state\n"
            "    note: 探测条目\n"
            "  - attr: _probe_behavior\n"
            "    factory: _make_probe\n"
            "    phase: collaborator\n"
            "    note: 探测协作者\n"
            "  - attr: _probe_new\n"
            "    factory: \n"
            "    phase: state\n"
            "    note: 热更新增条目\n",
            encoding="utf-8",
        )

        # 触发热更（PolicyLoader.hot_update）→ 下次装配读到新 manifest
        assert pl.hot_update() is True

        engine2 = BareEngine()
        ctx2 = WiringContext(engine=engine2, session_manager=None, provider=None)
        wired2 = assemble(ctx2)
        assert "_probe_new" in wired2
        assert getattr(engine2, "_probe_new") is None
    finally:
        monkeypatch.undo()


def test_missing_manifest_falls_back_to_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """manifest YAML 缺失 → 回退代码内 WIRING_MANIFEST（graceful degrade）。"""
    # 空策略目录：wiring_manifest.yaml 不存在
    monkeypatch.setattr(pl, "_POLICIES_DIR", tmp_path)

    from lingclaude.core.query_engine import QueryEngine
    from lingclaude.core.session import SessionManager
    from lingclaude.core.wiring import WIRING_MANIFEST

    engine = QueryEngine.__new__(QueryEngine)
    engine.session_manager = SessionManager()
    engine.session_id = "hotupdate_fallback_test"
    ctx = WiringContext(
        engine=engine,
        session_manager=engine.session_manager,
        provider=object(),
        runtime=None,
    )
    wired = assemble(ctx)

    # 装配条目不比代码内 manifest 少（回退 = 代码内全量）
    assert len(wired) == len(WIRING_MANIFEST)
    assert "_behavior" in wired
    assert "state_store" in wired
