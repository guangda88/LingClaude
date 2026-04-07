"""灵克 MCP Server — 将15个核心能力封装为MCP工具。

工具清单（灵系命名）:
  核心编码(8): edit_code(灵编), search_code(灵查), read_file(灵读),
              write_file(灵写), run_bash(灵动), index_project(灵索),
              list_functions(灵析), replace_function(灵构)
  版本控制(3): git_status(灵态), git_log(灵史), git_diff(灵异)
  自优化(4):  run_optimization(灵优), evaluate_code(灵评),
              get_advice(灵谏), check_triggers(灵检)
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="LingClaude",
    instructions="灵克（LingClaude）MCP Server — 自学习AI编程助手核心能力",
)


def _to_dict(obj: Any) -> dict:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return obj


def _unwrap(result: Any) -> Any:
    """解包 Result[T] monad，失败时返回错误字符串。"""
    if hasattr(result, "is_ok") and not result.is_ok:
        return {"error": str(result.error), "ok": False}
    if hasattr(result, "data"):
        val = result.data
        return _to_dict(val) if dataclasses.is_dataclass(val) and not isinstance(val, type) else val
    return _to_dict(result) if dataclasses.is_dataclass(result) and not isinstance(result, type) else result


# ── 核心编码（8个工具） ──


@mcp.tool(name="read_file", description="智能读取文件（灵读）")
def tool_read_file(
    path: str,
    offset: int = 0,
    limit: int = 0,
    line_numbers: bool = True,
    working_dir: str = "",
) -> dict:
    """读取文件内容，支持偏移和行数限制。limit=0 表示不限制。working_dir 为空时使用当前目录。"""
    from ..engine.file_read import FileReadTool

    base = working_dir or "."
    reader = FileReadTool(base_dir=base)
    result = reader.read(path, offset=offset, limit=limit or None, line_numbers=line_numbers)
    return _unwrap(result)


@mcp.tool(name="write_file", description="写入或创建文件（灵写）")
def tool_write_file(path: str, content: str, working_dir: str = "") -> dict:
    """创建新文件或覆盖已有文件。working_dir 为空时使用当前目录。"""
    from ..engine.file_edit import FileEditTool

    base = working_dir or "."
    editor = FileEditTool(base_dir=base)
    result = editor.create(path, content)
    return _unwrap(result)


@mcp.tool(name="edit_code", description="精确编辑代码（灵编）")
def tool_edit_code(
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
    working_dir: str = "",
) -> dict:
    """替换文件中的文本片段。replace_all=True 时替换所有匹配。working_dir 为空时使用当前目录。"""
    from ..engine.file_edit import FileEditTool

    base = working_dir or "."
    editor = FileEditTool(base_dir=base)
    result = editor.replace(path, old_text, new_text, replace_all=replace_all)
    return _unwrap(result)


@mcp.tool(name="search_code", description="代码搜索（灵查）")
def tool_search_code(
    pattern: str,
    include: str = "*.py",
    literal: bool = False,
    case_sensitive: bool = True,
    max_depth: int = 0,
    working_dir: str = "",
) -> dict:
    """在项目中搜索代码，支持正则和字面匹配。max_depth=0 表示不限制。working_dir 为空时使用当前目录。"""
    from ..engine.grep import GrepTool

    base = working_dir or "."
    grep = GrepTool(base_dir=base)
    result = grep.search(
        pattern,
        include=include or None,
        literal=literal,
        case_sensitive=case_sensitive,
        max_depth=max_depth or None,
    )
    return _unwrap(result)


@mcp.tool(name="run_bash", description="执行Shell命令（灵动）")
def tool_run_bash(
    command: str,
    working_dir: str = "",
    timeout: int = 60,
) -> dict:
    """执行Shell命令并返回结果。危险命令会被拦截。"""
    from ..engine.bash import BashExecutor

    executor = BashExecutor(working_dir=working_dir or None, timeout=timeout)
    bash_result = executor.run(command, timeout=timeout)
    return _to_dict(bash_result)


@mcp.tool(name="index_project", description="项目代码索引（灵索）")
def tool_index_project(path: str = ".", max_files: int = 200) -> dict:
    """索引项目代码结构，提取类、函数、导入等符号。"""
    from ..engine.indexer import index_project

    result = index_project(root=path, max_files=max_files)
    return _unwrap(result)


@mcp.tool(name="list_functions", description="列出函数定义（灵析）")
def tool_list_functions(file_path: str) -> list[dict]:
    """列出文件中所有函数和方法的定义。"""
    from ..engine.ast_edit import list_functions

    result = list_functions(file_path)
    unwrapped = _unwrap(result)
    if isinstance(unwrapped, list):
        return unwrapped
    return [unwrapped]


@mcp.tool(name="replace_function", description="替换函数体（灵构）")
def tool_replace_function(
    file_path: str,
    function_name: str,
    new_body: str,
    class_name: str = "",
    occurrence: int = 1,
) -> dict:
    """用新代码替换指定函数/方法的函数体。支持类方法。"""
    from ..engine.ast_edit import replace_function_body

    result = replace_function_body(
        file_path,
        function_name,
        new_body,
        class_name=class_name or None,
        occurrence=occurrence,
    )
    return _unwrap(result)


# ── 版本控制（3个工具） ──


@mcp.tool(name="git_status", description="Git仓库状态（灵态）")
def tool_git_status(path: str = ".", short: bool = True) -> dict:
    """查看Git仓库状态，包括修改、暂存、未跟踪文件。"""
    from ..engine.git import git_status

    result = git_status(path=path, short=short)
    return _unwrap(result)


@mcp.tool(name="git_log", description="Git提交历史（灵史）")
def tool_git_log(
    path: str = ".",
    count: int = 10,
    oneline: bool = True,
    follow: str = "",
) -> dict:
    """查看Git提交历史。follow 可指定文件追踪其重命名历史。"""
    from ..engine.git import git_log

    result = git_log(path=path, count=count, oneline=oneline, follow=follow or None)
    return _unwrap(result)


@mcp.tool(name="git_diff", description="Git差异对比（灵异）")
def tool_git_diff(
    path: str = ".",
    target: str = "",
    staged: bool = False,
    stat: bool = False,
) -> dict:
    """查看Git差异。target 可指定对比的分支/提交。"""
    from ..engine.git import git_diff

    result = git_diff(path=path, target=target, staged=staged, stat=stat)
    return _unwrap(result)


# ── 自优化（4个工具） ──


@mcp.tool(name="evaluate_code", description="代码结构评估（灵评）")
def tool_evaluate_code(target_path: str = ".") -> dict:
    """评估代码结构质量，返回圈复杂度、类规模等指标。"""
    from ..self_optimizer.evaluator import StructureEvaluator

    evaluator = StructureEvaluator(target_path=target_path)
    metrics = evaluator.get_current_metrics()
    return metrics


@mcp.tool(name="run_optimization", description="运行代码优化（灵优）")
def tool_run_optimization(
    target: str = ".",
    goal: str = "structure",
    max_trials: int = 20,
) -> dict:
    """运行同步优化搜索，寻找最佳参数配置。goal 可选: structure/performance/quality。"""
    from ..self_optimizer.optimizer import OptimizationRequest, SimpleSearchSpace

    space = SimpleSearchSpace()
    if goal == "structure":
        space.add_discrete("max_class_lines", [100, 200, 300, 500])
        space.add_discrete("max_method_lines", [20, 50, 100])
        space.add_discrete("max_complexity", [5, 10, 15])
    elif goal == "performance":
        space.add_discrete("cache_size", [0, 100, 1000])
        space.add_discrete("batch_size", [1, 10, 50, 100])
    else:
        space.add_discrete("max_class_lines", [100, 300, 500])
        space.add_discrete("max_complexity", [5, 10, 15])

    best_params = space.sample()
    request = OptimizationRequest(
        target=target,
        goal=goal,
        params=best_params,
        config={"max_trials": max_trials},
    )

    from ..self_optimizer.optimizer import SynchronousOptimizer

    optimizer = SynchronousOptimizer()
    result = optimizer.optimize(request)
    return _to_dict(result)


@mcp.tool(name="get_advice", description="获取优化建议（灵谏）")
def tool_get_advice(
    goal: str = "structure",
    target: str = ".",
) -> str:
    """获取代码优化建议报告，包含当前指标和改进方向。"""
    from ..self_optimizer.advisor import OptimizationAdvisor
    from ..self_optimizer.evaluator import StructureEvaluator

    evaluator = StructureEvaluator(target_path=target)
    metrics = evaluator.get_current_metrics()

    from ..self_optimizer.optimizer import OptimizationResult

    dummy_result = OptimizationResult(
        success=False,
        best_params={},
        best_score=0.0,
        experiments=0,
        duration=0.0,
        error="",
    )

    advisor = OptimizationAdvisor()
    report = advisor.generate_report(goal, target, metrics, dummy_result)
    return report


@mcp.tool(name="check_triggers", description="检查优化触发条件（灵检）")
def tool_check_triggers(
    target_path: str = ".",
    total_files: int = 0,
    total_lines: int = 0,
    test_pass_rate: float = 0.0,
    avg_response_time: float = 0.0,
) -> dict:
    """检查是否满足自动优化触发条件（质量下降、结构恶化、性能瓶颈等）。"""
    from ..self_optimizer.trigger import OptimizationTrigger

    trigger = OptimizationTrigger()
    context = {
        "target_path": target_path,
        "total_files": total_files,
        "total_lines": total_lines,
        "test_pass_rate": test_pass_rate,
        "avg_response_time": avg_response_time,
    }
    should_optimize, trigger_info = trigger.check_all_conditions(context)
    return {
        "should_optimize": should_optimize,
        "trigger": _to_dict(trigger_info) if trigger_info else None,
    }


def main():
    """stdio transport entry point."""
    mcp.run()


if __name__ == "__main__":
    main()
