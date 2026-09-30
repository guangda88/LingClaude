"""斜杠命令插件包（2026-09-30 方案 A）。

每个插件文件暴露 ``register(add)`` 契约；由
``lingclaude/cli/slash_plugin_loader.py`` 在启动与 /policy reload 时
扫描并首次加载。以 ``_`` 开头的文件视为私有，不作为插件。
"""
