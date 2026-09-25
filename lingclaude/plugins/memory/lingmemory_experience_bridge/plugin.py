"""LayeredMemory(L3/L4) → 灵忆 旁路镜像桥（P3.3）——M3 第二批缝插片（core/ → plugins/memory/）。

灵元纪律：本体 bridge.py 由 PluginLoader 按 manifest 加载，register() 进
SeamType.MEMORY 缝；wiring 消费面经 SeamRegistry.get_optional(MEMORY, "lingmemory_experience") 装配
（G11：core 不 import 插件，plugins→core 单向）。ERR-05 教训：迁移验收含启动链冒烟。
"""
from __future__ import annotations

from lingclaude.core.seam import SeamRegistry, SeamType
from .bridge import LingMemoryExperienceSink


class Plugin:
    """memory 插片载体：加载即注册进 MEMORY 缝。"""

    name = "lingmemory_experience"

    def __init__(self) -> None:
        self.impl = LingMemoryExperienceSink()

    def register(self) -> None:
        SeamRegistry.register(SeamType.MEMORY, "lingmemory_experience", self.impl)
