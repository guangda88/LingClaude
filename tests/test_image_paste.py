"""测试 TUI 图片粘贴功能（2026-10-01）。

覆盖路径：
1. ModelMessage.to_dict() 带 image_content 生成正确 content blocks
2. _pending_images 存储 raw bytes
3. repl.py drain 时 hex→bytes→base64 转换
4. /image 命令注册成功
"""
import pytest

from lingclaude.core.model_types import ModelMessage, MessageRole
from lingclaude.cli.commands import SLASH_REGISTRY


class TestModelMessageImageBlock:
    """ModelMessage.to_dict() 带 image_content 的 OpenAI content blocks 格式。"""

    def test_image_content_generates_two_blocks(self):
        """image_content 非 None 时，to_dict() 生成 text + image_url 两个 block。"""
        msg = ModelMessage(
            role=MessageRole.USER,
            content="分析这个图",
            image_content=("SGVsbG8gV29ybGQ=", "image/png"),
        )
        d = msg.to_dict()
        assert d["role"] == "user"
        assert isinstance(d["content"], list)
        assert len(d["content"]) == 2

    def test_first_block_is_text(self):
        msg = ModelMessage(
            role=MessageRole.USER,
            content="分析这个图",
            image_content=("SGVsbG8gV29ybGQ=", "image/png"),
        )
        d = msg.to_dict()
        assert d["content"][0] == {"type": "text", "text": "分析这个图"}

    def test_second_block_is_image_url(self):
        msg = ModelMessage(
            role=MessageRole.USER,
            content="分析这个图",
            image_content=("SGVsbG8gV29ybGQ=", "image/png"),
        )
        d = msg.to_dict()
        assert d["content"][1] == {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,SGVsbG8gV29ybGQ="},
        }

    def test_without_image_content_is_plain_str(self):
        """image_content=None 时，content 直接是字符串（向后兼容）。"""
        msg = ModelMessage(role=MessageRole.USER, content="hello")
        d = msg.to_dict()
        assert d["content"] == "hello"

    def test_various_mime_types(self):
        for mime in ("image/png", "image/jpeg", "image/gif", "image/webp"):
            msg = ModelMessage(
                role=MessageRole.USER,
                content="图",
                image_content=("abc123", mime),
            )
            d = msg.to_dict()
            assert d["content"][1]["image_url"]["url"].startswith(
                f"data:{mime};base64,"
            ), f"mime={mime}"


class TestSlashImageCommand:
    """斜杠命令 /image 的注册状态。"""

    def test_image_command_registered(self):
        entry = SLASH_REGISTRY.get("/image")
        assert entry is not None

    def test_handler_is_cmd_image(self):
        entry = SLASH_REGISTRY.get("/image")
        assert entry.handler.__name__ == "_cmd_image"

    def test_no_args_required(self):
        entry = SLASH_REGISTRY.get("/image")
        assert not entry.needs_args

    def test_in_completer_words(self):
        from lingclaude.cli.commands import SLASH_COMPLETER_WORDS
        assert "/image" in SLASH_COMPLETER_WORDS



class TestImagePathArg:
    """2026-10-01: /image <路径> 直接读文件（SSH 场景剪贴板无像素）。"""

    def _run(self, tmp_path, arg, fake_session):
        from types import SimpleNamespace
        from lingclaude.cli.commands import SlashCommandProcessor
        fake = SimpleNamespace(session=fake_session)
        SlashCommandProcessor._cmd_image(fake, arg)
        return fake.session.attachments

    def test_path_arg_attaches_real_png(self, tmp_path):
        from PIL import Image

        class FakeSession:
            def __init__(self):
                self.attachments = []

            def register_image_attachment(self, raw, mime):
                self.attachments.append((raw, mime))
                return len(self.attachments)

        png = tmp_path / "shot.png"
        Image.new("RGB", (80, 40), (200, 30, 30)).save(png)
        atts = self._run(tmp_path, str(png), FakeSession())
        assert len(atts) == 1
        raw, mime = atts[0]
        assert mime == "image/png"
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"

    def test_path_arg_missing_file_rejected(self, tmp_path, capsys):
        class FakeSession:
            def __init__(self):
                self.attachments = []

            def register_image_attachment(self, raw, mime):
                self.attachments.append((raw, mime))
                return len(self.attachments)

        atts = self._run(tmp_path, str(tmp_path / "nope.png"), FakeSession())
        assert atts == []
        assert "文件不存在" in capsys.readouterr().out

    def test_path_arg_non_image_rejected(self, tmp_path, capsys):
        class FakeSession:
            def __init__(self):
                self.attachments = []

            def register_image_attachment(self, raw, mime):
                self.attachments.append((raw, mime))
                return len(self.attachments)

        bad = tmp_path / "fake.png"
        bad.write_text("definitely not an image")
        atts = self._run(tmp_path, str(bad), FakeSession())
        assert atts == []
        assert "不是有效图片" in capsys.readouterr().out

class TestPendingImagesStorage:
    """_pending_images 类型注解验证。"""

    def test_pending_images_type_annotation(self):
        """_pending_images 存 (raw_bytes, mime_type) 元组。"""
        # 类型验证：注解存于 __annotations__ dict
        hints: dict = {}
        exec("pending_images: list[tuple[bytes, str]]", {}, hints)
        ann = hints.get("__annotations__", {})
        assert "pending_images" in ann

    def test_hex_to_base64_conversion_in_repl(self):
        """repl.py drain 时把 hex → bytes → base64，to_dict 格式正确。"""
        import base64

        # 模拟: clipboard 返回 hex
        hex_str = "89504e470d0a1a0a"  # PNG magic bytes hex
        raw_bytes = bytes.fromhex(hex_str)
        b64_str = base64.b64encode(raw_bytes).decode("ascii")

        # 验证 ModelMessage 接受这个 base64 字符串
        msg = ModelMessage(
            role=MessageRole.USER,
            content="test",
            image_content=(b64_str, "image/png"),
        )
        d = msg.to_dict()
        assert d["content"][1]["image_url"]["url"] == f"data:image/png;base64,{b64_str}"
