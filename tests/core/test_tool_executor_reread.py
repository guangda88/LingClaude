"""长会话文件重读强制层测试（tool_executor read 快路径短路）。

覆盖诊断报告优化点1：缓存命中（内容 hash 未变）时不回全文，回惰性引用；
逃生门 force_refresh / offset·limit 窗口读；文件变更后 hash 变化自动 miss。
"""

from __future__ import annotations

import pytest

from lingclaude.core.context_cache import ContextCache
from lingclaude.core.tool_executor import ToolExecutor
from lingclaude.core.types import ToolErrorCode


class _Monitor:
    def record_file_read(self, path: str, content: str) -> bool:  # noqa: ARG002
        return False


class _Dementia:
    def record_file_read(self, path: str) -> None:  # noqa: ARG002
        pass


class _Cfg:
    max_tool_calls_per_session = 0  # 不限


class _Engine:
    def __init__(self, cache: ContextCache):
        self._cache = cache
        self._monitor = _Monitor()
        self._dementia_detector = _Dementia()
        self._session_cache_hits = 0
        self._runtime = None  # 快路径即可，无需 runtime
        self._tool_call_count = 0
        self._tool_call_log = []
        self.config = _Cfg()


def _make(tmp_path):
    cache = ContextCache(cache_size=16, ttl_hours=24, db_path=tmp_path / "c.db")
    engine = _Engine(cache)
    return ToolExecutor(engine), engine


def _read(ex: ToolExecutor, **kw):
    import json as _json
    return ex._execute_tool_typed("read", _json.dumps(kw))


def test_first_read_returns_full_content(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("line1\nline2\nline3\n")
    ex, _ = _make(tmp_path)
    r = _read(ex, path=str(f))
    assert not r.is_error
    assert r.data["cache_hit"] is False
    assert r.data["content"] == "line1\nline2\nline3\n"
    assert "content_omitted" not in r.data


def test_reread_short_circuits_and_omits_content(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("line1\nline2\nline3\n")
    ex, engine = _make(tmp_path)
    _read(ex, path=str(f))  # 首读
    r = _read(ex, path=str(f))  # 重读 → 短路
    assert not r.is_error
    assert r.data["cache_hit"] is True
    assert r.data["content_omitted"] is True
    assert "content" not in r.data
    assert r.data["lines"] == 4  # 3 行 + 末尾换行
    assert "force_refresh" in r.data["message"]
    assert engine._session_cache_hits == 1


def test_force_refresh_bypasses_short_circuit(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("hello\n")
    ex, _ = _make(tmp_path)
    _read(ex, path=str(f))
    r = _read(ex, path=str(f), force_refresh=True)
    assert r.data["cache_hit"] is False  # 强刷 → miss
    assert r.data["content"] == "hello\n"


def test_offset_window_bypasses_short_circuit(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("l1\nl2\nl3\n")
    ex, _ = _make(tmp_path)
    _read(ex, path=str(f))
    r = _read(ex, path=str(f), offset=1)  # 窗口读 → 不短路
    assert "content" in r.data


def test_limit_window_bypasses_short_circuit(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("l1\nl2\nl3\n")
    ex, _ = _make(tmp_path)
    _read(ex, path=str(f))
    r = _read(ex, path=str(f), limit=50)
    assert "content" in r.data


def test_file_change_auto_misses_and_returns_new_content(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("v1\n")
    ex, _ = _make(tmp_path)
    _read(ex, path=str(f))
    f.write_text("v2 CHANGED\n")  # 改内容 → hash 变
    r = _read(ex, path=str(f))
    assert r.data["cache_hit"] is False
    assert r.data["content"] == "v2 CHANGED\n"
    assert "content_omitted" not in r.data
