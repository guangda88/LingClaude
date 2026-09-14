"""C2: LingClaude webUI seam 注册 — 接入 LINGKERNEL_v1 SeamRegistry。

SeamRegistry 来源: lingflow/lingflow/coordination/seam_registry.py
  register_service(name, interface, provider) -> disposer
  declare_consumer(seam, consumer, methods)   -> disposer
  build_dependency_graph() -> {consumer: [seams]}（fail loud）

与 lingclaude/core/seam.py（进程内 SeamRegistry）的关系（P8, 2026-09-14）:
  - lingflow 那套: 跨进程/跨模块的 Service Definition 声明层（webui 等外部宿主），
    纯声明、无实体注册（register_service 只存接口元数据）
  - core/seam.py 这套: 进程内插片注册表（灵元"插片 = type"），存真实可调用实例，
    SeamRegistry.get(PROVIDER, "fs") 可直接取到 provider
  - register_capability_seams() 把能力缝同步注册到进程内 SeamRegistry，
    形成统一查询视图（P5 已把 model provider/tool 同步，P8 补齐能力缝）

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


def _lingflow_seam_registry():
    """跨仓契约：显式声明依赖 lingflow 仓库（灵元「跨仓 = 显式插片契约」）。

    1. 先经 cross_repo_seam.ensure_import_path('lingflow') 登记仓库路径
       （env 可覆盖 LINGFLOW_PATH，不依赖隐式 editable 安装布局）；
    2. 再包导入 lingflow 的 seam_registry —— 若不可用抛 ImportError，
       由调用方（api._register_webui_seam_safe）fail-soft 处置，不阻断启动。
    """
    from lingclaude.lacp.cross_repo_seam import ensure_import_path

    ensure_import_path("lingflow")
    from lingflow.coordination.seam_registry import get_seam_registry

    return get_seam_registry()


def register_webui_seam(registry: Any | None = None) -> Any:
    """注册 webui seam 并声明 lingclaude 引擎为 Consumer。

    :param registry: SeamRegistry 实例（默认用单例 get_seam_registry()）
    :returns: (service_disposer, consumer_disposer) 两个可逆注销器
    :raises SeamDefinitionError: 重复注册同名 seam 且 provider 不同（fail loud）
    """
    if registry is None:
        registry = _lingflow_seam_registry()

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
    return _lingflow_seam_registry().build_dependency_graph()


def register_capability_seams() -> None:
    """T3-1: 注册 capability seam 到 SeamRegistry（fs/shell/llm/subagent）。

    局部引入：只 4 个能力，不全面插件化。

    P8 (2026-09-14): 同步注册到进程内 SeamRegistry（SeamType.PROVIDER 槽位），
    形成统一查询视图 —— SeamRegistry.get(PROVIDER, "fs") 可查能力提供者，
    与 P5 已同步的 model provider/tool 一致。
    """
    from lingclaude.lacp.capability_seam import (
        FS_SEAM, SHELL_SEAM, LLM_SEAM, SUBAGENT_SEAM,
        register_default_providers,
    )

    registry = _lingflow_seam_registry()

    # 注册能力 seam（lingflow 声明层）
    registry.register_service("fs", FS_SEAM.interface, provider="lingclaude")
    registry.register_service("shell", SHELL_SEAM.interface, provider="lingclaude")
    registry.register_service("llm", LLM_SEAM.interface, provider="lingclaude")
    registry.register_service("subagent", SUBAGENT_SEAM.interface, provider="lingclaude")

    # 注册默认提供者
    register_default_providers()

    # P8: 同步到进程内 SeamRegistry（统一查询视图）
    #   SeamType.PROVIDER 槽位存可调用实例（CapabilitySeam 本身），
    #   与 model provider（ProviderRegistry 同步）同槽，但名字空间独立不冲突。
    #   fs/shell/llm/subagent 是能力缝名，openai/anthropic/local 是模型 provider 名。
    from lingclaude.core.seam import SeamRegistry as ProcSeamRegistry
    from lingclaude.core.seam import SeamType as ProcSeamType

    for name, seam in (("fs", FS_SEAM), ("shell", SHELL_SEAM),
                       ("llm", LLM_SEAM), ("subagent", SUBAGENT_SEAM)):
        ProcSeamRegistry.register(ProcSeamType.PROVIDER, name, seam)


if __name__ == "__main__":
    # 验证入口：注册 + 拓扑校验 + 快照
    reg = _lingflow_seam_registry()
    sd, cd = register_webui_seam(reg)
    print("seam 注册:", reg.snapshot())
    print("依赖图:", webui_seam_graph())
    sd()
    cd()
    print("dispose 后:", reg.snapshot())
