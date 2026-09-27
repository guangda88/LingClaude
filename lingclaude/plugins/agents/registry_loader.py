"""AgentSeam 启动装配器 — 扫描 plugins/agents/*/plugin.py 并注册进 SeamRegistry。

补齐"最后一公里"：24+ 插片各自实现 AgentSeam（run/abort/status）并暴露 register()
入口，但主干启动路径此前无人调用它们——SeamRegistry 的 AGENT 槽位启动时为空，
模型侧 sub_agent 工具只能看到 inprocess/acp 内置后端，无法派单全族。

设计裁决：
- fail-soft（对齐 api.py _register_webui_seam_safe 范式）：单个插件装载失败只记
  audit record + 警告，不阻断其余插件与 lc 启动（L1 语义：缺席不崩）；
- 幂等：SeamRegistry.register 同名覆盖（热更语义），重复调用安全；
- J4：装载结果全程 record 化（agent_registry_load type，可 query 可对账）；
- J1：变化走接缝——本模块只读 plugins/agents/ 目录并调各插件 register()，
  core/ 零 diff；插件自身健康状态仍由各自 status()/铁律 8 管理；
- 显式豁免清单：已知不可装载的目录（缺 register 入口等）在此登记 + 原因，
  装载器跳过它们但记 debt 语义（不假活、不留无账红灯）。
"""
from __future__ import annotations

import importlib
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

AGENTS_ROOT = Path(__file__).parent

# 显式豁免：目录名 → 原因（J5：逃逸必须显式，不静默）
EXEMPT_DIRS: dict[str, str] = {
    "proj_family_meeting": "缺 register() 入口（2026-09-27 盘点）——会议插件经 LingBus 直调，不走 AgentSeam；入 debt 待补",
    "__pycache__": "非插件目录",
}

# 装载结果 record 的 type（J4 可 query）
RECORD_TYPE = "agent_registry_load"


def _record(store, key: str, extra: dict) -> None:
    """J4：装载结果入账（store 缺省时静默跳过——fail-soft 不反噬启动）。"""
    if store is None:
        return
    try:
        rec = store.load(RECORD_TYPE, key) or {}
        rec.update({"transited_at": time.time(), **extra})
        store.save(RECORD_TYPE, key, rec)
    except Exception:  # noqa: BLE001 —— 审计旁路不反噬主流程
        logger.debug("agent_registry_load record failed: %s", key, exc_info=True)


def discover_plugins(root: Path | None = None) -> list[str]:
    """枚举可尝试装载的插件目录名（有 plugin.py 且不在豁免清单）。"""
    base = root or AGENTS_ROOT
    out = []
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith("__"):
            continue
        if d.name in EXEMPT_DIRS:
            continue
        if not (d / "plugin.py").exists():
            continue
        out.append(d.name)
    return out


def load_all(store=None, root: Path | None = None) -> dict:
    """扫描并装载全部 AgentSeam 插片 → SeamRegistry.register(SeamType.AGENT, ...)。

    :param store: StateStore（J4 审计落点；None=只装载不入账）
    :param root: 插件根目录（默认 plugins/agents）
    :returns: {"loaded": {name: dir}, "failed": {dir: error}, "exempt": {dir: reason},
               "registered": [缝 key]}
    """
    from lingclaude.core.seam import SeamRegistry, SeamType

    base = root or AGENTS_ROOT
    loaded: dict[str, str] = {}
    failed: dict[str, str] = {}
    registered: list[str] = []

    for dirname in discover_plugins(base):
        mod_name = f"lingclaude.plugins.agents.{dirname}.plugin"
        try:
            mod = importlib.import_module(mod_name)
            reg_fn = getattr(mod, "register", None)
            if reg_fn is None:
                raise AttributeError(f"{mod_name} 无 register() 入口")
            reg_fn(SeamRegistry)
            # 注册结果以 SeamRegistry 实际状态为准（插件可能改用别名注册）
            keys = [k for k in SeamRegistry.snapshot().get(SeamType.AGENT.value, [])
                    if k not in registered]
            registered.extend(keys)
            loaded[dirname] = mod_name
            _record(store, f"load:{dirname}", {
                "dir": dirname, "module": mod_name, "state": "succeeded",
                "seam_keys": keys})
        except Exception as e:  # noqa: BLE001 —— fail-soft：单个失败不阻断
            failed[dirname] = f"{type(e).__name__}: {e}"
            logger.warning("AgentSeam 装载失败 %s: %s: %s",
                           dirname, type(e).__name__, e)
            _record(store, f"load:{dirname}", {
                "dir": dirname, "module": mod_name, "state": "failed",
                "error": str(e)[:300]})

    for dirname, reason in EXEMPT_DIRS.items():
        if dirname == "__pycache__":
            continue
        _record(store, f"exempt:{dirname}", {
            "dir": dirname, "state": "exempt", "reason": reason})

    return {"loaded": loaded, "failed": failed,
            "exempt": dict(EXEMPT_DIRS), "registered": sorted(registered)}


def registered_agents() -> list[str]:
    """当前 SeamRegistry 里已注册的 AGENT 缝 key（对账用）。"""
    from lingclaude.core.seam import SeamRegistry, SeamType
    return sorted(SeamRegistry.snapshot().get(SeamType.AGENT.value, []))
