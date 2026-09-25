"""MemoryStore → 灵忆 records 旁路镜像桥（M1 豁免销账对象，core 零消费点）——M3 第二批缝插片（core/ → plugins/memory/）。

灵元纪律：本体 bridge.py 由 PluginLoader 按 manifest 加载，register() 进
SeamType.MEMORY 缝；wiring 消费面经 SeamRegistry.get_optional(MEMORY, "lingmemory_memstore") 装配
（G11：core 不 import 插件，plugins→core 单向）。ERR-05 教训：迁移验收含启动链冒烟。
"""
from __future__ import annotations

from lingclaude.core.seam import SeamRegistry, SeamType
from .bridge import LingMemoryStoreSink


class Plugin:
    """memory 插片载体：加载即注册进 MEMORY 缝。"""

    name = "lingmemory_memstore"

    def __init__(self) -> None:
        self.impl = LingMemoryStoreSink()

    def register(self) -> None:
        SeamRegistry.register(SeamType.MEMORY, "lingmemory_memstore", self.impl)
