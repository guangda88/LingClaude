# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵忆 MCP Server — 将灵忆adapter暴露为MCP工具

全族成员可通过MCP协议调用灵忆。
每个工具自动隔离成员数据。

工具清单:
  核心3操作: lm_create, lm_transition, lm_query, lm_get, lm_get_events
  高层组合: lm_start_task, lm_end_task, lm_add_todo, lm_complete_todo, lm_get_todos
  信息管理: lm_record_info, lm_search
  Handover: lm_save_handover, lm_load_handover_summary
  维护:     lm_run_maintenance, lm_db_stats
  Registry: lm_list_types, lm_get_type_spec
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from lingmemory.adapter import LingMemoryAdapter

DB_PATH = os.environ.get(
    "LINGMEMORY_DB_PATH",
    os.path.join(os.path.dirname(__file__), "lingmemory.db"),
)

_adapter = LingMemoryAdapter(DB_PATH)

mcp = FastMCP(
    name="lingmemory",
    instructions=(
        "灵忆(lingmemory) MCP Server — 全族认知状态管理底座。"
        "主干2表3操作，通过Type Registry无限扩展。"
        "所有工具需要 member 参数标识调用者身份。"
    ),
)


# ==========================================================
# 核心3操作
# ==========================================================

@mcp.tool(name="lm_create", description="创建一条 record")
def lm_create(
    member: str,
    type: str,
    data: str,
    parent_id: str = "",
) -> str:
    """创建 record。data 为 JSON 字符串。"""
    import json
    return json.dumps(_adapter.create(
        member=member,
        type=type,
        data=json.loads(data),
        parent_id=parent_id or None,
    ), ensure_ascii=False)


@mcp.tool(name="lm_transition", description="状态流转")
def lm_transition(
    member: str,
    record_id: str,
    event_type: str,
    data: str = "{}",
) -> str:
    """触发状态转换。data 为 JSON 字符串，可含事件特有数据。"""
    import json
    return json.dumps(_adapter.transition(
        member=member,
        record_id=record_id,
        event_type=event_type,
        data=json.loads(data) if data else None,
    ), ensure_ascii=False)


@mcp.tool(name="lm_query", description="检索 records（游标分页）")
def lm_query(
    member: str,
    type: str = "",
    state: str = "",
    parent_id: str = "",
    created_by: str = "",
    cursor: int = 0,
    limit: int = 20,
) -> str:
    """查询 records，返回 {items, next_cursor}。"""
    import json
    return json.dumps(_adapter.query(
        member=member,
        type=type or None,
        state=state or None,
        parent_id=parent_id or None,
        created_by=created_by or None,
        cursor=cursor or None,
        limit=limit,
    ), ensure_ascii=False)


@mcp.tool(name="lm_get", description="取单条 record")
def lm_get(member: str, record_id: str) -> str:
    import json
    return json.dumps(_adapter.get(member=member, record_id=record_id), ensure_ascii=False)


@mcp.tool(name="lm_get_events", description="取 record 的状态变更历史")
def lm_get_events(member: str, record_id: str) -> str:
    import json
    return json.dumps(_adapter.get_events(member=member, record_id=record_id), ensure_ascii=False)


# ==========================================================
# 高层组合
# ==========================================================

@mcp.tool(name="lm_start_task", description="创建+激活任务+创建session")
def lm_start_task(
    member: str, goal: str, boundary: str = ""
) -> str:
    import json
    return json.dumps(_adapter.start_task(
        member=member, goal=goal, boundary=boundary or None
    ), ensure_ascii=False)


@mcp.tool(name="lm_end_task", description="完成+归档任务")
def lm_end_task(member: str, task_id: str, conclusion: str) -> str:
    import json
    return json.dumps(_adapter.end_task(member=member, task_id=task_id, conclusion=conclusion), ensure_ascii=False)


@mcp.tool(name="lm_add_todo", description="给任务添加步骤")
def lm_add_todo(member: str, task_id: str, title: str, order_idx: int) -> str:
    import json
    return json.dumps(_adapter.add_todo(
        member=member, task_id=task_id, title=title, order_idx=order_idx
    ), ensure_ascii=False)


@mcp.tool(name="lm_complete_todo", description="完成步骤（铁律：无结论不done）")
def lm_complete_todo(member: str, todo_id: str, conclusion: str) -> str:
    import json
    return json.dumps(_adapter.complete_todo(
        member=member, todo_id=todo_id, conclusion=conclusion
    ), ensure_ascii=False)


@mcp.tool(name="lm_get_todos", description="获取任务的所有步骤")
def lm_get_todos(member: str, task_id: str) -> str:
    import json
    return json.dumps(_adapter.get_todos(member=member, task_id=task_id), ensure_ascii=False)


@mcp.tool(name="lm_record_info", description="记录一条持久化信息")
def lm_record_info(
    member: str,
    content: str,
    info_type: str = "conclusion",
    is_conclusion: bool = False,
    visibility: str = "private",
    retain: bool = False,
    parent_id: str = "",
) -> str:
    import json
    return json.dumps(_adapter.record_info(
        member=member,
        content=content,
        info_type=info_type,
        is_conclusion=is_conclusion,
        visibility=visibility,
        retain=retain,
        parent_id=parent_id or None,
    ), ensure_ascii=False)


@mcp.tool(name="lm_search", description="全文搜索 info 内容")
def lm_search(member: str, keyword: str, limit: int = 20) -> str:
    import json
    return json.dumps(_adapter.search(member=member, keyword=keyword, limit=limit), ensure_ascii=False)


# ==========================================================
# Handover
# ==========================================================

@mcp.tool(name="lm_save_handover", description="将 handover.yaml 写入灵忆")
def lm_save_handover(member: str, handover_path: str) -> str:
    import json
    return json.dumps(_adapter.save_handover(member=member, handover_path=handover_path), ensure_ascii=False)


@mcp.tool(name="lm_load_handover_summary", description="从灵忆加载成员最新handover摘要")
def lm_load_handover_summary(member: str) -> str:
    import json
    return json.dumps(_adapter.load_handover_summary(member=member), ensure_ascii=False)


# ==========================================================
# 维护
# ==========================================================

@mcp.tool(name="lm_run_maintenance", description="执行信息生命周期维护（archive→expire→purge）")
def lm_run_maintenance() -> str:
    import json
    return json.dumps(_adapter.run_maintenance(), ensure_ascii=False)


@mcp.tool(name="lm_db_stats", description="数据库统计")
def lm_db_stats() -> str:
    import json
    return json.dumps(_adapter.db_stats(), ensure_ascii=False)


# ==========================================================
# Registry
# ==========================================================

@mcp.tool(name="lm_list_types", description="列出所有已注册的 record type")
def lm_list_types() -> str:
    import json
    return json.dumps(_adapter.list_types(), ensure_ascii=False)


@mcp.tool(name="lm_get_type_spec", description="获取 type 的详细规格（状态机+data_schema）")
def lm_get_type_spec(type_name: str) -> str:
    import json
    return json.dumps(_adapter.get_type_spec(type_name=type_name), ensure_ascii=False)


# ==========================================================
# 灵码飞轮工具
# ==========================================================

@mcp.tool(name="lm_record_trace", description="灵码飞轮：记录一次编码轨迹并自动提取规律")
def lm_record_trace(member: str, prompt: str, language: str, generated_code: str,
                    test_result: str, fix: str | None = None,
                    file_path: str | None = None, project: str | None = None,
                    model_used: str | None = None, stderr_snippet: str | None = None) -> str:
    from lingmemory.data_flywheel import DataFlywheel
    import json
    flywheel = DataFlywheel(member=member)
    result = flywheel.record_and_extract(
        prompt=prompt, language=language, generated_code=generated_code,
        test_result=test_result, fix=fix, file_path=file_path,
        project=project, model_used=model_used, stderr_snippet=stderr_snippet)
    flywheel.close()
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(name="lm_flywheel_stats", description="灵码飞轮：数据统计")
def lm_flywheel_stats(member: str) -> str:
    from lingmemory.data_flywheel import DataFlywheel
    import json
    flywheel = DataFlywheel(member=member)
    stats = flywheel.get_stats()
    flywheel.close()
    return json.dumps(stats, ensure_ascii=False)
