"""TokenMonitor → 灵忆 token 双写桥（P3.4）——M3 第二批缝插片（core/ → plugins/memory/）。

灵元纪律：本体 bridge.py 由 PluginLoader 按 manifest 加载，register() 进
SeamType.MEMORY 缝；wiring 消费面经 SeamRegistry.get_optional(MEMORY, "lingmemory_token") 装配
（G11：core 不 import 插件，plugins→core 单向）。ERR-05 教训：迁移验收含启动链冒烟。
"""
from __future__ import annotations

from lingclaude.core.seam import SeamRegistry, SeamType
from .bridge import LingMemoryTokenSink


class Plugin:
    """memory 插片载体：加载即注册进 MEMORY 缝。"""

    name = "lingmemory_token"

    def __init__(self) -> None:
        self.impl = LingMemoryTokenSink()

    def register(self) -> None:
        SeamRegistry.register(SeamType.MEMORY, "lingmemory_token", self.impl)
