"""AgentSeam ↔ SubagentBackend 桥 — 让模型侧 sub_agent 工具可派单全族插片。

归位说明（2026-09-28，G11 合规化）：
  本桥是「把 AGENT 缝适配为 SubagentBackend」的主干机制——与具体插件实现无关，
  只依赖 SeamRegistry.AGENT 缝契约（plugin.run(tool, **kwargs)）。原居
  plugins/agents/seam_bridge.py，致主干（tool_handlers/subagent_tools.py）
  直接 import 插件目录，撞 G11。现归位 engine/subagent/（SubagentManager 同域），
  插件侧 seam_bridge.py 保留为纯 re-export 兼容壳（防外部引用断裂）。

架构背景（2026-09-27 桥接验证实证）：SeamRegistry 的 AGENT 缝与
SubagentManager 的后端表（inprocess/acp）是两套独立注册表；MCPSubagentBackend
契约是 client.call_tool("run")（要求 MCP server 自带 run 工具），与各插片的
领域工具面（27 工具等）不匹配——硬凑会失败。

本桥：
  AgentSeamBackend —— 把任一 AgentSeam 插片实例适配为 SubagentBackend 协议：
    run(request, ctx) → plugin.run(**task_args)（task 即工具名，arguments 经
    request.config 传入）；结果收敛为 SubagentResult（J4 语义：失败也是合法
    终态，不抛异常穿透）。

装配入口 install_family_backends(manager)：遍历 SeamRegistry.AGENT 缝，逐个
包装注册进 manager（provider 名 = 缝 key，模型侧 provider="agent/ghidra" 即达）。
fail-soft：单插件包装失败跳过 + 告警，不阻断其余。
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class AgentSeamBackend:
    """把一个 AgentSeam 插片实例适配为 SubagentBackend 协议。

    - name 属性：缝 key（provider 名，模型侧 --provider直达）；
    - run(request, ctx)：request.task = 插片工具名（如 list_methods /
      decompile_function），request.config = 工具 kwargs；插件自身管理
      白名单/审计/J4 record（本桥不重复记账）；
    - abort/status：透传插件协议（status() 返回 dict，收敛为 SubagentStatus）。
    """

    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin
        self.name = getattr(plugin, "name", None) or "agent/unknown"

    def run(self, request: Any, ctx: Any = None) -> Any:
        from lingclaude.engine.subagent.base import SubagentResult, SubagentStatus

        agent_id = f"seam-{uuid.uuid4().hex[:8]}"
        tool = (request.task or "").strip()
        kwargs = dict(getattr(request, "config", None) or {})
        try:
            out = self._plugin.run(tool, **kwargs)
        except Exception as e:  # noqa: BLE001 —— J4：异常收敛为失败结果
            return SubagentResult(
                agent_id=agent_id, task=tool, output="", success=False,
                error=f"{type(e).__name__}: {e}", provider=self.name,
                status=SubagentStatus.FAILED,
            )
        state = out.get("state")
        if state == "succeeded":
            # 插件 result 是 MCP JSON-RPC 全文；提取 content 文本做输出
            output = _extract_text(out.get("result"))
            return SubagentResult(
                agent_id=agent_id, task=tool, output=output, success=True,
                provider=self.name, status=SubagentStatus.COMPLETED,
            )
        return SubagentResult(
            agent_id=agent_id, task=tool, output="",
            success=False,
            error=out.get("error") or f"plugin state={state}",
            provider=self.name,
            status=_status_of(state),
        )

    def abort(self, agent_id: str) -> bool:
        try:
            return bool(self._plugin.abort(agent_id))
        except Exception:  # noqa: BLE001
            return False

    def status(self, agent_id: str = "") -> Any:
        from lingclaude.engine.subagent.base import SubagentStatus
        try:
            st = self._plugin.status()
            if st.get("absent"):
                return SubagentStatus.ABSENT if hasattr(SubagentStatus, "ABSENT") else SubagentStatus.FAILED
            return SubagentStatus.COMPLETED if st.get("healthy") else SubagentStatus.FAILED
        except Exception:  # noqa: BLE001
            return SubagentStatus.FAILED


def _extract_text(mcp_result_json: Any) -> str:
    """从插件 run() 返回的 MCP JSON-RPC 字符串提取 content[].text。"""
    if not isinstance(mcp_result_json, str):
        return str(mcp_result_json or "")
    try:
        obj = json.loads(mcp_result_json)
        parts = obj.get("result", {}).get("content", [])
        return "\n".join(p.get("text", "") for p in parts if isinstance(p, dict))
    except (json.JSONDecodeError, AttributeError):
        return mcp_result_json[:500]


def _status_of(state: str):
    from lingclaude.engine.subagent.base import SubagentStatus
    return {
        "timeout": SubagentStatus.FAILED,
        "failed": SubagentStatus.FAILED,
        "aborted": SubagentStatus.FAILED,
    }.get(state, SubagentStatus.FAILED)


def install_family_backends(manager: Any, only_keys: list[str] | None = None) -> dict:
    """把 SeamRegistry.AGENT 缝全量包装注册进 SubagentManager（模型可派单面）。

    :param manager: SubagentManager 实例
    :param only_keys: 只装指定缝 key（None=全部）
    :returns: {"installed": [keys], "skipped": {key: error}}
    """
    from lingclaude.core.seam import SeamRegistry, SeamType

    installed: list[str] = []
    skipped: dict[str, str] = {}
    for key in SeamRegistry.snapshot().get(SeamType.AGENT.value, []):
        if only_keys is not None and key not in only_keys:
            continue
        try:
            plugin = SeamRegistry.get(SeamType.AGENT, key)
            if not hasattr(plugin, "run"):
                skipped[key] = "no run() on plugin instance"
                continue
            manager.register(AgentSeamBackend(plugin), names=(key,))
            installed.append(key)
        except Exception as e:  # noqa: BLE001 —— fail-soft
            skipped[key] = f"{type(e).__name__}: {e}"
            logger.warning("AgentSeamBackend 装配失败 %s: %s", key, e)
    return {"installed": installed, "skipped": skipped}


def reload_agent_plugins(store: Any = None) -> Any:
    """agent 插件目录重扫装载 — engine 内间接入口（G11 合规通路）。

    主干侧调用点（hot_reload_trigger 等）只允许 import 本函数，不允许直接
    import lingclaude.plugins.agents（G11：主干不 import 插件实现，只经
    PluginLoader 入口）。实现上优先复用 sys.modules 中已加载的
    registry_loader 单例（装配器模块是幂等单例，重扫需拿同一份状态）；
    缺席时退 importlib 按文件路径装载（不经包 import，对齐 PluginLoader
    的 spec_from_file_location 通路语义）；load_all 幂等（同名覆盖）。
    """
    import sys

    module = sys.modules.get("lingclaude.plugins.agents.registry_loader")
    if module is None:
        import importlib.util
        from pathlib import Path

        agents_loader_path = (
            Path(__file__).resolve().parents[2] / "plugins" / "agents" / "registry_loader.py"
        )
        spec = importlib.util.spec_from_file_location(
            "lingclaude.plugins.agents.registry_loader", agents_loader_path
        )
        if spec is None or spec.loader is None:
            return {"loaded": [], "skipped": {"registry_loader": "spec load failed"}}
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module.load_all(store=store)
