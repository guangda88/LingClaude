"""斜杠命令插件 loader 专项测试（2026-09-30 方案 A）。

覆盖：运行时新增插件免重启拾取、补全表同步、幂等、
「已加载插件不重载」红线、坏插件隔离、契约校验。
全局状态（注册表/补全表/_loaded/sys.modules）用 restore_state 快照还原，
防测试间互染。
"""
from __future__ import annotations

import contextlib
import io
import sys

import pytest

from lingclaude.cli import slash_plugin_loader as loader
from lingclaude.cli.commands import SLASH_COMPLETER_WORDS, SLASH_REGISTRY, SlashCommandProcessor

ECHO_PLUGIN = '''\
def echo_cmd(processor, arg=""):
    print(f"ECHO:{arg or '空'}")


def register(add):
    add("/echo", echo_cmd, "回显测试命令")
'''

V1 = '''\
def v_cmd(processor, arg=""):
    print("VERSION:1")


def register(add):
    add("/vtest", v_cmd, "版本命令")
'''

V2_MODIFIED = '''\
def v_cmd(processor, arg=""):
    print("VERSION:2")


def register(add):
    add("/vtest", v_cmd, "版本命令（已改）")
'''

BAD_SYNTAX = "def broken(:\n"

NO_CONTRACT = "SHARED_CONST = 42\n"

BAD_SHAPE = '''\
def too_many(a, b, c):
    pass


def register(add):
    add("/badshape", too_many, "签名违规")
'''

BAD_NAME = '''\
def cmd(processor, arg=""):
    pass


def register(add):
    add("noslash", cmd, "名字违规")
'''


def _make_proc() -> SlashCommandProcessor:
    """最小 processor（handle 分派路径不触 engine 内部）。"""
    proc = SlashCommandProcessor.__new__(SlashCommandProcessor)
    return proc


@pytest.fixture
def restore_state():
    """快照/还原 loader 触及的全部全局状态。"""
    saved_loaded = set(loader._loaded)
    saved_registry = dict(SLASH_REGISTRY)
    saved_words = list(SLASH_COMPLETER_WORDS)
    prefix = "lingclaude.cli.slash_plugins."
    saved_modules = {k: v for k, v in sys.modules.items() if k.startswith(prefix)}
    yield
    loader._loaded.clear()
    loader._loaded.update(saved_loaded)
    for k in set(SLASH_REGISTRY) - set(saved_registry):
        SLASH_REGISTRY.pop(k, None)
    SLASH_COMPLETER_WORDS[:] = saved_words
    for k in set(sys.modules) - set(saved_modules):
        if k.startswith(prefix):
            sys.modules.pop(k, None)


class TestRuntimePickup:
    def test_new_plugin_file_picked_up_without_restart(self, tmp_path, monkeypatch, restore_state):
        """运行时丢入插件文件 → load 一次即注册+进补全+可分派。"""
        monkeypatch.setattr(loader, "PLUGINS_DIR", tmp_path)
        (tmp_path / "echo.py").write_text(ECHO_PLUGIN, encoding="utf-8")

        new_cmds = loader.load_slash_plugins()

        assert new_cmds == ["/echo"]
        assert "/echo" in SLASH_REGISTRY
        assert "/echo" in SLASH_COMPLETER_WORDS  # 补全表同步
        proc = _make_proc()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert proc.handle("/echo 你好") is True
        assert "ECHO:你好" in buf.getvalue()

    def test_wants_arg_detected_noarg_handler(self, tmp_path, monkeypatch, restore_state):
        """单参 handler (processor) → wants_arg=False，无参分派不误传。"""
        monkeypatch.setattr(loader, "PLUGINS_DIR", tmp_path)
        (tmp_path / "plain.py").write_text(
            'def plain_cmd(processor):\n'
            '    print("PLAIN")\n\n\n'
            'def register(add):\n'
            '    add("/plain", plain_cmd, "无参命令")\n',
            encoding="utf-8")
        loader.load_slash_plugins()
        assert SLASH_REGISTRY["/plain"].wants_arg is False
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert _make_proc().handle("/plain") is True
        assert "PLAIN" in buf.getvalue()


class TestIdempotency:
    def test_second_load_no_dup(self, tmp_path, monkeypatch, restore_state):
        monkeypatch.setattr(loader, "PLUGINS_DIR", tmp_path)
        (tmp_path / "echo.py").write_text(ECHO_PLUGIN, encoding="utf-8")
        assert loader.load_slash_plugins() == ["/echo"]
        assert loader.load_slash_plugins() == []  # 幂等
        words = [w for w in SLASH_COMPLETER_WORDS if w == "/echo"]
        assert len(words) == 1


class TestNoModuleReload:
    def test_modified_plugin_keeps_old_behavior(self, tmp_path, monkeypatch, restore_state):
        """红线锚：修改已加载插件 → 不重载，旧行为保持。"""
        monkeypatch.setattr(loader, "PLUGINS_DIR", tmp_path)
        f = tmp_path / "v.py"
        f.write_text(V1, encoding="utf-8")
        loader.load_slash_plugins()

        f.write_text(V2_MODIFIED, encoding="utf-8")  # 运行中改已加载插件
        assert loader.load_slash_plugins() == []  # 不拾取

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _make_proc().handle("/vtest")
        assert "VERSION:1" in buf.getvalue()  # 仍旧版本
        assert "VERSION:2" not in buf.getvalue()


class TestIsolation:
    def test_broken_plugin_does_not_block_others(self, tmp_path, monkeypatch, restore_state, capsys):
        monkeypatch.setattr(loader, "PLUGINS_DIR", tmp_path)
        (tmp_path / "zz_broken.py").write_text(BAD_SYNTAX, encoding="utf-8")
        (tmp_path / "aa_good.py").write_text(ECHO_PLUGIN, encoding="utf-8")

        new_cmds = loader.load_slash_plugins()

        assert "/echo" in new_cmds  # 好插件照常注册
        assert "/zz_broken" not in SLASH_REGISTRY
        out = capsys.readouterr().out
        assert "zz_broken" in out and "加载失败" in out  # 有声失败，不静默

    def test_no_contract_file_silently_skipped(self, tmp_path, monkeypatch, restore_state):
        monkeypatch.setattr(loader, "PLUGINS_DIR", tmp_path)
        (tmp_path / "lib.py").write_text(NO_CONTRACT, encoding="utf-8")
        assert loader.load_slash_plugins() == []

    def test_missing_dir_no_crash(self, tmp_path, monkeypatch, restore_state):
        monkeypatch.setattr(loader, "PLUGINS_DIR", tmp_path / "nonexistent")
        assert loader.load_slash_plugins() == []


class TestContractValidation:
    @pytest.mark.parametrize("src,stem", [(BAD_SHAPE, "badshape"), (BAD_NAME, "badname")])
    def test_invalid_contract_rejected(self, tmp_path, monkeypatch, restore_state, capsys, src, stem):
        """3 参 handler / 非斜杠名 → 拒绝注册 + 有声报错（不进注册表）。"""
        monkeypatch.setattr(loader, "PLUGINS_DIR", tmp_path)
        (tmp_path / f"{stem}.py").write_text(src, encoding="utf-8")
        assert loader.load_slash_plugins() == []
        assert f"/{stem}" not in SLASH_REGISTRY
        assert "注册失败" in capsys.readouterr().out


class TestMainlineIntegration:
    def test_policy_plugin_registered_from_real_dir(self):
        """真实插件目录：/policy 由插件注册（迁移锚），主干方法已删。"""
        assert "/policy" in SLASH_REGISTRY
        h = SLASH_REGISTRY["/policy"].handler
        assert getattr(h, "__module__", "") == "lingclaude.cli.slash_plugins.policy"
        assert not hasattr(SlashCommandProcessor, "_cmd_policy")

    def test_completer_words_derivation_consistency(self):
        """插件注册后补全清单与注册表可见条目一致（单源锚）。"""
        expected = []
        seen = set()
        for e in SLASH_REGISTRY.values():
            if e.hidden or e.name in seen:
                continue
            seen.add(e.name)
            expected.append(e.name)
        assert SLASH_COMPLETER_WORDS == expected
