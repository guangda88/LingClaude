"""测试 TUI 图片直接粘贴（2026-10-01）。

覆盖 _handle_paste 三形态识别与零回归：
A. 8-bit 二进制透传（PNG/JPEG magic，utf-8+surrogateescape 与 latin-1 双通道）
B. 单行图片路径 / file:// URI（存在性 + PIL/magic 双验真）
C. base64 / data URI（解码后 magic 复验）
零回归：普通文本/长文本粘贴仍走折叠链，非图片误判不进附件队列。
"""
import base64
from pathlib import Path
import os

import pytest

from lingclaude.cli.full_tui import (
    FullTuiSession,
    _IMAGE_PLACEHOLDER_FMT,
    _PLACEHOLDER_FMT,
)

# 最小「magic 合法」字节串：仅用于形态 A（二进制透传走 magic 嗅探，
# 与剪贴板场景一致——透传字节无法保证完整可解码，magic 是正确校验级）。
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff\xe0"
_MIN_PNG = _PNG_MAGIC + b"\x00\x00\x00\x0dIHDR"
_MIN_JPEG = _JPEG_MAGIC + b"\x00\x20"


def _write_real_png(tmp_path, name="shot.png", size=(8, 8)) -> str:
    """用 PIL 生成完整合法 PNG（形态 B/C 走 PIL verify 严格校验，夹具必须真图）。"""
    from PIL import Image
    p = tmp_path / name
    Image.new("RGB", size).save(p, format="PNG")
    return str(p)


def _noise_png_b64() -> str:
    """大尺寸噪声 PNG 的 base64（>512 字符，过裸 base64 长度门槛）。"""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.frombytes(
        "RGB", (64, 64), os.urandom(64 * 64 * 3)
    ).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _fake_event(data: str) -> object:
    """构造最小 BracketedPaste 事件（只用到 event.data）。"""
    return type("E", (), {"data": data})()


def _fake_session(tmp_path) -> FullTuiSession:
    """绕过 __init__ 的最小方法宿主：只测纯逻辑方法。"""
    s = FullTuiSession.__new__(FullTuiSession)
    s._pending_images = []
    s._image_paste_count = 0
    s._paste_seq = 0
    s._paste_registry = {}
    s._area_lock = __import__("threading").Lock()
    s._output_area = []  # append_output 未启动路径：仅更新缓冲
    s._paste_store_path = Path(tmp_path or "/tmp") / "pastes.json"
    return s


class TestFormABinaryPassthrough:
    """形态 A：终端 8-bit 透传图片字节。"""

    def test_png_bytes_via_surrogateescape_roundtrip(self):
        raw = _MIN_PNG + b"\x80\xff\x00binary-ish"
        data = raw.decode("utf-8", errors="surrogateescape")
        s = _fake_session(None)
        ret = s._try_paste_image(data)
        assert ret is not None and "PNG" in ret
        assert s._pending_images[0][0] == raw  # 字节精确还原
        assert s._pending_images[0][1] == "image/png"

    def test_jpeg_bytes_via_latin1_channel(self):
        # latin-1 全字节 1:1（\xff\xfd 不产生 surrogate，utf-8 通道也能编）
        raw = _MIN_JPEG
        data = raw.decode("latin-1")
        s = _fake_session(None)
        ret = s._try_paste_image(data)
        assert ret is not None and "JPEG" in ret
        assert s._pending_images[0][0] == raw

    def test_gif_magic_recognized(self):
        data = (b"GIF89a" + b"\x00" * 8).decode("latin-1")
        s = _fake_session(None)
        assert s._try_paste_image(data) is not None
        assert s._pending_images[0][1] == "image/gif"

    def test_high_bit_text_not_misdetected(self):
        # 普通含高位字节的文本（如中文 UTF-8）不得误判为图片
        s = _fake_session(None)
        assert s._try_paste_image("这是一段中文粘贴内容") is None
        assert s._pending_images == []


class TestFormBPathReference:
    """形态 B：单行图片路径 / file:// URI。"""

    def test_real_png_path(self, tmp_path):
        p = _write_real_png(tmp_path)
        s = _fake_session(None)
        ret = s._try_paste_image(p + "\n")
        assert ret is not None and "PNG" in ret
        assert len(s._pending_images[0][0]) > 0
        assert s._pending_images[0][1] == "image/png"

    def test_file_uri(self, tmp_path):
        p = _write_real_png(tmp_path, name="截图 测试.png")  # 含空格与非 ASCII，验 unquote
        uri = Path(p).as_uri()  # file:///.../%20...
        s = _fake_session(None)
        ret = s._try_paste_image(uri)
        assert ret is not None
        assert s._pending_images[0][1] == "image/png"

    def test_nonexistent_path_falls_back_to_text(self, tmp_path, capsys):
        s = _fake_session(None)
        ghost = str(tmp_path / "no_such.png")
        ret = s._try_paste_image(ghost)
        # 路径不存在 → 非图片 → 走文本折叠链（返回 None，由调用方折叠）
        assert ret is None

    def test_disguised_text_file_rejected(self, tmp_path):
        # 伪装成 .png 的文本文件：PIL/magic 都过不了 → 按文本粘贴
        p = tmp_path / "fake.png"
        p.write_text("hello, not an image")
        s = _fake_session(None)
        assert s._try_paste_image(str(p)) is None
        assert s._pending_images == []

    def test_multiline_path_not_misdetected(self, tmp_path):
        # 多行文本（恰好第一行像路径）不得走图片链
        p = tmp_path / "real.png"
        p.write_bytes(_MIN_PNG)
        s = _fake_session(None)
        assert s._try_paste_image(f"{p}\n第二行内容") is None


class TestFormCBase64:
    """形态 C：base64 / data URI。"""

    def test_data_uri_png(self):
        b64 = _noise_png_b64()
        s = _fake_session(None)
        ret = s._try_paste_image(f"data:image/png;base64,{b64}")
        assert ret is not None
        assert s._pending_images[0][1] == "image/png"

    def test_bare_base64_png(self):
        b64 = _noise_png_b64()
        assert len(b64) > 512  # 门槛自检
        s = _fake_session(None)
        ret = s._try_paste_image(b64)
        assert ret is not None
        assert s._pending_images[0][1] == "image/png"

    def test_broken_base64_falls_back(self):
        s = _fake_session(None)
        # 长单行但解码失败 → None（回退文本链）
        assert s._try_paste_image("a" * 600) is None

    def test_short_base64_ignored(self):
        # 短串（<=512）不进裸 base64 判定（防普通短文本误判）
        b64 = base64.b64encode(b"\x89PNG").decode("ascii")
        s = _fake_session(None)
        assert s._try_paste_image(b64) is None


class TestZeroRegression:
    """零回归：非图片粘贴行为与旧版完全一致。"""

    def test_long_text_still_folds(self):
        s = _fake_session(None)
        text = "line\n" * 50
        # _try_paste_image 返回 None 后 _handle_paste 走 _register_paste
        insert = s._try_paste_image(text)
        assert insert is None
        placeholder, lines = s._register_paste(text)
        assert placeholder == _PLACEHOLDER_FMT.format(n=1, lines=51, chars=250)
        assert lines == 51  # 既有实现：尾随换行计为新行（既有行为，非本特性引入）

    def test_placeholder_format_contains_fields(self):
        fmt = _IMAGE_PLACEHOLDER_FMT.format(n=3, fmt="PNG", kb=42)
        assert "[图片 #3" in fmt and "42KB" in fmt

    def test_register_increments_pending(self):
        s = _fake_session(None)
        s._register_image_attachment(_MIN_PNG, "image/png")
        s._register_image_attachment(_MIN_JPEG, "image/jpeg")
        assert s.pending_image_count() == 2
        assert s._image_paste_count == 2
