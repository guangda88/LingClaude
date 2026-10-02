"""P1③ 测试：supersede 链（件 A）+ per-project memory（件 B）。

hermetic 纪律：全部 tmp_path / monkeypatch 隔离，不触真库真文件。
"""
from __future__ import annotations

import sqlite3

import pytest

from lingclaude.core.l7_cognitive import CognitiveStore, CognitiveMemory
from lingclaude.core import project_memory as pm


# ══════════════════════════════ 件 A：supersede 链 ══════════════════════════════


@pytest.fixture()
def store(tmp_path):
    return CognitiveStore(db_path=str(tmp_path / "l7test.db"))


def test_same_key_three_writes_search_returns_only_current(store):
    """同 key 三写：检索只回现行，不堆三份（膨胀根治的契约锚）。"""
    for v in ("v1", "v2", "v3"):
        store.put_memory(CognitiveMemory(key="k:deploy", value=v, importance=7))
    hits = [m for m in store.search("k:deploy") if m.key == "k:deploy"]
    assert len(hits) == 1
    assert hits[0].value == "v3"


def test_history_preserves_all_versions(store):
    """链可回溯：get_memory_history 返回全版本，现行在前。"""
    for v in ("v1", "v2", "v3"):
        store.put_memory(CognitiveMemory(key="k:hist", value=v, importance=7))
    hist = store.get_memory_history("k:hist")
    assert [m.value for m in hist] == ["v3", "v2", "v1"]
    assert hist[0].superseded_by == ""
    assert hist[1].superseded_by == hist[0].id
    assert hist[2].superseded_by == hist[1].id


def test_layered_contexts_filtered(store):
    """分层检索三出口（always/ondemand/triggered）都只回现行。"""
    store.put_memory(CognitiveMemory(key="k:always", value="old", importance=9))
    store.put_memory(CognitiveMemory(key="k:always", value="new", importance=9))
    store.put_memory(CognitiveMemory(key="k:ond", value="old", importance=5))
    store.put_memory(CognitiveMemory(key="k:ond", value="new", importance=5))
    always = store.get_always_context(max_items=50)
    ond = store.get_ondemand_context("k:ond", max_items=50)
    assert [m.value for m in always if m.key == "k:always"] == ["new"]
    assert [m.value for m in ond if m.key == "k:ond"] == ["new"]


def test_get_memory_by_id_still_works_for_superseded(store):
    """按 id 直查不受过滤影响（历史单条可取证）。"""
    m1 = CognitiveMemory(key="k:id", value="v1", importance=7)
    store.put_memory(m1)
    store.put_memory(CognitiveMemory(key="k:id", value="v2", importance=7))
    got = store.get_memory(m1.id)
    assert got is not None and got.value == "v1"


def test_migration_adds_column_to_legacy_db(tmp_path):
    """存量库（旧 schema 无 superseded_by）打开即补列，旧数据现行可检索。"""
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE l7_cognitive_memories (
        id TEXT PRIMARY KEY, key TEXT NOT NULL, value TEXT,
        source TEXT DEFAULT '', session_id TEXT DEFAULT '',
        importance INTEGER DEFAULT 5, tier TEXT DEFAULT 'triggered',
        okf_type TEXT DEFAULT 'concept', tags TEXT DEFAULT '[]',
        created_at REAL, updated_at REAL, access_count INTEGER DEFAULT 0)""")
    conn.execute(
        "INSERT INTO l7_cognitive_memories (id, key, value, importance, tier, "
        "created_at, updated_at) VALUES ('old1','k:legacy','legacy-v','5',"
        "'triggered',1.0,1.0)")
    conn.commit()
    conn.close()

    s = CognitiveStore(db_path=str(db))
    rows = s.search("k:legacy")
    assert len(rows) == 1 and rows[0].value == "legacy-v"
    # 迁移后新写同 key → 旧条目被标记，检索只剩新条目
    s.put_memory(CognitiveMemory(key="k:legacy", value="new-v", importance=5))
    hits = [m for m in s.search("k:legacy") if m.key == "k:legacy"]
    assert len(hits) == 1 and hits[0].value == "new-v"


def test_different_keys_not_superseded(store):
    """不同 key 互不影响。"""
    store.put_memory(CognitiveMemory(key="k:a", value="a1", importance=7))
    store.put_memory(CognitiveMemory(key="k:b", value="b1", importance=7))
    assert len(store.search("k:")) == 2


# ══════════════════════════════ 件 B：per-project memory ══════════════════════════════


@pytest.fixture()
def mem_env(tmp_path, monkeypatch):
    """记忆文件指到 tmp，默认不存在（= 注入空串）。"""
    target = tmp_path / "proj" / ".lingclaude" / "project_memory.md"
    monkeypatch.setenv(pm.ENV_OVERRIDE, str(target))
    return target


def test_load_missing_file_returns_empty(mem_env):
    assert pm.load_project_memory() == ""


def test_append_and_load_roundtrip(mem_env):
    assert pm.append_project_memory("proxy3 端口是 9091") is True
    text = pm.load_project_memory()
    assert "project_memory.md" in text
    assert "proxy3 端口是 9091" in text
    assert "[20" in text  # 日期戳行


def test_append_sanitizes_newlines(mem_env):
    pm.append_project_memory("第一行\n第二行\t制表")
    text = pm.load_project_memory()
    assert "\n第二行" not in text  # 单行压平


def test_disabled_via_env(mem_env, monkeypatch):
    pm.append_project_memory("存在但禁用")
    monkeypatch.setenv(pm.ENV_OVERRIDE, "off")
    assert pm.load_project_memory() == ""
    monkeypatch.setenv(pm.ENV_OVERRIDE, "0")
    assert pm.load_project_memory() == ""


def test_truncation_guard(mem_env):
    mem_env.parent.mkdir(parents=True, exist_ok=True)
    mem_env.write_text("x" * 6000, encoding="utf-8")
    text = pm.load_project_memory(max_chars=1000)
    assert len(text) < 1200
    assert "截断" in text


def test_corrupt_file_fail_soft(mem_env):
    mem_env.parent.mkdir(parents=True, exist_ok=True)
    mem_env.write_bytes(b"\xff\xfe\x00bad")
    # errors=replace 兜底，绝不抛
    assert isinstance(pm.load_project_memory(), str)


def test_injection_in_dynamic_suffix(mem_env, monkeypatch):
    """注入点契约：文件存在时动态尾随块含项目记忆段，前缀不受影响。"""
    from lingclaude.core.system_prompt_builder import build_dynamic_system_suffix

    pm.append_project_memory("注入契约锚点句")

    class _BM:
        hallucination_risk = 0.0
        frustration_rate = 0.0
        tool_error_rate = 0.0
        corrections_received = 0
        total_turns = 0
        tool_use_rate = 1.0
        tool_error_count = 0

    class _LM:
        def inject_common_to_prompt(self):
            return ""

        def build_context_injection(self, current_query=""):
            return ""

    class _Meta:
        def get_system_prompt_injection(self):
            return ""

    class _D:
        intervention_prompt = ""

    class _DD:
        def diagnose(self):
            return _D()

    suffix = build_dynamic_system_suffix(
        behavior=_BM(), layered_memory=_LM(), meta_cognition=_Meta(),
        messages=[], session_cache_hits=0, dementia_detector=_DD(),
        project_index=None,
    )
    assert "项目记忆" in suffix and "注入契约锚点句" in suffix
