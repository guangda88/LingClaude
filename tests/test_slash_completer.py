# -*- coding: utf-8 -*-
"""SlashCompleter 测试（A 2026-10-02，atomcode 同构补全注释渲染）。

覆盖：
  • 命令词前缀补全（/mo → /model，整词替换 start_position=-len(token)）
  • desc 渲染进 display_meta（P0：补全浮层注释灰字）
  • arg_hint 追加第三段（P2：desc → arg_hint）
  • 语境边界：参数语境不补命令名、hidden 不出现、普通文本零打扰
  • 单源：补全集合与 SLASH_REGISTRY 派生一致（含插件命令）
  • full_tui 布局：CompletionsMenu 挂载（FloatContainer）
"""
import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from prompt_toolkit.document import Document
from prompt_toolkit.completion import CompleteEvent


def _completions(completer, buf: str):
    doc = Document(buf, len(buf))
    out = []
    for comp in completer.get_completions(doc, None):
        # display_meta 是 FormattedText，取纯文本便于断言
        meta = comp.display_meta
        if meta is not None and not isinstance(meta, str):
            meta = "".join(frag[1] for frag in meta)
        out.append((comp.text, comp.start_position, meta))
    return out


@pytest.fixture()
def completer():
    from lingclaude.cli.slash_completer import SlashCompleter

    return SlashCompleter()


class TestSlashCompletion:
    def test_prefix_completion(self, completer):
        """/mo → /model，整词替换（start=-3）。"""
        got = _completions(completer, "/mo")
        assert ("/model", -3) in [(t, sp) for t, sp, _ in got]

    def test_desc_rendered_in_meta(self, completer):
        """P0：desc 进 display_meta（atomcode 双列注释同构）。"""
        from lingclaude.cli.commands import SLASH_REGISTRY

        got = dict((t, m) for t, _, m in _completions(completer, "/mo"))
        assert got["/model"] == SLASH_REGISTRY["/model"].desc

    def test_arg_hint_appended(self, completer):
        """P2：arg_hint 非空的命令 meta 追加 ` → hint`（探针注册验证）。"""
        import lingclaude.cli.commands as cmds
        from lingclaude.cli.commands import SlashCommand

        probe = SlashCommand(
            # handler 必须独立（不能 None）——None 单例会被归组逻辑并进
            # /quit、/exit 组（id(None) 相同），别名视图判定就会指向 /quit。
            name="/zz_hint", handler=lambda: None, desc="探针命令",
            arg_hint="usage: /zz_hint <x>",
        )
        cmds.SLASH_REGISTRY["/zz_hint"] = probe
        try:
            got = dict((t, m) for t, _, m in _completions(completer, "/zz_h"))
            assert got["/zz_hint"] == "探针命令 → usage: /zz_hint <x>"
        finally:
            cmds.SLASH_REGISTRY.pop("/zz_hint", None)

    def test_no_arg_hint_meta_is_desc_only(self, completer):
        """arg_hint 为空（现存注册表全为此态）→ meta 纯 desc，无箭头尾巴。"""
        from lingclaude.cli.commands import SLASH_REGISTRY

        entry = SLASH_REGISTRY["/model"]
        assert not entry.arg_hint
        got = dict((t, m) for t, _, m in _completions(completer, "/mo"))
        assert got["/model"] == entry.desc

    def test_full_slash_lists_all(self, completer):
        """/ 单字符前缀 → 全量清单（hidden 除外），与注册表派生一致。"""
        from lingclaude.cli.commands import SLASH_REGISTRY

        expected = {
            e.name for e in SLASH_REGISTRY.values() if not e.hidden
        }
        got = {t for t, _, _ in _completions(completer, "/")}
        assert got == expected

    def test_plugin_commands_included(self, completer):
        """单源：插件命令（slash_plugin_loader 挂载）也在补全集合里。"""
        from lingclaude.cli.commands import SLASH_REGISTRY

        plugin_names = [
            e.name for e in SLASH_REGISTRY.values()
            if getattr(e.handler, "__module__", "").startswith(
                "lingclaude.cli.slash_plugins"
            )
        ]
        got = {t for t, _, _ in _completions(completer, "/")}
        assert plugin_names, "前提：至少存在一个插件斜杠命令"
        assert set(plugin_names) <= got

    def test_arg_context_no_command_completion(self, completer):
        """命令词后已空格 → 参数语境，不补命令名（B 起转入参数级补全：
        /help 有意升级为补 <命令> 主题，见 TestArgCompletions）。"""
        # 用真无参命令断言「不串台到命令名补全」
        assert _completions(completer, "/clear ") == []
        # /help 参数语境出的是主题词（不带斜杠），不再是命令名
        got = _completions(completer, "/help ")
        assert got and all(not t.startswith("/") for t, _, _ in got)

    def test_hidden_excluded(self, completer):
        """/? hidden 别名不进候选（噪声防护）。"""
        assert _completions(completer, "/?") == []

    def test_plain_text_zero_noise(self, completer):
        """非斜杠输入零打扰；斜杠出现在词中不补。"""
        assert _completions(completer, "hello /m") == []

    def test_case_insensitive_prefix(self, completer):
        """/MO 大写前缀命中（ignore_case 对齐旧行为）。"""
        got = _completions(completer, "/MO")
        assert "/model" in [t for t, _, _ in got]

    def test_registry_stamps_cache_invalidation(self, completer):
        """注册表新增命令后缓存按 stamp 失效（插件 reload 热注册场景）。"""
        import lingclaude.cli.commands as cmds
        from lingclaude.cli.commands import SlashCommand

        before = {t for t, _, _ in _completions(completer, "/")}
        stamp_obj = SlashCommand(
            name="/zzz_test_probe", handler=None, desc="探针",
        )
        cmds.SLASH_REGISTRY["/zzz_test_probe"] = stamp_obj
        try:
            after = {t for t, _, _ in _completions(completer, "/")}
            assert "/zzz_test_probe" in after
            assert "/zzz_test_probe" not in before
        finally:
            cmds.SLASH_REGISTRY.pop("/zzz_test_probe", None)


class TestWiring:
    def test_repl_uses_slash_completer(self):
        """repl.py 已换 SlashCompleter（WordCompleter 摆设退役）。"""
        import inspect

        from lingclaude.cli import repl

        src = inspect.getsource(repl)
        # B 2026-10-02: engine 注入（参数级补全动态源）
        assert "SlashCompleter(engine)" in src
        # 旧接线不得残留
        assert "WordCompleter(SLASH_COMPLETER_WORDS" not in src

    def test_full_tui_has_completions_menu_float(self):
        """全屏 TUI 布局挂了 CompletionsMenu float（否则候选无处渲染）。"""
        import inspect

        from lingclaude.cli import full_tui

        src = inspect.getsource(full_tui)
        assert "FloatContainer(" in src
        assert "CompletionsMenu(" in src


class TestImportFallback:
    def test_import_error_yields_none(self, monkeypatch):
        """prompt_toolkit 缺席时 repl 构造路径不炸（ImportError → None 兜底）。"""
        # SlashCompleter 模块自身 import prompt_toolkit——模拟缺席即 ImportError
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "lingclaude.cli.slash_completer":
                raise ImportError("simulated")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        import lingclaude.cli.repl as repl_mod
        importlib.reload(repl_mod)
        # reload 后 repl 模块仍可导入（构造函数体不在此刻执行，只验证无 ImportError 外泄）
        assert hasattr(repl_mod, "_interactive_loop")
        # 还原：再次 reload 恢复正常 import
        monkeypatch.undo()
        importlib.reload(repl_mod)


class TestAliasGrouping:
    """别名归组视图（2026-10-02 用户反馈：/tasks//todo//plan 三行同文案噪音）。

    语义与 /help 的 handler 同体归组对齐（commands.py:309）：
    别名可补全，但 meta 指向主名；/quit//exit 独立注册靠 None 单例同组归并。
    """

    @pytest.fixture
    def completer(self):
        from lingclaude.cli.slash_completer import SlashCompleter
        return SlashCompleter()

    def _meta_by_name(self, completer, text):
        return dict(
            (x.text, x.display_meta_text)
            for x in completer.get_completions(Document(text), CompleteEvent())
        )

    def test_tasks_alias_points_to_main(self, completer):
        """/todo //plan meta 是指向注释，不再复读主 desc。"""
        d = self._meta_by_name(completer, "/t")
        assert d["/tasks"].startswith("任务面板")
        assert d["/todo"] == "→ /tasks（同义）"
        d = self._meta_by_name(completer, "/p")
        assert d["/plan"] == "→ /tasks（同义）"

    def test_quit_exit_grouped(self, completer):
        """/exit 独立注册（handler=None）→ None 单例归组指向 /quit。"""
        assert self._meta_by_name(completer, "/e")["/exit"] == "→ /quit（同义）"
        assert self._meta_by_name(completer, "/q")["/quit"] == "退出"

    def test_alias_still_completable(self, completer):
        """归组只改注释列，不改补全行为：敲别名照样出候选、能选中。"""
        texts = {x.text for x in completer.get_completions(Document("/to"), CompleteEvent())}
        assert "/todo" in texts


# ============ B 2026-10-02: 参数级补全（/model /session 等） ============

class TestArgCompletions:
    """参数语境补全：命令词后空格 → slash_arg_completions 接管。"""

    def _comp(self, engine=None):
        from lingclaude.cli.slash_completer import SlashCompleter
        return SlashCompleter(engine)

    def _run(self, comp, line):
        d = Document(line, len(line))
        return [c.text for c in comp.get_completions(d, None)]

    def test_model_unpin_and_providers(self):
        from types import SimpleNamespace as NS
        eng = MagicMock()
        eng._task_router._providers = {
            "openai": NS(default_model="gpt-4o", models=["gpt-4o", "gpt-4o-mini"]),
        }
        got = self._run(self._comp(eng), "/model ")
        assert "--unpin" in got and "gpt-4o@openai" in got

    def test_model_prefix_filter(self):
        from types import SimpleNamespace as NS
        eng = MagicMock()
        eng._task_router._providers = {
            "openai": NS(default_model="gpt-4o", models=["gpt-4o", "claude-x"]),
        }
        got = self._run(self._comp(eng), "/model gpt")
        assert got == ["gpt-4o@openai"]

    def test_session_static_and_dynamic_ids(self):
        eng = MagicMock()
        eng.session_manager.list_sessions.return_value = [
            {"session_id": "aaaabbbb-cccc", "summary": "s1"},
        ]
        got = self._run(self._comp(eng), "/session ")
        assert "list" in got and "switch" in got and "aaaabbbb" in got

    def test_alias_fold_todo_to_tasks(self):
        eng = MagicMock()
        store = MagicMock()
        store.list.return_value = [NS2(id="t1", status=NS2(value="pending"), content="x")]
        eng._runtime._todo_store = store
        got = self._run(self._comp(eng), "/todo st")
        assert got == ["start"]

    def test_engine_none_static_only_no_crash(self):
        comp = self._comp(None)
        assert "list" in self._run(comp, "/session ")
        # 静态候选不依赖 engine，照出；动态模型清单缩水为空
        got = self._run(comp, "/model ")
        assert "--unpin" in got and not any("@" in g for g in got)

    def test_provider_exception_swallowed(self):
        eng = MagicMock()
        eng.session_manager.list_sessions.side_effect = RuntimeError("boom")
        got = self._run(self._comp(eng), "/resume ")  # 动态源炸 → 静默缩水
        assert got == []

    def test_free_text_args_not_completed(self):
        eng = MagicMock()
        assert self._run(self._comp(eng), "/tasks add 写个测试") == []

    def test_unknown_or_noparam_commands_stay_quiet(self):
        eng = MagicMock()
        comp = self._comp(eng)
        assert self._run(comp, "/clear ") == []
        assert self._run(comp, "/multi ") == []
        assert self._run(comp, "/exit ") == []

    def test_history_show_id_fold(self):
        eng = MagicMock()
        eng.session_manager.list_sessions.return_value = [
            {"session_id": "aaaabbbb-cccc", "summary": "s1"},
        ]
        got = self._run(self._comp(eng), "/history show ")
        assert got == ["aaaabbbb"]

    # ── C 2026-10-02 覆盖面补齐守卫 ──

    def test_coverage_every_command_with_param_semantics(self):
        """全注册表扫描：非「无参/自由参数」命令在参数语境必须有候选。
        锁覆盖面——根除「只接了 9/33」式漏登复发。"""
        free_or_none = {"/checkpoint", "/clear", "/compact", "/continue", "/exit",
                        "/fork", "/image", "/multi", "/new", "/quit", "/recover",
                        "/resync", "/share"}
        comp = self._comp(None)  # engine=None：静态候选照出，足以判覆盖
        from lingclaude.cli.commands import SLASH_REGISTRY
        seen = set()
        for e in SLASH_REGISTRY.values():
            if e.name in seen or e.name in free_or_none or e.hidden:
                continue
            seen.add(e.name)
            # /resume 动态源缩水属预期（engine=None），静态有 /session /undo 等兜着
            if e.name in ("/resume", "/rewind", "/?"):
                continue  # 纯动态源，engine=None 缩水属预期（静态兜底命令在别项已锁）
            got = self._run(comp, f"{e.name} ")
            assert got, f"{e.name} 参数语境零候选——覆盖面漏登"

    def test_agent_static_words_and_tolerant(self):
        got = self._run(self._comp(None), "/agent ")
        assert {"list", "match", "reload"} <= set(got)

    def test_help_bare_names_no_slash(self):
        comp = self._comp(None)
        assert "clear" in self._run(comp, "/help cl")
        assert self._run(comp, "/help m") == ["multi", "model"]  # 注册序：/multi 先于 /model

    def test_vault_delete_lists_entry_names(self, monkeypatch):
        import lingclaude.model.vault as vault_mod

        class FakeVault:
            def list(self):
                return [{"name": "credential_pool_keys", "updated_at": "t"}]

        monkeypatch.setattr(vault_mod, "Vault", FakeVault)
        got = self._run(self._comp(None), "/vault delete ")
        assert got == ["credential_pool_keys"]

    def test_openrouter_webui_budget_policy_static(self):
        comp = self._comp(None)
        assert self._run(comp, "/openrouter ") == ["status", "logout", "models"]
        assert self._run(comp, "/webui ") == ["--local"]
        assert self._run(comp, "/budget ") == ["reset"]
        assert self._run(comp, "/policy ") == ["reload"]


class NS2:
    """MagicMock 属性访问会吞 dict/int 对比的坑，用真 namespace。"""
    def __init__(self, **kw):
        self.__dict__.update(kw)
