"""C(2026-09-26) 富历史条目（粘贴跨会话重水化）测试。

根因回归锁定：FileHistory 里的历史行含占位符（[文本块 #N …]），而
_paste_registry 纯内存——重启后上键召回旧条目直接提交，占位符字面
发给模型。修复：注册表落盘 pastes.json + 启动回灌 + seq 续接。

覆盖：
1. _register_paste 落盘（写 pastes.json）
2. 新会话 _load_paste_store 回灌（占位符可还原 = 富历史）
3. _paste_seq 续接（新粘贴编号不冲突）
4. 损坏/缺失文件静默（不反噬）
5. 上限裁剪（_PASTE_STORE_MAX 条）
"""

from __future__ import annotations

import json
from pathlib import Path

from lingclaude.cli.full_tui import (
    _PASTE_STORE_MAX,
    _PLACEHOLDER_RE,
    FullTuiSession,
)


def _paste_lines(n: int) -> str:
    return "\n".join(f"paste-line-{i}" for i in range(n))


class TestPasteStore:
    def test_register_persists(self, tmp_path: Path) -> None:
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        store = tmp_path / "pastes.json"
        assert not store.exists()
        placeholder, lines = s._register_paste(_paste_lines(10))  # noqa: SLF001
        assert lines == 10
        assert store.exists()
        data = json.loads(store.read_text(encoding="utf-8"))
        assert data["pastes"]["1"] == [_paste_lines(10), 10]

    def test_reload_rehydrates_registry(self, tmp_path: Path) -> None:
        """重启模拟：新会话回灌注册表 → 占位符能还原全文（富历史核心）。"""
        hist = str(tmp_path / "h")
        s1 = FullTuiSession(history_file=hist)
        ph, _ = s1._register_paste(_paste_lines(8))  # noqa: SLF001

        s2 = FullTuiSession(history_file=hist)
        assert s2._paste_registry == {1: (_paste_lines(8), 8)}  # noqa: SLF001
        # 占位符 → 全文（与提交前 _expand_placeholders 同路径）
        expanded = s2._expand_placeholders(f"看这段：{ph} 谢谢")  # noqa: SLF001
        assert expanded == f"看这段：{_paste_lines(8)} 谢谢"

    def test_seq_continues_after_reload(self, tmp_path: Path) -> None:
        """回灌后 _paste_seq 续接最大编号——新粘贴不与历史编号冲突。"""
        hist = str(tmp_path / "h")
        s1 = FullTuiSession(history_file=hist)
        for i in range(3):
            s1._register_paste(_paste_lines(6 + i))  # noqa: SLF001
        s2 = FullTuiSession(history_file=hist)
        assert s2._paste_seq == 3  # noqa: SLF001
        ph, _ = s2._register_paste(_paste_lines(6))  # noqa: SLF001
        assert "#4" in ph
        data = json.loads((tmp_path / "pastes.json").read_text("utf-8"))
        assert "4" in data["pastes"]

    def test_corrupt_store_silent(self, tmp_path: Path) -> None:
        (tmp_path / "pastes.json").write_text("{not json", encoding="utf-8")
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        assert s._paste_registry == {}  # noqa: SLF001
        assert s._paste_seq == 0  # noqa: SLF001

    def test_missing_store_silent(self, tmp_path: Path) -> None:
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        assert s._paste_registry == {}  # noqa: SLF001

    def test_store_cap_drops_oldest(self, tmp_path: Path) -> None:
        """回灌只取最新 _PASTE_STORE_MAX 条（防 pastes.json 无限膨胀）。"""
        hist = str(tmp_path / "h")
        store = tmp_path / "pastes.json"
        n = _PASTE_STORE_MAX + 10
        pastes = {str(i): [_paste_lines(6), 6] for i in range(1, n + 1)}
        store.write_text(json.dumps({"max_seq": n, "pastes": pastes}),
                         encoding="utf-8")
        s = FullTuiSession(history_file=hist)
        assert len(s._paste_registry) == _PASTE_STORE_MAX  # noqa: SLF001
        assert min(s._paste_registry) == 11  # noqa: SLF001  丢最旧 10 条
        assert s._paste_seq == n  # noqa: SLF001  编号续接不回退

    def test_oversized_entry_skipped(self, tmp_path: Path) -> None:
        """单条 >1MiB 丢弃（防病态条目撑爆内存）。"""
        hist = str(tmp_path / "h")
        store = tmp_path / "pastes.json"
        big = "z" * (1_048_577)
        store.write_text(json.dumps(
            {"max_seq": 1, "pastes": {"1": [big, 6]}}), encoding="utf-8")
        s = FullTuiSession(history_file=hist)
        assert 1 not in s._paste_registry  # noqa: SLF001

    def test_placeholder_re_shape_stable(self, tmp_path: Path) -> None:
        """占位符格式未变（旧 FileHistory 条目的占位符仍可被还原正则命中）。"""
        hist = str(tmp_path / "h")
        s = FullTuiSession(history_file=hist)
        ph, _ = s._register_paste(_paste_lines(7))  # noqa: SLF001
        assert _PLACEHOLDER_RE.fullmatch(ph) is not None
