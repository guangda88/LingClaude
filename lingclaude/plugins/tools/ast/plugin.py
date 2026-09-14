"""ast 工具组插件（P1: plugins/tools 载体第四批插片）。

灵元纪律：
- 变化（工具实现）= 插片，不焊进主干 —— 本文件自包含 manifest + 实现。
- 不复制主干逻辑：execute 委托 engine/ast_edit 纯函数
  （replace_function_body / list_functions），无状态委托，天然可插件化。
- 插件提供 ast_replace / list_functions 两种能力，按 name 分派。
"""

from __future__ import annotations

from typing import Any


class AstPlugin:
    """ToolPlugin 协议实现：name + execute（SeamType.TOOL 槽位）。"""

    name = "ast_plugin"

    def execute(self, name: str = "list_functions", **kwargs: Any) -> Any:
        """按工具名分派到 engine.ast_edit 纯函数。

        - ast_replace → replace_function_body(file_path, function_name, new_body, class_name, occurrence)
        - list_functions → list_functions(file_path)
        """
        from lingclaude.engine.ast_edit import list_functions, replace_function_body

        if name == "ast_replace":
            result = replace_function_body(
                file_path=kwargs.get("file_path", ""),
                function_name=kwargs.get("function_name", ""),
                new_body=kwargs.get("new_body", ""),
                class_name=kwargs.get("class_name"),
                occurrence=kwargs.get("occurrence", 1),
            )
            if getattr(result, "is_error", False):
                return {"error": getattr(result, "error", "unknown ast_replace error")}
            return getattr(result, "data").to_dict()

        result = list_functions(file_path=kwargs.get("file_path", ""))
        if getattr(result, "is_error", False):
            return {"error": getattr(result, "error", "unknown list_functions error")}
        return {"functions": getattr(result, "data")}
