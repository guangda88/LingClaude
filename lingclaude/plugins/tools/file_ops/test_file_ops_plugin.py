"""file_ops_plugin 自包含测试（灵元「插片无测试 = 非法插片」门禁资产）。

只测委托行为的薄契约：插件可实例化 + create 分派到 FileEditTool。
"""

from __future__ import annotations

from pathlib import Path


def test_file_ops_plugin_instantiable():
    from plugin import FileOpsPlugin

    assert FileOpsPlugin.name == "file_ops_plugin"


def test_file_create_delegates(tmp_path: Path):
    from plugin import FileOpsPlugin

    # FileEditTool 默认 base_dir='.'（插件进程 cwd），tmp_path 会被路径安全拒绝。
    # 用 cwd 内的临时目录文件测委托路径（真实调用场景目录已存在）。
    base = Path.cwd() / ".tmp_plugin_gate_test"
    base.mkdir(parents=True, exist_ok=True)
    try:
        target = base / "created.txt"
        result = FileOpsPlugin().execute(
            name="file_create", path=str(target), content="插片门禁",
        )
        assert target.read_text(encoding="utf-8") == "插片门禁"
        assert result is not None
    finally:
        import shutil
        shutil.rmtree(base, ignore_errors=True)
