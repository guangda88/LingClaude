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


def test_state_store_integration_with_session_runtime(monkeypatch, tmp_path):
    """I1: SessionRuntime 的 session_state 读写走 StateStore 接缝（json 后端，旧路径字节级兼容）。"""
    import json

    from lingclaude.core.session_runtime import SessionRuntime
    import lingclaude.core.state_store as ss_mod

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

    root = tmp_path
    # I1: session_state 旧路径由 StateStore._LEGACY_PATHS 决定；monkeypatch 到临时目录隔离
    monkeypatch.setitem(ss_mod._LEGACY_PATHS, "session_state", root / "session_state.json")

    engine = MockEngine()
    store = StateStore(backend="json")
    runtime = SessionRuntime(engine, state_store=store)

    runtime.save_session_state()
    path = root / "session_state.json"
    assert path.exists()

    # 写出的文件内容与手写 JSON 结构一致（字节级兼容）
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["behavior"]["score"] == 0.5
    assert data["total_messages_sent"] == 5
    assert data["l1_last_triggered_at"] == 10

    engine2 = MockEngine()
    runtime2 = SessionRuntime(engine2, state_store=StateStore(backend="json"))
    runtime2.load_session_state()

    assert engine2._total_messages_sent == 5
    assert engine2._l1_last_triggered_at == 10


def test_state_store_list_keys_roundtrip():
    """list_keys：保存后可枚举，嵌套 key 以 '/' 分隔，结果有序（直测补齐，台账 list-keys-no-direct-test）。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = StateStore(backend="json")
        store.save("bus_route", "r1", {"state": "open"}, root)
        store.save("bus_route", "grp/r2", {"state": "closed"}, root)
        store.save("bus_route", "a/b/c", {}, root)
        assert store.list_keys("bus_route", root) == ["a/b/c", "grp/r2", "r1"]
        # load 与 list_keys 互证：嵌套 key 可按枚举结果原样读回
        assert store.load("bus_route", "grp/r2", root) == {"state": "closed"}


def test_state_store_list_keys_empty_and_filtering():
    """list_keys：record_type 目录不存在返回 []；非 .json 文件不入枚举。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = StateStore(backend="json")
        assert store.list_keys("no_such_type", root) == []
        d = root / "mixed"
        d.mkdir()
        (d / "real.json").write_text("{}", encoding="utf-8")
        (d / "note.txt").write_text("x", encoding="utf-8")
        assert store.list_keys("mixed", root) == ["real"]


def test_state_store_list_keys_root_param_overrides_backend_default():
    """list_keys：root 参数优先于后端默认根（显式 root 全程不触碰 ~/.lingclaude）。"""
    with tempfile.TemporaryDirectory() as tmp:
        explicit = Path(tmp) / "explicit"
        store = StateStore(backend="json")
        store.save("t", "k", {"v": 1}, explicit)
        assert store.list_keys("t", explicit) == ["k"]
        # 无 root 时走后端默认根分支：只断言返回类型安全（只读枚举，不落盘）
        assert isinstance(store.list_keys("t"), list)
