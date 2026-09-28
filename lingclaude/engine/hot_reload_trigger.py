"""灵元 P7: HotReloadTrigger —— 工具/Agent 插片「新落盘拾取」触发器。

补齐热更半径最后一块：配置层（3 槽 + 4 轻通道 + 11 yaml 策略）已热更，
工具/Agent 是仅剩的「代码层变更需重启」缺口。本触发器补上「新插件目录落盘
→ 自动注册」通路，改动小、属装配器扩展，不动主干三层。

诚实边界（重要，勿误读为「热替换」）：
  本机制是 add-only「新落盘拾取」，不是「已加载代码热替换」。Python 模块缓存
  决定已 import 的插件代码无法在不重启下被替换（registry_loader.load_all 的
  importlib.import_module 对已加载模块是 no-op）。这正是灵元红线「reload data
  不 reload module」（policy_loader.py:8）的正确体现：
    - 新增插件目录 → 本轮拾取（import 新模块，合法）；
    - 已加载插件改代码 → 不热替换，需重启（防止运行期模块对象漂移）。

触发模式（对齐 PolicyLoader，拉取式非后台线程）：
  - 不起后台线程（避免线程安全 / 测试污染 / 进程生命周期管理成本）；
  - check() 由主干在工具执行 / 派单前周期性调用（节流 _SCAN_INTERVAL 秒）；
  - 快照两目录的「插件目录名集合」，diff 出新增 → 触发对应装载入口。

两个装载入口（均为幂等 / fail-soft，复用现有机制零新增注册逻辑）：
  - 工具：tools._ensure_tool_plugins_loaded()（扫 plugins/tools/*/manifest.plugin.json，
    exactly-once 锁；新增拾取前重置 _PLUGIN_LOAD_DONE 放行重扫）；
  - Agent：plugins.agents.registry_loader.load_all()（扫 plugins/agents/*/plugin.py，
    SeamRegistry.register(AGENT) 同名覆盖幂等）。

安全纪律（J1/J5 + security 低置信默认保守）：
  - 只「拾取」不「放行」——新插件仍走 PluginLoader / registry_loader 既有装载
    通路（含各自 manifest 校验 / register() 契约 / fail-soft 隔离），本触发器
    不绕过任何安全闸，不构成新的代码注入面；
  - 快照差异仅作为「是否重扫」信号，不直接执行落盘文件；
  - 测试默认关闭（enabled=False），避免测试间目录状态串扰。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 拉取式扫描节流（秒）：对齐 policy_loader._WATCH_INTERVAL 量级，
# 避免每次工具调用都 stat 目录；新落盘最迟一个节流周期内被拾取。
_SCAN_INTERVAL = 30.0

_PKG_ROOT = Path(__file__).resolve().parent.parent  # lingclaude/
TOOLS_PLUGINS_DIR = _PKG_ROOT / "plugins" / "tools"
AGENTS_PLUGINS_DIR = _PKG_ROOT / "plugins" / "agents"


def _snapshot_dirs(base: Path, marker: str) -> frozenset[str]:
    """枚举含 marker 文件的插件目录名集合。目录缺失 → 空集（fail-soft）。"""
    try:
        if not base.is_dir():
            return frozenset()
        return frozenset(
            d.name
            for d in base.iterdir()
            if d.is_dir() and not d.name.startswith("__") and (d / marker).exists()
        )
    except OSError:  # noqa: BLE001 —— 目录不可读不反噬主流程
        logger.debug("hot-reload snapshot failed: %s", base, exc_info=True)
        return frozenset()


class HotReloadTrigger:
    """工具/Agent 插片新落盘拾取触发器（拉取式，节流）。

    :param enabled: 总开关（测试 / 关闭热更场景置 False）
    :param tools_dir / agents_dir: 可注入（测试指向临时目录）
    :param on_tools_reload / on_agents_reload: 装载动作可注入（默认走真实入口，
        测试注入探针断言触发而不真装载）
    :param store: StateStore（J4 审计落点；None=只装载不入账，透传 registry_loader）
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        tools_dir: Path | None = None,
        agents_dir: Path | None = None,
        on_tools_reload: Callable[[], Any] | None = None,
        on_agents_reload: Callable[[], Any] | None = None,
        store: Any = None,
        scan_interval: float = _SCAN_INTERVAL,
    ) -> None:
        self._enabled = enabled
        self._tools_dir = tools_dir or TOOLS_PLUGINS_DIR
        self._agents_dir = agents_dir or AGENTS_PLUGINS_DIR
        self._store = store
        self._scan_interval = scan_interval
        self._on_tools_reload = on_tools_reload or self._default_tools_reload
        self._on_agents_reload = on_agents_reload or self._default_agents_reload
        self._last_scan = 0.0  # 首次 check 必扫（建立基线快照）
        self._tools_snapshot: frozenset[str] | None = None
        self._agents_snapshot: frozenset[str] | None = None

    # ------------------------------------------------------------------
    # 默认装载动作（复用现有幂等入口）
    # ------------------------------------------------------------------
    def _default_tools_reload(self) -> Any:
        from lingclaude.engine import tools as _tools  # 函数内延迟 import（S3）

        # 放行重扫：_ensure_tool_plugins_loaded 是 exactly-once，
        # 新增拾取需重置完成标志（同一 _PLUGIN_LOAD_LOCK 下重入安全）。
        with _tools._PLUGIN_LOAD_LOCK:
            _tools._PLUGIN_LOAD_DONE = False
        ok = _tools._ensure_tool_plugins_loaded()
        return {"tools_loaded": ok}

    def _default_agents_reload(self) -> Any:
        from lingclaude.plugins.agents import registry_loader  # 延迟 import

        return registry_loader.load_all(store=self._store)

    # ------------------------------------------------------------------
    # 主通路：节流 diff 扫描
    # ------------------------------------------------------------------
    def check(self, *, force: bool = False) -> dict[str, Any]:
        """节流扫描两插件目录，diff 出新增 → 触发对应装载。

        :param force: 跳过节流（装配期 / 测试用）
        :returns: {"scanned": bool, "tools_added": [...], "agents_added": [...],
                   "tools_result": ..., "agents_result": ...}
        """
        if not self._enabled:
            return {"scanned": False, "reason": "disabled"}
        now = time.monotonic()
        if not force and (now - self._last_scan) < self._scan_interval:
            return {"scanned": False, "reason": "throttled"}
        self._last_scan = now

        out: dict[str, Any] = {"scanned": True}

        # --- 工具侧：新增 manifest.plugin.json 目录 ---
        cur_tools = _snapshot_dirs(self._tools_dir, "manifest.plugin.json")
        if self._tools_snapshot is None:
            self._tools_snapshot = cur_tools  # 基线快照，不触发（首扫视为现状）
        else:
            added = sorted(cur_tools - self._tools_snapshot)
            if added:
                out["tools_added"] = added
                try:
                    out["tools_result"] = self._on_tools_reload()
                except Exception as e:  # noqa: BLE001 —— fail-soft
                    logger.warning("hot-reload 工具装载失败: %s", e)
                    out["tools_error"] = f"{type(e).__name__}: {e}"
                # 无论成败都推进快照，避免同一新增反复触发；
                # 失败由 PluginLoader 自身 fail-soft 记账（不假活）。
                self._tools_snapshot = cur_tools

        # --- Agent 侧：新增 plugin.py 目录 ---
        cur_agents = _snapshot_dirs(self._agents_dir, "plugin.py")
        if self._agents_snapshot is None:
            self._agents_snapshot = cur_agents
        else:
            added = sorted(cur_agents - self._agents_snapshot)
            if added:
                out["agents_added"] = added
                try:
                    out["agents_result"] = self._on_agents_reload()
                except Exception as e:  # noqa: BLE001 —— fail-soft
                    logger.warning("hot-reload Agent 装载失败: %s", e)
                    out["agents_error"] = f"{type(e).__name__}: {e}"
                self._agents_snapshot = cur_agents

        return out

    def reset_snapshots(self) -> None:
        """清空基线快照（下次 check 重建基线，不触发）。测试用。"""
        self._tools_snapshot = None
        self._agents_snapshot = None
