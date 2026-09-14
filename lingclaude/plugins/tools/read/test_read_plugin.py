"""read_plugin 自包含测试（灵元「插片无测试 = 非法插片」门禁资产）。

只测委托行为的薄契约：插件可实例化 + execute 分派到 FileReadTool。
不测 FileReadTool 本体（那是主干实现，有 tests/test_file_read.py 覆盖）。
"""

from __future__ import annotations

from pathlib import Path


def test_read_plugin_instantiable():
    from plugin import ReadPlugin

    assert ReadPlugin.name == "read_plugin"


def test_read_plugin_delegates_to_file_read(tmp_path: Path):
    from plugin import ReadPlugin

    # FileReadTool 有路径安全（拒绝超出基础目录 base_dir='.'），用 cwd 内临时文件
    base = Path.cwd() / ".tmp_plugin_gate_test_read"
    base.mkdir(parents=True, exist_ok=True)
    try:
        p = base / "hello.txt"
        p.write_text("灵元门禁测试", encoding="utf-8")

        # ReadPlugin.execute 是 *args/**kwargs 吸收型（name 会透传给 FileReadTool.read，
        # 真实执行时别名代理只传 path）。测试按真实调用形状只传 path。
        result = ReadPlugin().execute(path=str(p))
        assert "灵元门禁测试" in str(result)
    finally:
        import shutil
        shutil.rmtree(base, ignore_errors=True)
