"""P1-2（会话问题重构 2026-09-15）: 会话项目隔离 + /session 摘要 + 历史隔离 验收。

背景 Q5/Q6：
- 会话此前存 ~/.lingclaude/sessions/ 但 persist 不传 project_path → 全落
  _default/，-continue 取全局最近（跨项目泄露）
- 命令历史 ~/.lingclaude/history 全局共享（在 A 项目输入的命令在 B 项目
  按上键还原）

本测试验证三件事：
1. persist_session 保存带 project_path=os.getcwd()，list_sessions(project_path)
   能按目录过滤（不同目录的会话互不可见）
2. _list_sessions_in 生成摘要字段（前 3 条对话），/session list 可识别内容
3. DEFAULT_HISTORY_FILE 移到项目内（相对路径 .lingclaude/history）
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from lingclaude.cli.interface import DEFAULT_HISTORY_FILE
from lingclaude.core.session import Session, SessionManager
from lingclaude.core.session_persist import SessionPersister


class _FakeEngine:
    """模拟 QueryEngine 的最小对象（session_persist 只用这些字段）。"""

    def __init__(self, session_id: str, messages: list[str]) -> None:
        self.session_id = session_id
        self._messages = messages
        self._usage = MagicMock()
        self._usage.input_tokens = 10
        self._usage.output_tokens = 20
        self.session_manager = SessionManager()


class TestSessionProjectIsolation:
    def test_persist_carries_project_path(self, tmp_path: Path, monkeypatch: Any) -> None:
        """persist_session 保存的 Session 带 project_path=os.getcwd()。"""
        # 隔离：固定 cwd 到 tmp_path 下（不污染真实环境）
        fake_cwd = tmp_path / "project_a"
        fake_cwd.mkdir()
        monkeypatch.chdir(fake_cwd)
        # SessionManager 用临时目录（不碰 ~/.lingclaude）
        mgr = SessionManager(save_dir=tmp_path / "sessions")
        engine = _FakeEngine("proj_a_001", ["第一条消息", "第二条"])
        engine.session_manager = mgr
        persister = SessionPersister(engine)  # type: ignore[arg-type]

        result = persister.persist_session()
        assert result.is_ok
        # 落盘文件里 project_path 必须是当前 cwd
        saved = list((tmp_path / "sessions").glob("*.json"))[0]
        data = json.loads(saved.read_text())
        assert data["project_path"] == str(fake_cwd)

    def test_list_sessions_filters_by_project(self, tmp_path: Path, monkeypatch: Any) -> None:
        """不同 project_path 的会话互不可见（list_sessions 按目录过滤）。

        需用 global 模式（生产默认）：非 global 模式（注入 save_dir）是隔离
        临时目录用途，不做项目过滤。monkeypatch _global_sessions_root 到
        临时目录，避免污染 ~/.lingclaude。
        """
        monkeypatch.setattr(
            "lingclaude.core.session._global_sessions_root",
            lambda: tmp_path / "sessions",
        )
        mgr = SessionManager()  # global 模式（生产默认）
        # 项目 A 的会话
        mgr.save(Session(
            session_id="sess_a", messages=("A 的消息",),
            input_tokens=1, output_tokens=1, project_path=str(tmp_path / "proj_a"),
        ))
        # 项目 B 的会话
        mgr.save(Session(
            session_id="sess_b", messages=("B 的消息",),
            input_tokens=1, output_tokens=1, project_path=str(tmp_path / "proj_b"),
        ))

        # 按 A 过滤只看到 A
        list_a = mgr.list_sessions(project_path=str(tmp_path / "proj_a"))
        ids_a = {s["session_id"] for s in list_a}
        assert ids_a == {"sess_a"}
        assert "sess_b" not in ids_a

        # 按 B 过滤只看到 B
        list_b = mgr.list_sessions(project_path=str(tmp_path / "proj_b"))
        ids_b = {s["session_id"] for s in list_b}
        assert ids_b == {"sess_b"}

    def test_load_sessions_by_project_path(self, tmp_path: Path) -> None:
        """load(session_id, project_path) 精确定位项目内会话。"""
        mgr = SessionManager(save_dir=tmp_path / "sessions")
        mgr.save(Session(
            session_id="sess_x", messages=("X",), input_tokens=0, output_tokens=0,
            project_path=str(tmp_path / "proj_x"),
        ))
        result = mgr.load("sess_x", project_path=str(tmp_path / "proj_x"))
        assert result.is_ok
        assert result.data.session_id == "sess_x"


class TestSessionSummary:
    def test_list_includes_summary_from_messages(self, tmp_path: Path) -> None:
        """_list_sessions_in 生成摘要（前 3 条非空文本）。"""
        mgr = SessionManager(save_dir=tmp_path / "sessions")
        mgr.save(Session(
            session_id="sess_sum", messages=(
                "修复 git push 错误", "/model 切换", "第二条指令", "第三条",
            ),
            input_tokens=0, output_tokens=0, project_path=str(tmp_path / "proj"),
        ))
        sessions = mgr.list_sessions(project_path=str(tmp_path / "proj"))
        assert len(sessions) == 1
        summary = sessions[0]["summary"]
        # 摘要含前 3 条对话（斜杠命令跳过），用 | 拼接
        assert "修复 git push 错误" in summary
        assert "第二条指令" in summary
        assert "第三条" in summary
        # 斜杠命令不进摘要
        assert "/model" not in summary

    def test_empty_session_summary(self, tmp_path: Path) -> None:
        """空会话摘要为 (空会话)。"""
        mgr = SessionManager(save_dir=tmp_path / "sessions")
        mgr.save(Session(
            session_id="sess_empty", messages=(), input_tokens=0, output_tokens=0,
            project_path=str(tmp_path / "proj"),
        ))
        sessions = mgr.list_sessions(project_path=str(tmp_path / "proj"))
        assert sessions[0]["summary"] == "(空会话)"


class TestHistoryProjectIsolation:
    def test_default_history_file_is_project_local(self) -> None:
        """DEFAULT_HISTORY_FILE 是项目内相对路径（.lingclaude/history），
        不再指向全局 ~/.lingclaude/history（跨项目泄露根因）。"""
        assert DEFAULT_HISTORY_FILE == ".lingclaude/history"
        # 相对路径：不展开到 home（Path("~").expanduser 才会）
        assert not str(Path(DEFAULT_HISTORY_FILE)).startswith(str(Path.home()))
