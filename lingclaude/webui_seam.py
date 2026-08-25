"""C2: LingClaude webUI seam 注册 — 接入 LINGKERNEL_v1 SeamRegistry。

SeamRegistry 来源: lingflow/lingflow/coordination/seam_registry.py
  register_service(name, interface, provider) -> disposer
  declare_consumer(seam, consumer, methods)   -> disposer
  build_dependency_graph() -> {consumer: [seams]}（fail loud）

本模块把 lingclaude-webui（Rust server）注册为 Service Definition，
并声明 lingclaude 引擎作为 Consumer 消费 webUI 的对话/审批/实时通道。
"""

from __future__ import annotations

from typing import Any

WEBUI_SEAM = "webui.server"

# webUI Service Definition：接口声明（方法签名 + SSE 事件 schema）
WEBUI_INTERFACE: dict[str, Any] = {
    "service": "lingclaude-webui",
    "endpoints": {
        "chat": {"method": "POST", "path": "/chat", "transport": "sse"},
        "live": {"method": "GET", "path": "/live", "transport": "sse"},
        "permission": {"method": "POST", "path": "/chat/permission", "transport": "json"},
        "status": {"method": "GET", "path": "/status", "transport": "json"},
    },
    "events": ["runtime_info", "text", "tool_start", "tool_result", "done", "error"],
}

# lingclaude 引擎作为 Consumer 消费的方法
WEBUI_CONSUMER_METHODS = ["chat", "live", "permission", "status"]


def register_webui_seam(registry: Any | None = None) -> Any:
    """注册 webui seam 并声明 lingclaude 引擎为 Consumer。

    :param registry: SeamRegistry 实例（默认用单例 get_seam_registry()）
    :returns: (service_disposer, consumer_disposer) 两个可逆注销器
    :raises SeamDefinitionError: 重复注册同名 seam 且 provider 不同（fail loud）
    """
    if registry is None:
        from lingflow.coordination.seam_registry import get_seam_registry

        registry = get_seam_registry()

    service_disposer = registry.register_service(
        name=WEBUI_SEAM,
        interface=WEBUI_INTERFACE,
        provider="lingclaude-webui",
    )
    consumer_disposer = registry.declare_consumer(
        seam=WEBUI_SEAM,
        consumer="lingclaude.QueryEngine",
        methods=WEBUI_CONSUMER_METHODS,
    )
    return service_disposer, consumer_disposer


def webui_seam_graph() -> dict[str, list[str]]:
    """构建 seam 依赖拓扑并做完整性校验（fail loud：消费未注册 seam 抛错）。

    :returns: {consumer: [seams]} 依赖图
    """
    from lingflow.coordination.seam_registry import get_seam_registry

    return get_seam_registry().build_dependency_graph()


def register_capability_seams() -> None:
    """T3-1: 注册 capability seam 到 SeamRegistry（fs/shell/llm/subagent）。

    局部引入：只 4 个能力，不全面插件化。
    """
    from lingclaude.lacp.capability_seam import (
        FS_SEAM, SHELL_SEAM, LLM_SEAM, SUBAGENT_SEAM,
        register_default_providers,
    )
    from lingflow.coordination.seam_registry import get_seam_registry

    registry = get_seam_registry()

    # 注册能力 seam
    registry.register_service("fs", FS_SEAM.interface, provider="lingclaude")
    registry.register_service("shell", SHELL_SEAM.interface, provider="lingclaude")
    registry.register_service("llm", LLM_SEAM.interface, provider="lingclaude")
    registry.register_service("subagent", SUBAGENT_SEAM.interface, provider="lingclaude")

    # 注册默认提供者
    register_default_providers()


if __name__ == "__main__":
    # 验证入口：注册 + 拓扑校验 + 快照
    from lingflow.coordination.seam_registry import get_seam_registry

    reg = get_seam_registry()
    sd, cd = register_webui_seam(reg)
    print("seam 注册:", reg.snapshot())
    print("依赖图:", webui_seam_graph())
    sd()
    cd()
    print("dispose 后:", reg.snapshot())
