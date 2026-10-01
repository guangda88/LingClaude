"""session_index 测试（2026-10-01 sqlite 索引层）。

覆盖：upsert/查询/自愈重建/污染目录排除/fork 收编/索引损坏回退。
隔离原则：全部走 tmp_path 注入 save_dir，不触碰真实 ~/.lingclaude/sessions。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from lingclaude.core.session import Session, SessionManager
from lingclaude.core.session_index import (
    NON_PROJECT_DIRS,
    SessionIndex,
    sessions_db_path,
)


def _mk_session(sid: str, msgs: tuple[str, ...] = ("你好", "world"),
                project_path: str = "/tmp/proj_a", created_at: str = "") -> Session:
    kw: dict = dict(
        session_id=sid, messages=msgs, input_tokens=10, output_tokens=5,
        project_path=project_path, project_name=Path(project_path).name,
    )
    if created_at:
        kw["created_at"] = created_at
    return Session(**kw)


@pytest.fixture()
def mgr(tmp_path: Path) -> SessionManager:
    return SessionManager(save_dir=tmp_path / "sessions")


# ---------- 基础：save 即入索引 ----------

def test_save_upserts_index(mgr: SessionManager):
    s = _mk_session("aaaa1111", created_at="2026-10-01T10:00:00")
    r = mgr.save(s)
    assert r.is_ok
    idx = SessionIndex(mgr.save_dir)
    rows = idx.list_rows(all_projects=True)
    assert rows is not None and len(rows) == 1
    assert rows[0]["session_id"] == "aaaa1111"
    assert rows[0]["project_dir"] == ""  # 非全局模式落 save_dir 根，无项目分层


def test_upsert_is_idempotent_on_same_id(mgr: SessionManager):
    mgr.save(_mk_session("aaaa2222", msgs=("v1",), created_at="2026-10-01T10:00:00"))
    mgr.save(_mk_session("aaaa2222", msgs=("v2 更新",), created_at="2026-10-01T10:00:00"))
    idx = SessionIndex(mgr.save_dir)
    rows = idx.list_rows(all_projects=True)
    assert len(rows) == 1
    assert "v2" in rows[0]["summary"]


# ---------- 汇总口径 ----------

def test_summary_skips_slash_commands_and_shortcuts(mgr: SessionManager):
    mgr.save(_mk_session("aaaa3333", msgs=("/policy reload", "  ", "第一条真人消息", "第二条"),
                         created_at="2026-10-01T10:00:00"))
    rows = SessionIndex(mgr.save_dir).list_rows(all_projects=True)
    assert rows[0]["summary"].startswith("第一条真人消息")


# ---------- 查询：项目过滤 + default 孤儿口径 ----------

def test_list_rows_project_filter_with_default_orphans(mgr: SessionManager):
    mgr.save(_mk_session("bbbb1111", project_path="/tmp/proj_a", created_at="2026-10-01T10:00:00"))
    # 孤儿：project_path 为空，落 _default（P1-2 历史口径）
    orphan = _mk_session("bbbb2222", project_path="", created_at="2026-10-01T09:00:00")
    mgr.save(orphan)
    # 有归属的 _default 会话不算孤儿
    owned = _mk_session("bbbb3333", project_path="", created_at="2026-10-01T08:00:00")
    object.__setattr__  # noop，保持可读
    s3 = Session(session_id="bbbb3333", messages=("x",), input_tokens=0, output_tokens=0,
                 project_path="", project_name="",
                 created_at="2026-10-01T08:00:00")
    # 直接写 json 到 _default 且带 project_path（模拟旧数据）
    d = mgr.save_dir / "_default"
    d.mkdir(parents=True, exist_ok=True)
    payload = owned.to_dict_redacted()
    payload["project_path"] = "/tmp/other"
    (d / "bbbb3333.json").write_text(json.dumps(payload))
    SessionIndex(mgr.save_dir).upsert("bbbb3333", d / "bbbb3333.json", payload)

    rows = SessionIndex(mgr.save_dir).list_rows(
        project_dir="proj_a", include_default_orphans=True)
    ids = {r["session_id"] for r in rows}
    assert ids == {"bbbb1111", "bbbb2222"}  # b333 有归属不入


# ---------- 自愈重建 ----------

def test_rebuild_from_json_after_index_loss(mgr: SessionManager):
    mgr.save(_mk_session("cccc1111", created_at="2026-10-01T10:00:00"))
    mgr.save(_mk_session("cccc2222", created_at="2026-10-01T11:00:00"))
    db = sessions_db_path(mgr.save_dir)
    db.unlink()  # 模拟索引丢失（手动 rm / 损坏清理）
    assert not db.exists()
    idx = SessionIndex(mgr.save_dir)
    rows = idx.list_rows(all_projects=True)  # 空索引 → None → 触发上层重建
    assert rows is None
    assert idx.ensure_ready()  # 重建
    rows = idx.list_rows(all_projects=True)
    assert rows is not None and {r["session_id"] for r in rows} == {"cccc1111", "cccc2222"}


def test_rebuild_excludes_pollution_dirs(mgr: SessionManager):
    mgr.save(_mk_session("dddd1111", project_path="/tmp/proj_a"))
    # 模拟 StateStore 双写落进 sessions 根的 record_type 目录
    poll = mgr.save_dir / "session"
    poll.mkdir(parents=True, exist_ok=True)
    (poll / "dddd1111.json").write_text(json.dumps({"session_id": "dddd1111", "messages": ["污染"]}))
    n = SessionIndex(mgr.save_dir).rebuild_from_json()
    assert n == 1  # 只收真项目
    conn = sqlite3.connect(str(sessions_db_path(mgr.save_dir)))
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE project_dir='session'").fetchone()[0] == 0


# ---------- list_sessions 集成：快路径 + json 兜底一致性 ----------

def test_list_sessions_index_and_json_views_agree(mgr: SessionManager):
    """非 global（注入 save_dir）模式：list_sessions 走 legacy json 扫描
    （不过滤项目），索引存在与否不影响结果——这才是两视图真正的一致性契约。"""
    mgr.save(_mk_session("eeee1111", project_path="/tmp/proj_a", created_at="2026-10-01T10:00:00"))
    mgr.save(_mk_session("eeee2222", project_path="/tmp/proj_b", created_at="2026-10-01T11:00:00"))
    with_index = mgr.list_sessions(project_path="/tmp/proj_a")
    ids_with = {s["session_id"] for s in with_index}
    assert ids_with == {"eeee1111", "eeee2222"}  # legacy 语义：根部全量
    # 删索引 → 结果必须不变（索引只是缓存，非事实源）
    sessions_db_path(mgr.save_dir).unlink()
    mgr._index.close()
    mgr._index = SessionIndex(mgr.save_dir)
    without_index = mgr.list_sessions(project_path="/tmp/proj_a")
    assert {s["session_id"] for s in without_index} == ids_with


def test_list_sessions_global_mode_project_filter(tmp_path: Path):
    """global 模式（生产路径）：快路径按项目目录过滤 + default 孤儿并入。"""
    save_dir = tmp_path / "gsessions"
    mgr = SessionManager(save_dir=save_dir)
    mgr._global_mode = True  # 走生产布局：save_dir/<proj>/*.json
    _save_global(mgr, "gggg1111", proj="proj_a", created_at="2026-10-01T10:00:00")
    _save_global(mgr, "gggg2222", proj="proj_b", created_at="2026-10-01T11:00:00")
    rows = mgr.list_sessions(project_path="/somewhere/proj_a")
    assert {s["session_id"] for s in rows} == {"gggg1111"}


def _save_global(mgr: SessionManager, sid: str, proj: str, created_at: str) -> None:
    """global 布局直写：save_dir/<proj>/<sid>.json + 索引同步。"""
    import json as _json
    d = mgr.save_dir / proj
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": sid, "messages": [f"msg of {sid}"],
        "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
        "created_at": created_at, "project_path": f"/somewhere/{proj}",
        "project_name": proj,
    }
    (d / f"{sid}.json").write_text(_json.dumps(payload))
    mgr._index.upsert(sid, d / f"{sid}.json", payload, project_dir=proj)


def test_list_sessions_excludes_state_record_dir(mgr: SessionManager):
    mgr.save(_mk_session("ffff1111", project_path="/tmp/proj_a"))
    poll = mgr.save_dir / "session"
    poll.mkdir(parents=True, exist_ok=True)
    (poll / "ffff1111.json").write_text(json.dumps({"session_id": "ffff1111"}))
    rows = mgr.list_sessions()  # 全项目列举
    names = {s["project_name"] for s in rows}
    assert "session" not in names
    assert all(s["session_id"] != "ffff1111" or s["project_path"] == "/tmp/proj_a" for s in rows)


# ---------- fork 收编（线程写锁配套语义回归） ----------

def test_fork_session_gets_own_index_row(mgr: SessionManager):
    mgr.save(_mk_session("aaaa9999", msgs=("原档",), created_at="2026-10-01T10:00:00"))
    fork = Session(session_id="aaaa9999-fork0a1b2c", messages=("fork 内容",),
                   input_tokens=1, output_tokens=1,
                   project_path="/tmp/proj_a", project_name="proj_a",
                   created_at="2026-10-01T10:30:00")
    mgr.save(fork)
    rows = SessionIndex(mgr.save_dir).list_rows(all_projects=True)
    ids = {r["session_id"] for r in rows}
    assert ids == {"aaaa9999", "aaaa9999-fork0a1b2c"}  # 原档与 fork 并存（不覆盖）


# ---------- 崩溃安全：损坏 db 文件 → 回退 json 扫描 ----------

def test_corrupt_db_falls_back_to_json_scan(mgr: SessionManager, tmp_path: Path):
    mgr.save(_mk_session("eeee9999", project_path="/tmp/proj_a"))
    db = sessions_db_path(mgr.save_dir)
    db.write_bytes(b"this is not sqlite at all" * 10)  # 写坏
    mgr._index.close()
    mgr._index = SessionIndex(mgr.save_dir)
    # connect 时 schema 初始化会失败 → 走 json 扫描兜底，不抛异常
    rows = mgr.list_sessions(project_path="/tmp/proj_a")
    assert {s["session_id"] for s in rows} == {"eeee9999"}


# ---------- 常量契约 ----------

def test_non_project_dirs_constant():
    assert "session" in NON_PROJECT_DIRS
    assert "_index" in NON_PROJECT_DIRS


# ---------- delete 同步删索引（孤儿行防复活） ----------

def test_delete_removes_index_row(mgr: SessionManager):
    mgr.save(_mk_session("hhhh1111", project_path="/tmp/proj_a"))
    idx = SessionIndex(mgr.save_dir)
    assert idx.list_rows(all_projects=True) is not None
    r = mgr.delete("hhhh1111")
    assert r.is_ok
    idx.ensure_ready()
    # 主档删除（session/ 下的是 StateStore record json，J4 设计与主档生命周期
    # 解耦，不在 delete 契约内——只断言主档消失 + 索引无孤儿行复活）。
    assert not (mgr.save_dir / "hhhh1111.json").exists()
    assert mgr.list_sessions(project_path="/tmp/proj_a") == ()
