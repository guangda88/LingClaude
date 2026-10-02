"""斜杠命令插件 loader（2026-09-30 方案 A）。

职责：扫描插件目录，对每个插件模块**首次** import 并调用其 register(add)。
契约（插件文件只需暴露一个函数）::

    def register(add) -> None:
        add("/mycmd", my_handler, "帮助文本", aliases=(), needs_args=, arg_hint=)

红线边界：只做首次 import（importlib 缓存中已有的模块直接跳过）——
「reload data 不 reload module」；修改已加载插件仍需重启进程。

handler 形状：普通函数 (processor, arg) —— SLASH_REGISTRY 存未绑定
可调用，handle() 以 entry.handler(self, arg) 调用，函数与方法同构。

循环引用边界（2026-09-30 实证修复）：本模块顶部禁止 import commands——
commands.py 尾部反向调 load_slash_plugins()，顶部依赖会在 pytest 先加载
本模块时把 import 钉死在半初始化 commands 上（ImportError 被尾部 try 吞掉，
插件静默失踪）。注册表一律函数体内 import lingclaude.cli.commands 取。
"""
from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
from types import ModuleType

# 插件目录：与本 loader 同级的 slash_plugins/
PLUGINS_DIR = Path(__file__).resolve().parent / "slash_plugins"

# 本进程已加载过的插件模块名集合（防重复注册的唯一事实源）
_loaded: set[str] = set()


def _import_file(path: Path) -> ModuleType | None:
    """按文件路径首次加载模块；缓存命中（sys.modules）返回 None。"""
    mod_name = f"lingclaude.cli.slash_plugins.{path.stem}"
    if mod_name in _loaded or mod_name in importlib.sys.modules:
        return None
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover — 异常文件名
        return None
    module = importlib.util.module_from_spec(spec)
    importlib.sys.modules[mod_name] = module  # 先占位，防插件内自引用死循环
    spec.loader.exec_module(module)
    return module


def load_slash_plugins() -> list[str]:
    """扫描目录、注册新插件；返回本次新注册的命令名（如 ['/policy']）。

    幂等：已加载模块跳过，重复调用不产生重复注册。
    目录不存在 → 返回 []（不抛，启动路径不允许因插件炸掉）。
    """
    import lingclaude.cli.commands as _cmds

    if not PLUGINS_DIR.is_dir():
        return []
    new_cmds: list[str] = []
    from lingclaude.core import plugin_governance

    for path in sorted(PLUGINS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue  # 私有/共享工具模块，不作为插件
        verdict = plugin_governance.deny_on_error(
            plugin_governance.check_file_allowed, path
        )
        if not verdict.allowed:
            print(f"[slash 插件被治理拒绝] {path.name}: {verdict.reason}")
            continue
        before = set(_cmds.SLASH_REGISTRY)
        try:
            module = _import_file(path)
        except Exception as e:  # noqa: BLE001 — 单个插件坏不拖垮其余
            print(f"[slash 插件加载失败] {path.name}: {e}")
            continue
        if module is None:
            continue  # 缓存命中，已加载过
        register = getattr(module, "register", None)
        if not callable(register):
            continue  # 无契约的 .py 静默跳过（当作共享库）
        after: set[str] = set()

        def add(name, handler, desc, **kw):  # noqa: ANN001 — 契约签名
            import inspect as _inspect

            import lingclaude.cli.commands as _cmds

            if not isinstance(name, str) or not name.startswith("/"):
                raise ValueError(f"插件命令名必须以 / 开头: {name!r}")
            try:  # 与主干 _register 同款：按签名探测 wants_arg
                n_params = len(_inspect.signature(handler).parameters)
            except (ValueError, TypeError):  # pragma: no cover
                n_params = 2
            if n_params > 2:
                raise ValueError(
                    f"插件 handler 必须 (processor, arg) 形状: "
                    f"{getattr(handler, '__name__', handler)}"
                )
            _cmds.SLASH_REGISTRY[name] = _cmds.SlashCommand(
                name=name, handler=handler, desc=desc,
                aliases=tuple(kw.get("aliases", ())),
                needs_args=bool(kw.get("needs_args", False)),
                arg_hint=str(kw.get("arg_hint", "")),
                wants_arg=n_params >= 2,
            )
            # 同步补全表（repl.py 持列表引用，append 即生效）。
            # 首次挂载发生在 commands.py 尾部、补全清单派生之前——此时该表
            # 尚未创建，靠派生循环自然收录；运行时 reload 才走 append。
            cw = getattr(_cmds, "SLASH_COMPLETER_WORDS", None)
            if cw is not None and name not in cw:
                cw.append(name)

        try:
            register(add)
        except Exception as e:  # noqa: BLE001 — register 坏不拖垮其余
            print(f"[slash 插件注册失败] {path.name}: {e}")
            continue
        after = set(_cmds.SLASH_REGISTRY) - before
        _loaded.add(f"lingclaude.cli.slash_plugins.{path.stem}")
        new_cmds.extend(sorted(after))
    return new_cmds
