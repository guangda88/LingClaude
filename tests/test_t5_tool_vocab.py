"""T5: ToolRouter 学习词表持久化 — 重启不丢、损坏回退、路径可重定向。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.engine.tools import ToolDefinition
from lingclaude.engine.tool_router import ToolCategory, ToolRouter


def _tool(name: str, desc: str) -> ToolDefinition:
    # 测试内构造直传 handler 不触发（用 handler_name 形态，不依赖已弃用路径）
    return ToolDefinition(
        name=name, description=desc, parameters={"q": {"type": "string"}},
        handler_name=name,
    )


def test_learn_persists_vocab(tmp_path: Path) -> None:
    vocab_file = tmp_path / "tool_vocab.json"
    rt = ToolRouter()
    rt.route("找一下这个文件里的函数", (_tool("grep", "Search file contents with regex"),))
    assert vocab_file.exists(), "学习后应落盘"
    data = json.loads(vocab_file.read_text(encoding="utf-8"))
    assert "learned" in data
    assert any(data["learned"].values()), "learned 词表不应为空"


def test_learned_vocab_survives_restart(tmp_path: Path) -> None:
    vocab_file = tmp_path / "tool_vocab.json"
    t = _tool("zanzibar_grep", "quirky xyzzy search thing")

    rt1 = ToolRouter()
    rt1.route("quirky xyzzy", (t,))  # 学习 quirky/xyzzy → search 类
    learned = json.loads(vocab_file.read_text(encoding="utf-8"))["learned"]

    rt2 = ToolRouter()  # 新实例 = 模拟重启
    assert rt2._vocab[ToolCategory.SEARCH] >= {"quirky", "xyzzy"}, (
        f"重启后 learned 词应从 {learned} 恢复"
    )


def test_corrupt_vocab_file_falls_back_to_seeds(tmp_path: Path) -> None:
    (tmp_path / "tool_vocab.json").write_text("{broken json", encoding="utf-8")
    rt = ToolRouter()  # 不应 raise
    assert "file" in rt._vocab[ToolCategory.FILESYSTEM]


def test_unknown_category_in_file_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "tool_vocab.json").write_text(
        json.dumps({"learned": {"nonexistent_cat": ["x"]}}), encoding="utf-8"
    )
    rt = ToolRouter()  # 枚举外类别静默跳过
    assert "x" not in rt._vocab[ToolCategory.SEARCH]


def test_unwritable_path_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    readonly = tmp_path / "ro"
    readonly.mkdir()
    (readonly / "tool_vocab.json").write_text("{}", encoding="utf-8")
    import os
    os.chmod(readonly, 0o555)
    monkeypatch.setenv("LINGCLAUDE_TOOL_VOCAB_PATH", str(readonly / "tool_vocab.json"))
    try:
        rt = ToolRouter()
        rt.route("read this file", (_tool("read", "Read file contents"),))  # 不应 raise
    finally:
        os.chmod(readonly, 0o755)
