from __future__ import annotations

import pathlib

from lingclaude.engine.file_read import FileReadTool, ReadResult


class TestFileReadBasic:
    def test_read_file_with_line_numbers(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("line1\nline2\nline3\n")
        tool = FileReadTool(base_dir=str(tmp_path))
        result = tool.read(str(f))
        assert result.is_ok
        assert "line1" in result.data.content
        assert result.data.lines == 3
        assert result.data.size > 0

    def test_read_file_not_found(self, tmp_path: pathlib.Path) -> None:
        tool = FileReadTool(base_dir=str(tmp_path))
        result = tool.read(str(tmp_path / "nope.txt"))
        assert result.is_error
        assert "不存在" in result.error

    def test_read_directory_fails(self, tmp_path: pathlib.Path) -> None:
        d = tmp_path / "subdir"
        d.mkdir()
        tool = FileReadTool(base_dir=str(tmp_path))
        result = tool.read(str(d))
        assert result.is_error
        assert "不是文件" in result.error

    def test_read_binary_file_rejected(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "data.sqlite"
        f.write_bytes(b"\x00\x01\x02")
        tool = FileReadTool(base_dir=str(tmp_path))
        result = tool.read(str(f))
        assert result.is_error
        assert "二进制" in result.error


class TestFileReadOffsetLimit:
    def test_read_with_offset(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("a\nb\nc\nd\ne\n")
        tool = FileReadTool(base_dir=str(tmp_path))
        result = tool.read(str(f), offset=2)
        assert result.is_ok
        assert "c" in result.data.content
        assert "a" not in result.data.content

    def test_read_with_limit(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("a\nb\nc\nd\ne\n")
        tool = FileReadTool(base_dir=str(tmp_path))
        result = tool.read(str(f), limit=2)
        assert result.is_ok
        content = result.data.content
        assert "a" in content
        assert "b" in content
        assert "e" not in content
        assert result.data.truncated is True

    def test_read_with_offset_and_limit(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("a\nb\nc\nd\ne\n")
        tool = FileReadTool(base_dir=str(tmp_path))
        result = tool.read(str(f), offset=1, limit=2)
        assert result.is_ok
        content = result.data.content
        assert "b" in content
        assert "c" in content
        assert "d" not in content

    def test_read_offset_beyond_file(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("a\nb\n")
        tool = FileReadTool(base_dir=str(tmp_path))
        result = tool.read(str(f), offset=100)
        assert result.is_ok
        assert result.data.lines == 2


class TestFileReadNoLineNumbers:
    def test_read_without_line_numbers(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("hello\nworld\n")
        tool = FileReadTool(base_dir=str(tmp_path))
        result = tool.read(str(f), line_numbers=False)
        assert result.is_ok
        assert result.data.content == "hello\nworld"


class TestFileReadImage:
    def test_read_image_returns_base64(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        tool = FileReadTool(base_dir=str(tmp_path))
        result = tool.read(str(f))
        assert result.is_ok
        assert result.data.is_image is True
        assert result.data.image_mime == "image/png"

    def test_read_image_via_read_image_method(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)
        tool = FileReadTool(base_dir=str(tmp_path))
        result = tool.read_image(str(f))
        assert result.is_ok
        assert result.data.is_image is True

    def test_read_image_not_image_file(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("hello")
        tool = FileReadTool(base_dir=str(tmp_path))
        result = tool.read_image(str(f))
        assert result.is_error
        assert "不是图像" in result.error


class TestFileReadSizeLimit:
    def test_file_too_large(self, tmp_path: pathlib.Path) -> None:
        f = tmp_path / "big.txt"
        f.write_text("x" * 1000)
        tool = FileReadTool(base_dir=str(tmp_path), max_file_size=100)
        result = tool.read(str(f))
        assert result.is_error
        assert "文件过大" in result.error


class TestFileReadContract:
    def test_read_contract_exists(self) -> None:
        assert FileReadTool.contract.scope == "file:read"
        assert FileReadTool.contract.rollback == "No rollback needed — read-only operation"
        assert FileReadTool.contract.timeout == 5.0


class TestReadResultToDict:
    def test_text_result_dict(self) -> None:
        r = ReadResult(
            path="/tmp/test.py",
            content="hello",
            size=5,
            lines=1,
            offset=0,
            limit=10,
        )
        d = r.to_dict()
        assert "content" in d
        assert "is_image" not in d
        assert d["lines"] == 1

    def test_image_result_dict(self) -> None:
        r = ReadResult(
            path="/tmp/test.png",
            content="base64data",
            size=100,
            lines=0,
            is_image=True,
            image_mime="image/png",
        )
        d = r.to_dict()
        assert d["is_image"] is True
        assert "content" not in d
        assert d["image_mime"] == "image/png"
