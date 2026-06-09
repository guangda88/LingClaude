from __future__ import annotations

import pathlib


from lingclaude.engine.file_edit import FileEditTool, ToolContract, EditResult


class TestToolContract:
    def test_contract_defaults(self) -> None:
        c = ToolContract(
            scope="file:edit",
            effect="modifies files",
            rollback="restores from backup",
        )
        assert c.timeout == 30.0
        d = c.to_dict()
        assert d["scope"] == "file:edit"
        assert d["timeout"] == 30.0

    def test_contract_custom_timeout(self) -> None:
        c = ToolContract(
            scope="file:edit",
            effect="modifies files",
            rollback="restores from backup",
            timeout=30.0,
        )
        assert c.timeout == 30.0


class TestEditResult:
    def test_to_dict(self) -> None:
        r = EditResult(
            path="/tmp/test.py",
            lines_added=2,
            lines_removed=1,
            lines_changed=3,
            diff="--- a/test.py\n+++ b/test.py",
            duration=0.01,
            backed_up=True,
            backup_path="/tmp/test.py.bak",
        )
        d = r.to_dict()
        assert d["path"] == "/tmp/test.py"
        assert d["lines_added"] == 2
        assert d["backed_up"] is True


class TestFileEditReplace:
    def test_replace_single_match(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("hello world\nfoo bar\n")
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.replace(str(f), "hello world", "HELLO WORLD")
        assert result.is_ok
        assert "HELLO WORLD" in f.read_text()
        assert result.data.lines_added >= 1
        assert result.data.backed_up is True

    def test_replace_no_match(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("hello world\n")
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.replace(str(f), "nonexistent", "x")
        assert result.is_error
        assert "未找到" in result.error

    def test_replace_multiple_without_flag(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("foo\nfoo\nfoo\n")
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.replace(str(f), "foo", "bar")
        assert result.is_error
        assert "3 处匹配" in result.error

    def test_replace_all(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("foo\nfoo\nfoo\n")
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.replace(str(f), "foo", "bar", replace_all=True)
        assert result.is_ok
        assert f.read_text() == "bar\nbar\nbar\n"

    def test_replace_file_not_found(self, tmp_path: pathlib.Path) -> None:
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.replace(str(tmp_path / "nope.txt"), "a", "b")
        assert result.is_error

    def test_replace_diff_output(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("line1\nline2\nline3\n")
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.replace(str(f), "line2", "LINE2")
        assert result.is_ok
        assert "---" in result.data.diff
        assert "+++" in result.data.diff
        assert "+LINE2" in result.data.diff


class TestFileEditCreate:
    def test_create_new_file(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "new.py"
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.create(str(f), "print('hello')\n")
        assert result.is_ok
        assert f.read_text() == "print('hello')\n"
        assert result.data.lines_added >= 1

    def test_create_existing_file_fails(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "exists.py"
        f.write_text("old")
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.create(str(f), "new content")
        assert result.is_error
        assert "已存在" in result.error

    def test_create_in_subdirectory(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "sub" / "dir" / "new.py"
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.create(str(f), "content")
        assert result.is_ok
        assert f.exists()


class TestFileEditInsert:
    def test_insert_at_beginning(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("line1\nline2\nline3\n")
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.insert(str(f), 0, "inserted")
        assert result.is_ok
        lines = f.read_text().splitlines()
        assert lines[0] == "inserted"

    def test_insert_at_end(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("line1\nline2\n")
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.insert(str(f), 2, "appended")
        assert result.is_ok
        content = f.read_text()
        assert "appended" in content

    def test_insert_out_of_range(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("line1\n")
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.insert(str(f), 5, "text")
        assert result.is_error
        assert "超出范围" in result.error

    def test_insert_file_not_found(self, tmp_path: pathlib.Path) -> None:
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.insert(str(tmp_path / "nope.txt"), 0, "text")
        assert result.is_error


class TestFileEditDeleteLines:
    def test_delete_range(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("a\nb\nc\nd\ne\n")
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.delete_lines(str(f), 1, 3)
        assert result.is_ok
        lines = f.read_text().splitlines()
        assert lines == ["a", "d", "e"]

    def test_delete_single_line(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("a\nb\nc\n")
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.delete_lines(str(f), 1, 2)
        assert result.is_ok
        assert f.read_text() == "a\nc\n"

    def test_delete_invalid_range(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("a\nb\n")
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.delete_lines(str(f), 5, 6)
        assert result.is_error
        assert "超出范围" in result.error


class TestFileEditUndo:
    def test_undo_restores_file(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("original\n")
        tool = FileEditTool(base_dir=str(tmp_path))
        tool.replace(str(f), "original", "modified")
        assert "modified" in f.read_text()
        undo_result = tool.undo(str(f))
        assert undo_result.is_ok
        assert f.read_text() == "original\n"

    def test_undo_no_backup(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("content\n")
        tool = FileEditTool(base_dir=str(tmp_path))
        result = tool.undo(str(f))
        assert result.is_error
        assert "未找到备份" in result.error


class TestFileEditContract:
    def test_contract_is_class_attribute(self) -> None:
        assert FileEditTool.contract.scope == "file:edit"
        assert FileEditTool.contract.rollback != ""
        assert FileEditTool.contract.timeout > 0

    def test_contract_serializable(self) -> None:
        d = FileEditTool.contract.to_dict()
        assert all(k in d for k in ("scope", "effect", "rollback", "timeout"))


class TestFileEditSizeLimit:
    def test_file_too_large(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "big.py"
        f.write_text("x" * 1000)
        tool = FileEditTool(base_dir=str(tmp_path), max_file_size=100)
        result = tool.replace(str(f), "x", "y")
        assert result.is_error
        assert "文件过大" in result.error
