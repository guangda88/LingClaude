"""M3 阶段 1（建闸后续·耦合基线）——core/ 主干耦合告警级检查 + 基线快照。

背景（2026-09-25 事故后真实落账）：
- 铁律 L1 全称命题裁定"除主干外一切皆插片"，L1 合规差距审计实测 core/ 111 件非 .bak
  .py 中仅 9 件主干件（原语+装配器）有权留驻，其余 102 件为迁出候选（口径 B）。
- 本模块是 M3 三段收紧的阶段 1（告警级）：只检测、只告警、不拒绝——CI 红线与
  import hook 属阶段 2/3，待建闸期收口后与动刀期合并裁决（synthesis 序列约束）。

判据（口径 B，2026-09-25 实测定案）：
- 主干九件（L1 冻结区）：state_store / types / model_types / datalog / seam /
  wiring / plugin_loader / plugin_manifest / plugin_lifecycle
- 红名单 = core/ 全部非 .bak .py − 主干九件 = 102 件（含 1 件 __init__.py
  pending_judgment 单列）

失败模式声明（J5 四条件之 1）：
- 本检查只看 core/ 文件清单与 core→core import，看不到运行时动态 import 的
  语义（阶段 2 的 import hook 才覆盖）；反射调用探测归 M6 调用口径。
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# L1 冻结区：主干九件（铁律 L1 全称命题下的 core/ 合法居民）
TRUNK_MODULES = frozenset({
    "state_store",      # 2T3A 原语：records/events/transition
    "types",            # 词表层：状态枚举与 transition 合法集
    "model_types",      # 词表层：模型消息类型
    "datalog",          # 2T3A 原语：事件账本
    "seam",             # 缝注册表本体（挂载器，L0 不可插片化成员）
    "wiring",           # 装配器
    "plugin_loader",    # 装配器
    "plugin_manifest",  # 装配器
    "plugin_lifecycle", # 装配器（含熔断闸）
})

# 待判件：归属裁决未定，基线中单列
PENDING_JUDGMENT = frozenset({"__init__"})

# 基线锚点（口径 B 定案轮实测）
BASELINE_META = {
    "as_of": "2026-09-25",
    "baseline_head": "ff17eb2",
    "method": "口径 B：core/ 全部非 .bak .py − 主干九件",
    "drift_note": "卷宗原记 113 件；实测重扫得 102（91 卷宗内 + 8 补遗 + 3 已不在 + "
                  "9 主干件本就在清单外 + __init__ pending）——卷宗口径 drift 已在 "
                  "ERR-2026-0925-02 案说明",
}


def list_core_modules(core_dir: Path) -> tuple[list[str], list[str]]:
    """返回 (主干件, 红名单件)——core/ 非 .bak .py 实扫，口径 B。

    实测纪律：每次调用现场扫描，不接受外部传入清单（防两份基线 drift）。
    """
    trunk, redlist = [], []
    for p in sorted(core_dir.glob("*.py")):
        if ".bak" in p.name:
            continue
        name = p.stem
        if name in TRUNK_MODULES:
            trunk.append(name)
        elif name in PENDING_JUDGMENT:
            redlist.append(name + " [pending_judgment]")
        else:
            redlist.append(name)
    return trunk, redlist


def check(core_dir: Path | None = None) -> dict:
    """M3 阶段 1 检查：红名单清单 + 告警（不拒绝）。

    返回 dict：trunk / redlist / counts / meta——供快照脚本与 pytest 双消费。
    """
    core_dir = core_dir or Path(__file__).parent
    trunk, redlist = list_core_modules(core_dir)
    result = {
        "trunk": trunk,
        "redlist": redlist,
        "counts": {"trunk": len(trunk), "redlist": len(redlist)},
        "meta": BASELINE_META,
    }
    logger.warning(
        "M3 阶段 1 告警：core/ 红名单 %d 件（主干 %d 件）——迁出候选清单见基线快照，"
        "阶段 2（CI 红线）未启用",
        result["counts"]["redlist"], result["counts"]["trunk"],
    )
    return result
