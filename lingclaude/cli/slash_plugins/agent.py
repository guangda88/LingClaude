"""斜杠命令插件：/agent —— Agent 声明化系统接入点（2026-10-01 P2 配套）。

接入 lingclaude/core/agent_registry.py：
  - load_agents() 扫描 ~/.lingclaude/agents/ + .lingclaude/agents/
  - match_agents() 关键词匹配
  - get_agents() 缓存单例

用法：
  /agent           列出所有已注册 agent
  /agent <name>    显示指定 agent 详情
  /agent match <q> 搜索匹配 agent
  /agent reload    清除缓存并重新扫描
"""
from __future__ import annotations


def _preview_tier_resolution(model_field: str) -> str | None:
    """档位名预览解析结果；非档位名（具体模型名）返回 None 不显示。

    只读策略与 TaskRouter 当前状态做静态解析展示，不发请求；
    任何异常吞掉返回 None（展示层绝不反噬命令）。
    """
    from lingclaude.core.model_tiers import normalize_tier

    if normalize_tier(model_field) is None:
        return None
    try:
        from lingclaude.core.model_tiers import resolve_tier_model

        from lingclaude.model.task_router import TaskRouter

        router = TaskRouter()  # 默认 config 路径，只读不请求
        cfg = resolve_tier_model(model_field, router)
        if cfg is None:
            return "（候选全灭/不可用 → 运行时将回落 TaskRouter 默认路由）"
        return f"{cfg.model} @ {cfg.base_url}（档位可用）"
    except Exception:  # noqa: BLE001 — 预览失败不影响详情展示
        return "（预览不可用：TaskRouter 未初始化或策略读取失败）"


def _format_agent(a) -> str:
    parts = [f"**{a.name}**"]
    if a.model:
        parts.append(f"`{a.model}`")
    if a.color:
        parts.append(f"[{a.color}]")
    parts.append(f"— {a.description}")
    if a.tools:
        parts.append(f"  tools: {', '.join(a.tools)}")
    if a.skills:
        parts.append(f"  skills: {', '.join(a.skills)}")
    if a.hooks:
        parts.append(f"  hooks: {', '.join(a.hooks)}")
    if a.auto:
        parts.append("  [auto-trigger]")
    parts.append("")
    return "\n".join(parts)


def agent_cmd(processor, arg: str = "") -> None:
    from lingclaude.core.agent_registry import (
        get_agents,
        load_agents,
        match_agents,
        invalidate,
    )

    parts = arg.strip().split()
    cmd = parts[0].lower() if parts else ""

    # /agent reload
    if cmd == "reload":
        invalidate()
        agents = load_agents()
        count = len(agents)
        print(f"[agent registry 重载] 扫描完毕，共 {count} 个 agent")
        return

    # /agent match <query>
    if cmd == "match":
        query = " ".join(parts[1:])
        if not query:
            print("[/agent match] 用法：/agent match <关键词>")
            return
        agents = get_agents()
        results = match_agents(query, agents)
        if not results:
            print(f"[/agent] 无匹配 agent（关键词：{query}）")
            return
        print(f"[/agent] 匹配结果（{len(results)} 个）：\n")
        for a in results:
            print(_format_agent(a))
        return

    # /agent <name> — 显示详情
    if cmd and cmd not in ("list", "ls"):
        agents = get_agents()
        a = agents.get(cmd)
        if not a:
            print(f"[/agent] 未找到 agent：{cmd}")
            return
        print(f"[/agent] {a.name}\n")
        print(f"描述：{a.description}")
        if a.model:
            print(f"模型：{a.model}")
            # P3 三档语义: 档位名(或别名)时显示解析出的实际模型与可用性
            tier_preview = _preview_tier_resolution(a.model)
            if tier_preview is not None:
                print(f"档位解析：{tier_preview}")
        if a.color:
            print(f"配色：{a.color}")
        print(f"工具白名单：{a.tools or '（无限制）'}")
        print(f"工具黑名单：{a.disallowed_tools or '（无）'}")
        print(f"关联 skills：{a.skills or '（无）'}")
        print(f"关联 hooks：{a.hooks or '（无）'}")
        print(f"自动触发：{a.auto}")
        if a.source_file:
            print(f"来源：{a.source_file}")
        print(f"\n指令片段：{a.instruction[:200]}...")
        return

    # /agent — 列出全部
    agents = get_agents()
    if not agents:
        print("[/agent] 尚无已注册的 agent（参考 ~/.lingclaude/agents/）")
        return
    print(f"[/agent] 已注册 agent（{len(agents)} 个）：\n")
    for a in agents.values():
        print(_format_agent(a))


def register(add) -> None:
    """loader 契约：add(name, fn, desc)。"""
    add(
        "/agent",
        agent_cmd,
        "Agent 声明化：/agent [list|match|<name>|reload] — 列出/搜索/查看 agent 定义",
    )
