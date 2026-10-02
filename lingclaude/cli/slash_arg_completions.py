"""斜杠命令参数级补全（B 2026-10-02）——「选中即参数，无需再打字」。

设计（对齐 docs/atomcode_slash_command_reference.md §五 后续项）：
  • build_arg_completions 是唯一入口：输入「光标前的命令行文本」，
    输出 Completion 列表。SlashCompleter 在命令词后有空格时转入此分支。
  • provider 表按主名注册（别名经 SLASH_REGISTRY 折算，单源不另记账）；
    每个 provider 返回 (插入文本, 注释) 数据对，前缀过滤与 Completion
    包装在此统一做（DRY）。
  • 动态源（会话列表/模型路由/todo/checkpoint/undo 快照/LSP 注册表）
    全部 getattr 链 + try/except 兜底：补全器绝不允许炸输入框，
    engine 缺失/异常时静态候选照出、动态候选静默缩水。
  • 自由文本参数位（/tasks add <文本>、/schedule @daily "查询"）不补全，
    零打扰。
"""
from __future__ import annotations

from typing import Any, Callable

from prompt_toolkit.completion import Completion

from lingclaude.cli.commands import SLASH_REGISTRY


def _short(sid: str) -> str:
    return str(sid)[:8]


def _sessions_of(engine: Any) -> list[dict[str, Any]]:
    """当前项目的会话列表（/session /resume /history 共用源）。"""
    mgr = getattr(engine, "session_manager", None)
    if mgr is None:
        return []
    import os

    try:
        return mgr.list_sessions(project_path=os.getcwd())
    except Exception:  # noqa: BLE001 — cwd 不可用时引擎内部已退全局，再炸则空
        try:
            return mgr.list_sessions()
        except Exception:  # noqa: BLE001
            return []


def _session_id_items(engine: Any) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for s in _sessions_of(engine):
        sid = str(s.get("session_id", ""))
        if sid:
            out.append((_short(sid), str(s.get("summary", ""))[:44]))
    return out


# ---------- 各命令 provider：返回 (插入文本, 注释) ----------


def _p_model(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    out = [("--unpin", "解除钉住，恢复动态路由"), ("--ttl", "钉住秒数：--ttl N")]
    router = getattr(engine, "_task_router", None)
    provs = getattr(router, "_providers", None) or {}
    for pname, pinfo in provs.items():
        default = getattr(pinfo, "default_model", "")
        for m in getattr(pinfo, "models", None) or []:
            mark = " ←默认" if m == default else ""
            out.append((f"{m}@{pname}", f"{pname}{mark}"))
    return out


def _p_session(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    out = [("list", "列出当前项目会话"), ("switch", "切换：switch <id>")]
    out.extend(_session_id_items(engine))
    return out


def _p_undo(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    from lingclaude.core.file_history import list_changes

    out: list[tuple[str, str]] = []
    for i, r in enumerate(list_changes(source="tool_write", limit=20)):
        mark = "改" if r.get("existed") else "新建"
        pruned = " (已清理)" if r.get("pruned") else ""
        out.append((str(i), f"{mark} {r.get('original', '')}{pruned}"))
    return out


def _p_rewind(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    cps = engine.list_checkpoints()
    out: list[tuple[str, str]] = []
    for i, c in enumerate(cps):
        tag = c.get("tag")
        label = str(tag) if tag else str(i)
        meta = f"round={c.get('round_idx')} msgs={c.get('message_count')}"
        out.append((label, meta))
    return out


def _p_lsp(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    if not args:
        return [
            ("add", "注册：add <lang> <command>"),
            ("remove", "删除：remove <lang>"),
            ("check", "握手检查：check <lang>"),
        ]
    if args[0] in ("remove", "check"):
        from lingclaude.engine.lsp_registry import list_servers

        return [
            (str(s.get("lang", "")),
             f"{s.get('command', '')} [{'内置' if s.get('default') else '自定义'}]")
            for s in list_servers()
        ]
    return []  # add <lang> <command> 自由参数


def _p_schedule(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    if args:
        return []  # 第 2 参数是自由文本查询内容
    from lingclaude.core.scheduler import ScheduleType, get_schedule_manager

    out = [(t.value, "预设定时") for t in ScheduleType]
    out.append(("after:N", "N 秒后执行一次"))
    out.append(("at:HH:MM", "今日该时刻执行"))
    try:
        for t in get_schedule_manager().list_tasks():
            out.append((f"cancel {_short(t.task_id)}", f"取消 | {t.cron} {t.query[:28]}"))
    except Exception:  # noqa: BLE001
        pass
    return out


def _p_tasks(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    if not args:
        return [
            ("add", "新增：add <文本>"),
            ("all", "全量面板（含完成/取消）"),
            ("start", "置进行中：start <id>"),
            ("done", "完成：done <id>"),
        ]
    if args[0] in ("start", "done"):
        runtime = getattr(engine, "_runtime", None)
        store = getattr(runtime, "_todo_store", None) if runtime else None
        if store is None:
            return []
        items = store.list()
        return [(_short(i.id), f"{i.status.value} {i.content[:32]}") for i in items]
    return []


def _p_history(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    if args and args[0] == "show":
        return _session_id_items(engine)
    return [("show", "查看会话记录：show <id>")]


def _p_resume(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    return _session_id_items(engine)


# ---------- C 2026-10-02 覆盖面补齐：剩余 24 命令 ----------


def _p_agent(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    out = [("list", "列出全部"), ("match", "按问题匹配"), ("reload", "重载 ~/.lingclaude/agents/")]
    try:
        from lingclaude.core.agent_registry import get_agents

        for a in (get_agents() or {}).values():
            out.append((a.name, str(a.description)[:44]))
    except Exception:  # noqa: BLE001 — 动态源缩水，静态词表照出
        pass
    return out


def _p_policy(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    return [("reload", "重载 policies/*.yaml（免重启）")]


def _p_vault(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    if args and args[0] == "delete":
        try:
            from lingclaude.model.vault import Vault

            return [(e["name"], f"updated {e.get('updated_at', '')}") for e in Vault().list()]
        except Exception:  # noqa: BLE001
            return []
    return [("init", "生成根密钥建库"), ("delete", "删除条目：delete <name>")]


def _p_budget(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    return [("reset", "清零恢复")]


def _p_contract(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    return [
        ("new", "新建契约：new <验收条件>"),
        ("add", "追加条件：add <条件>"),
        ("done", "完成一条：done <序号>"),
        ("clear", "清空（/quit 拦截解除）"),
    ]


def _p_openrouter(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    return [("status", "查看接入状态"), ("logout", "登出"), ("models", "列出可用模型")]


def _p_webui(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    return [("--local", "仅本机 127.0.0.1 + 开浏览器")]


def _p_fork(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    return []  # 可选自定义 tag，自由参数零打扰（留空自动生成）


def _p_share(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    return []  # 目标路径自由参数，零打扰


def _p_image(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    return []  # 图片路径自由参数，零打扰


def _p_checkpoint(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    return []  # 无参数


def _p_recover(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    return []  # 无参数


def _p_help(engine: Any, args: list[str], current: str) -> list[tuple[str, str]]:
    """/help <命令> 的命令名清单——SLASH_REGISTRY 单源派生，/help 309 同构。
    插入文本不带斜杠（用法即 /help clear），hidden 条目除外。"""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for e in SLASH_REGISTRY.values():
        if e.hidden:
            continue
        bare = e.name.lstrip("/")
        if bare in seen:
            continue
        seen.add(bare)
        out.append((bare, e.desc[:44]))
    return out


def _canonical_name(entry: Any) -> str:
    """别名 → 主名：handler 同体归组取注册序首个（/help:309 同构，
    SlashCompleter._canonical_names 的单条目版）。"""
    for e in SLASH_REGISTRY.values():
        if e.hidden or id(e.handler) != id(entry.handler):
            continue
        return e.name
    return entry.name


_PROVIDERS: dict[str, Callable[[Any, list[str], str], list[tuple[str, str]]]] = {
    "/model": _p_model,
    "/session": _p_session,
    "/resume": _p_resume,
    "/undo": _p_undo,
    "/rewind": _p_rewind,
    "/lsp": _p_lsp,
    "/schedule": _p_schedule,
    "/tasks": _p_tasks,
    "/history": _p_history,
    # C 2026-10-02 覆盖面补齐（24 命令中 16 个有参数语义的 + 7 个无参守门）
    "/agent": _p_agent,
    "/policy": _p_policy,
    "/vault": _p_vault,
    "/budget": _p_budget,
    "/contract": _p_contract,
    "/openrouter": _p_openrouter,
    "/webui": _p_webui,
    "/fork": _p_fork,
    "/share": _p_share,
    "/image": _p_image,
    "/checkpoint": _p_checkpoint,
    "/recover": _p_recover,
    "/compact": _p_checkpoint,      # 无参
    "/continue": _p_recover,        # 无参
    "/resync": _p_checkpoint,       # 无参
    "/clear": _p_recover,           # 无参（含 /new 别名，同 handler 折算）
    "/multi": _p_checkpoint,        # 无参
    "/help": _p_help,               # /help <命令> 补命令名
    "/quit": _p_checkpoint,         # 无参（/exit 同）
}


def build_arg_completions(engine: Any, before_cursor: str) -> list[Completion]:
    """参数语境补全入口。before_cursor = 光标前的整行文本（已过命令词）。

    别名折算经 SLASH_REGISTRY（/todo s… → /tasks provider），单源。
    任何动态源异常都吞掉：补全永远不炸输入框。
    """
    parts = before_cursor.split()
    if not parts:
        return []
    entry = SLASH_REGISTRY.get(parts[0])
    if entry is None:
        return []
    # 别名折算：别名条目自身 name 即别名（_register 逐别名建条目），
    # 需经 handler 同体归组折回主名（/help 309 同构）——/todo → /tasks。
    main = _canonical_name(entry)
    fn = _PROVIDERS.get(main)
    if fn is None:
        return []
    if before_cursor.endswith((" ", "\t")):
        args, current = parts[1:], ""
    else:
        args, current = parts[1:-1], parts[-1]
    try:
        items = fn(engine, args, current)
    except Exception:  # noqa: BLE001 — provider 内部已尽量兜底，这里最后一道
        return []
    out: list[Completion] = []
    low = current.lower()
    for text, meta in items:
        if current and not text.lower().startswith(low):
            continue
        out.append(Completion(
            text,
            start_position=-len(current),
            display=text,
            display_meta=meta,
        ))
    return out
