"""瘦身读测试（2026-09-23）。

覆盖分层契约的三面:
1. handler 层: 模型不传 limit → 默认 200 行窗口 + _read_hint 续读提示;
   显式 limit / limit=0 行为不变。
2. 库层契约不变: FileReadTool limit=None 仍=全读（下游/MCP 依赖）。
3. 管线层不变: pipeline spill pruner 仍在位（16KB 落盘 locator）。
"""

from pathlib import Path

from lingclaude.engine.tool_handlers.file_tools import FileToolsMixin
from lingclaude.engine.file_read import FileReadTool


class _Harness(FileToolsMixin):
    """最小宿主: 只提供 mixin 依赖的两个属性。"""

    def __init__(self, base_dir: str):
        self.file_read = FileReadTool(base_dir=base_dir)

    def _gate_sensitive(self, op: str, path: str):
        return None  # 测试场景不做敏感路径门禁


def _make_file(tmp_path: Path, n_lines: int) -> str:
    p = tmp_path / "sample.py"
    p.write_text("\n".join(f"line_{i}" for i in range(1, n_lines + 1)), encoding="utf-8")
    return str(p)


def test_small_file_unaffected(tmp_path):
    """小于窗口的文件: 无 limit 读全文, 不出提示。"""
    h = _Harness(str(tmp_path))
    p = _make_file(tmp_path, 20)
    r = h._read_handler(path=p)
    assert not r.is_error
    assert r.data["lines"] == 20
    assert r.data["content"].count("line_") == 20
    assert "_read_hint" not in r.data


def test_large_file_default_window_and_hint(tmp_path):
    """500 行文件无 limit: 只回 200 行 + 续读提示指明 offset=200。"""
    h = _Harness(str(tmp_path))
    p = _make_file(tmp_path, 500)
    r = h._read_handler(path=p)
    assert not r.is_error
    assert r.data["lines"] == 500  # 元数据如实报总行数
    assert "line_200" in r.data["content"]
    assert "line_201" not in r.data["content"]
    hint = r.data["_read_hint"]
    assert "500" in hint and "offset=200" in hint and "limit=0" in hint


def test_explicit_limit_no_hint(tmp_path):
    """显式 limit: 行为与旧版一致, 不注入提示。"""
    h = _Harness(str(tmp_path))
    p = _make_file(tmp_path, 500)
    r = h._read_handler(path=p, limit=50)
    assert not r.is_error
    assert r.data["content"].count("line_") == 50
    assert "_read_hint" not in r.data


def test_limit_zero_reads_full(tmp_path):
    """limit=0 逃生门: 读全文。"""
    h = _Harness(str(tmp_path))
    p = _make_file(tmp_path, 500)
    r = h._read_handler(path=p, limit=0)
    assert not r.is_error
    assert r.data["content"].count("line_") == 500
    assert "_read_hint" not in r.data


def test_offset_continuation_returns_next_window(tmp_path):
    """按提示用 offset 续读: 拿到下一个窗口。"""
    h = _Harness(str(tmp_path))
    p = _make_file(tmp_path, 450)
    r = h._read_handler(path=p, offset=200)
    assert not r.is_error
    assert "line_201" in r.data["content"]
    assert "line_400" in r.data["content"]
    assert "line_401" not in r.data["content"]


def test_exact_window_no_hint(tmp_path):
    """恰好等于窗口: truncated=False, 不出提示。"""
    h = _Harness(str(tmp_path))
    p = _make_file(tmp_path, 200)
    r = h._read_handler(path=p)
    assert not r.is_error
    assert r.data["content"].count("line_") == 200
    assert "_read_hint" not in r.data


def test_library_layer_contract_unchanged(tmp_path):
    """库层契约回归: FileReadTool limit=None 仍=全读（MCP 桥/内部调用方依赖）。"""
    p = _make_file(tmp_path, 500)
    tool = FileReadTool(base_dir=str(tmp_path))
    r = tool.read(p, offset=0, limit=None)
    assert not r.is_error
    assert r.data.content.count("line_") == 500
    assert r.data.truncated is False


def test_pipeline_spill_pruner_still_in_place():
    """管线层哨兵: pipeline spill pruner 未被本次清理误删。"""
    import inspect

    from lingclaude.engine.tool_pipeline import ToolPipeline
    assert hasattr(ToolPipeline, "_prune_output")
    src = inspect.getsource(ToolPipeline._prune_output)
    assert "locator" in src or "临时文件" in src
