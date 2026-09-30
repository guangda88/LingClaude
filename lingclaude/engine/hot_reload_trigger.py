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

from lingclaude.core import policy_loader

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
        config_provider: Callable[[], Any] | None = None,
        config_factory: Callable[[Any], Any] | None = None,
        config_check_interval: float | None = None,
        store: Any = None,
        scan_interval: float | None = None,
    ) -> None:
        # E13 外置（2026-09-30）：两间隔默认参改调用时求值——显式注入优先（测试
        # 传 0.0 等假值照用），None → tuning.scan_interval / tuning.scan_config_interval
        # （yaml 热更），未配置回退代码默认（_SCAN_INTERVAL / 30.0）
        if scan_interval is None:
            scan_interval = float(
                policy_loader._tuned("scan_interval", _SCAN_INTERVAL, lo=2.0, hi=600.0)
            )
        if config_check_interval is None:
            config_check_interval = float(
                policy_loader._tuned("scan_config_interval", 30.0, lo=2.0, hi=600.0)
            )
        self._enabled = enabled
        self._tools_dir = tools_dir or TOOLS_PLUGINS_DIR
        self._agents_dir = agents_dir or AGENTS_PLUGINS_DIR
        self._store = store
        self._scan_interval = scan_interval
        self._on_tools_reload = on_tools_reload or self._default_tools_reload
        self._on_agents_reload = on_agents_reload or self._default_agents_reload
        # 环 3（2026-09-28 接通）：config 变化 → SlotManager.rebuild。
        # config_provider 读当前 config（默认读进程 config.yaml mtime 变化后的 model）；
        # config_factory(cfg) 建 provider 实例。二者可注入（测试用探针）。
        self._config_provider = config_provider or self._default_config_provider
        self._config_factory = config_factory or self._default_config_factory
        self._config_check_interval = config_check_interval
        self._config_slots: dict[str, tuple[Any, Callable[[], Any]]] = {}  # name -> (slot_manager, factory)
        self._last_config_check = 0.0
        self._config_yaml_mtime: tuple[int, int] | None = None  # (mtime_ns, size) 基线
        self._last_scan = 0.0  # 首次 check 必扫（建立基线快照）
        self._tools_snapshot: frozenset[str] | None = None
        self._agents_snapshot: frozenset[str] | None = None

    # ------------------------------------------------------------------
    # 环 3：config 变化 → SlotManager.rebuild（接通热更最后两环之一）
    # ------------------------------------------------------------------
    def register_config_slot(
        self,
        slot_name: str,
        slot_manager: Any,
        factory: Callable[[], Any],
    ) -> None:
        """注册一个随 config 变化重建的槽（进程级，CodingRuntime 装配期调用）。

        :param slot_name: 槽名（如 "model_provider"）
        :param slot_manager: 持有该槽的 SlotManager（engine 层，G1 方向合规）
        :param factory: 重建工厂（无参，返回新实例；内部读最新 config）
        幂等：同名重复注册覆盖（后注册者生效，与装配语义一致）。
        """
        self._config_slots[slot_name] = (slot_manager, factory)

    def _default_config_provider(self) -> Any:
        """默认 config 源：经 load_config 重读 config.yaml 的 model 配置（ModelProviderConfig）。

        load_config 每次都重新解析 YAML（无缓存），天然拿到热更后的值。
        """
        from lingclaude.core.config import load_config

        return load_config().model

    def _default_config_factory(self, cfg: Any) -> Any:
        """默认 config→provider 工厂：复用 create_provider（与装配同源）。"""
        from lingclaude.model.factory import create_provider

        result = create_provider(cfg)
        if result.is_ok and result.data is not None:
            return result.data
        raise RuntimeError(f"provider 构建失败: {getattr(result, 'error', 'unknown')}")

    def _config_yaml_changed(self) -> bool:
        """config.yaml mtime 是否变化（节流轮询的粗筛信号）。"""
        from lingclaude.core.config import find_config_path

        try:
            path = find_config_path()
            if path is None:
                return False
            st = path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:  # noqa: BLE001 — 配置文件不可读不反噬
            return False
        if self._config_yaml_mtime is None:
            self._config_yaml_mtime = stamp  # 首次建立基线，不触发
            return False
        if stamp != self._config_yaml_mtime:
            self._config_yaml_mtime = stamp
            return True
        return False

    def _check_config_rebuild(self, out: dict[str, Any]) -> None:
        """节流轮询 config.yaml → 变化则对注册槽做 needs_rebuild/rebuild（fail-soft）。"""
        if not self._config_slots:
            return
        now = time.monotonic()
        if (now - self._last_config_check) < self._config_check_interval:
            return
        self._last_config_check = now
        if not self._config_yaml_changed():
            return
        try:
            cfg = self._config_provider()
        except Exception:  # noqa: BLE001 — config 读取失败不阻断
            logger.debug("config provider 读取失败", exc_info=True)
            return
        rebuilt: list[str] = []
        for name, (slot_manager, factory) in list(self._config_slots.items()):
            try:
                if slot_manager.needs_rebuild(name, cfg):
                    # 用注册时传入的槽专属工厂（内部以最新 config 为准），
                    # 保证 needs_rebuild 的 cfg 判据与重建实例同源。
                    record = slot_manager.rebuild(name, factory, reason="config_hotreload")
                    rebuilt.append(name)
                    logger.info("config 热更：槽 %s 已 rebuild（epoch→%s）", name, record.epoch)
            except Exception as e:  # noqa: BLE001 — 单槽失败不影响其余
                logger.warning("config 热更 rebuild 槽 %s 失败: %s", name, e)
                out.setdefault("config_errors", {})[name] = f"{type(e).__name__}: {e}"
        if rebuilt:
            out["config_rebuilt"] = rebuilt

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
        # G11 合规：不直接 import lingclaude.plugins.agents，经 engine 内
        # 间接入口（seam_backend.reload_agent_plugins，内部走 importlib
        # 按路径装载，对齐 PluginLoader 通路语义）。
        from lingclaude.engine.subagent.seam_backend import reload_agent_plugins

        return reload_agent_plugins(store=self._store)

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

        # --- 环 3：config.yaml 变化 → 注册的槽 rebuild（模型 provider 零重启）---
        try:
            self._check_config_rebuild(out)
        except Exception:  # noqa: BLE001 —— config 热更是增强通路，不阻断主流程
            logger.debug("config hot-reload check failed", exc_info=True)

        return out

    def reset_snapshots(self) -> None:
        """清空基线快照（下次 check 重建基线，不触发）。测试用。"""
        self._tools_snapshot = None
        self._agents_snapshot = None
