"""P3 状态存储接缝测试 — StateStore / JsonFileBackend / LingYiBackend / StateBackend."""

from __future__ import annotations

import tempfile
from pathlib import Path

from lingclaude.core.state_store import (
    StateStore,
    JsonFileBackend,
    LingYiBackend,
    StateBackend,
    _LEGACY_PATHS,
)


def test_state_backend_protocol():
    """StateBackend 协议结构完整。"""
    assert hasattr(StateBackend, "save")
    assert hasattr(StateBackend, "load")
    assert hasattr(StateBackend, "_path_for")


def test_json_file_backend_roundtrip():
    """JsonFileBackend 读写往返一致。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        backend = JsonFileBackend(root)
        payload = {"key": "value", "count": 42, "nested": {"a": 1}}
        backend.save("test_type", "test_key", payload, root)
        loaded = backend.load("test_type", "test_key", root)
        assert loaded == payload


def test_json_file_backend_missing_returns_none():
    """JsonFileBackend 读不存在的键返回 None。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        backend = JsonFileBackend(root)
        loaded = backend.load("test_type", "nonexistent", root)
        assert loaded is None


def test_legacy_session_state_path():
    """session_state 旧路径兼容：~/.lingclaude/session_state.json。"""
    backend = JsonFileBackend()
    path = backend._path_for("session_state", "test", None)
    assert path == _LEGACY_PATHS["session_state"]


def test_state_store_json_backend():
    """StateStore json 后端门面同步 API 正常。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = StateStore(backend="json")
        payload = {"behavior": {"score": 0.9}, "messages": 10}
        store.save("session_state", "s1", payload, root)
        loaded = store.load("session_state", "s1", root)
        assert loaded == payload


def test_state_store_dualwrite_flag():
    """StateStore 双写期协议字段存在。"""
    store = StateStore(backend="json", dualwrite=True)
    assert hasattr(store, "_dualwrite")
    assert store._dualwrite is True


def test_state_store_read_from_lingyi_flag():
    """StateStore 读切期协议字段存在。"""
    store = StateStore(backend="json", read_from_lingyi=True)
    assert hasattr(store, "_read_from_lingyi")
    assert store._read_from_lingyi is True


def test_state_store_env_fallback():
    """StateStore 环境变量回落逻辑正常。"""
    import os
    os.environ["LINGYUAN_STATE_BACKEND"] = "json"
    store = StateStore()
    assert store._backend_name == "json"
    del os.environ["LINGYUAN_STATE_BACKEND"]


def test_lingyi_backend_structure():
    """LingYiBackend 结构定义完整（无 DSN 时不炸）。"""
    lingyi = LingYiBackend(dsn=None)
    assert lingyi._dsn is None
    assert hasattr(lingyi, "save")
    assert hasattr(lingyi, "load")
    assert hasattr(lingyi, "_ensure_schema")
    assert hasattr(lingyi, "close")


def test_lingyi_backend_ddl():
    """LingYiBackend 包含 2T3A DDL 定义。"""
    import inspect
    source = inspect.getsource(LingYiBackend._ensure_schema)
    assert "ly_state_records" in source
    assert "ly_state_events" in source
    assert "record_type" in source
    assert "key" in source
    assert "data" in source
    assert "PRIMARY KEY" in source


def test_state_store_integration_with_session_runtime():
    """StateStore 与 SessionRuntime 集成验证。"""
    from lingclaude.core.session_runtime import SessionRuntime

    class MockEngine:
        def __init__(self):
            self.session_id = "test-session"
            self._behavior = type("MockBehavior", (), {"to_dict": lambda self: {"score": 0.5}})()
            self._meta_cognition = type("MockMeta", (), {
                "_calibrator": type("Cal", (), {"records": {}})(),
                "_blind_spot_detector": type("BSD", (), {"error_patterns": {}})()
            })()
            self._total_messages_sent = 5
            self._l1_last_triggered_at = 10
            self.config = type("Config", (), {"structured_output": False})()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = StateStore(backend="json")

        engine = MockEngine()
        runtime = SessionRuntime(engine)
        runtime.session_state_path = lambda: root / "session_state.json"

        runtime.save_session_state()
        path = root / "session_state.json"
        assert path.exists()

        engine2 = MockEngine()
        runtime2 = SessionRuntime(engine2)
        runtime2.session_state_path = lambda: root / "session_state.json"
        runtime2.load_session_state()

        assert engine2._total_messages_sent == 5
        assert engine2._l1_last_triggered_at == 10