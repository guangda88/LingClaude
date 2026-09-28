"""兼容壳（2026-09-28，G11 合规化）：实现已归位 engine/subagent/seam_backend.py。

原由：本桥是「AGENT 缝 → SubagentBackend」的主干适配机制，与具体插件实现无关，
不应居 plugins/ 层（致主干 import 插件目录，违反 G11「变化不焊进主干」）。
本文件保留纯 re-export，防外部引用路径断裂；新代码请直接 import
lingclaude.engine.subagent.seam_backend。
"""
from lingclaude.engine.subagent.seam_backend import (  # noqa: F401
    AgentSeamBackend,
    install_family_backends,
)
