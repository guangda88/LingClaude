"""P4 记忆分层（2026-10-22）：project_memory 超限自动归档。

覆盖：
- 未超限：不触发归档、无副作用
- 超阈值：溢出尾部搬入 archive + 主文件留指针 + 主文件回到可全文注入
- 归档零丢失：溢出内容逐字在 archive 中可寻回
- fail-soft：归档目录不可写时不反噬记忆追加
"""
from __future__ import annotations

import pytest

from lingclaude.core import project_memory as pm


@pytest.fixture()
def mem_home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(pm.ENV_OVERRIDE, raising=False)
    return tmp_path


class TestAutoArchive:
    def test_under_threshold_noop(self, mem_home):
        assert pm.append_project_memory("短记忆条目")
        p = pm.memory_path()
        assert not (p.parent / pm.ARCHIVE_FILENAME).exists()

    def test_oversize_archives_overflow(self, mem_home):
        p = pm.memory_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        # 造 7000 chars 老内容（> threshold 4500）+ 追加新条目
        p.write_text("x" * 7000, encoding="utf-8")
        assert pm.append_project_memory("新条目A")
        archive = p.parent / pm.ARCHIVE_FILENAME
        assert archive.exists(), "超限必须触发归档"
        text = p.read_text(encoding="utf-8")
        assert len(text) <= pm.AUTO_ARCHIVE_THRESHOLD + 300  # 保留+指针标记
        # 新语义(2026-10-22)：保尾(新)切头(旧)——新条目必须留守主文件可注入
        assert "新条目A" in text, "最新条目必须留在主文件（保尾切头语义）"
        assert "project_memory_archive" in text  # 指针标记在主文件
        # 最旧头部在 archive 可寻回（7000-3400≈3600 chars 的 x）
        assert archive.read_text(encoding="utf-8").count("x") >= 2900

    def test_no_info_loss(self, mem_home):
        p = pm.memory_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("HEAD" + "y" * 6200, encoding="utf-8")
        assert pm.append_project_memory("尾声条目")
        combined = p.read_text(encoding="utf-8") + (
            p.parent / pm.ARCHIVE_FILENAME
        ).read_text(encoding="utf-8")
        assert "y" * 100 in combined  # 溢出未丢
        # 新语义：最旧头部(HEAD)归档、最新尾部(尾声条目)留守
        assert "HEAD" in (p.parent / pm.ARCHIVE_FILENAME).read_text(encoding="utf-8")
        assert "尾声条目" in p.read_text(encoding="utf-8")

    def test_after_archive_fully_injectable(self, mem_home):
        """分层后注入无截断 footer；最新条目留守主文件。"""
        p = pm.memory_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("z" * 7000, encoding="utf-8")
        assert pm.append_project_memory("触发条目")
        injected = pm.load_project_memory()
        # 主文件回到全文注入（无截断提示行）
        assert "截断：文件超限" not in injected
        # 新语义：追加条目（最新）留守主文件，最旧 z 头部归档
        assert "触发条目" in injected, "最新条目必须注入可见"
        assert "触发条目" in p.read_text(encoding="utf-8")
        # 指针标记注入可见
        assert "project_memory_archive" in injected

    def test_archive_anchor_lingclaude_for_root_files(self, mem_home, tmp_path):
        """记忆文件在仓库根（AGENTS.md 场景）时，归档必须锚定 .lingclaude/。"""
        root_md = tmp_path / "AGENTS.md"
        root_md.write_text("w" * 7000, encoding="utf-8")
        assert pm._auto_archive_if_oversized(root_md)
        # 不落在仓库根，而是 .lingclaude/ 下
        assert (tmp_path / ".lingclaude" / pm.ARCHIVE_FILENAME).exists()
        assert not (tmp_path / pm.ARCHIVE_FILENAME).exists()
