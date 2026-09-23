"""Q5 (2026-09-14): CodingRuntime 工具装配数据化（对位 QueryEngine WIRING_MANIFEST）。

把 coding.py _setup_tools() 里 42 行硬编码装配（BashExecutor/BashlingxiExecutor/
FileOps/FileEditTool/FileReadTool/GrepTool/ToolRegistry/PermissionContext/
StructureEvaluator/SynchronousOptimizer/OptimizationAdvisor/STTEngine/ToolPipeline）
收敛为 manifest + 工厂注册表，由 assemble_coding() 统一装配 —— 主干不再直接
构造 handler 实例（P18/Q5 方向：主干不知插片）。

不变式（Q5 验收口径）：
- 新增协作者 = 注册工厂 + manifest 加一行，coding.py 主干 diff 为 0
- 装配语义与原 _setup_tools() 逐字对齐（回归网：tests/test_coding.py 全量）
- 工厂函数内延迟 import（engine 内部依赖，规避模块级循环；对位 wiring.py 纪律）

唯一装配顺序依赖：tool_pipeline 构造时绑定 registry（构造期持引用），
故 registry 条目必须先于 tool_pipeline 装配 —— 与 QueryEngine 顺序无关不变式
不同，此处以 manifest 顺序表达依赖（单条、显式注释）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodingWiringContext:
    """装配上下文：工厂允许引用的全部外部依赖。

    runtime: 正在装配的 CodingRuntime 实例（工厂普遍需要 config / 已装配属性）。
    sandbox_policy: T0-10 沙箱策略（由调用方按 LINGCLAUDE_SANDBOX_MODE 构造）。
    """

    runtime: Any
    sandbox_policy: Any = None


@dataclass(frozen=True)
class CodingWiringSpec:
    """manifest 条目：装到 runtime 上的一个属性。"""

    attr: str
    factory: Callable[[CodingWiringContext], Any]
    note: str = ""


# ---------------------------------------------------------------------------
# 工厂（构造语义与原 _setup_tools() 逐字对齐）
# ---------------------------------------------------------------------------
def _make_bash(ctx: CodingWiringContext) -> Any:
    from lingclaude.engine.bash import BashExecutor

    return BashExecutor(
        timeout=ctx.runtime.config.optimizer.timeout_seconds,
        sandbox_policy=ctx.sandbox_policy,
    )


def _make_bash_lingxi(ctx: CodingWiringContext) -> Any:
    # P0 安全对齐(2026-09-11): bash_lingxi 默认继承 bash.py 同源黑名单
    # (见 bash_lingxi._DEFAULT_LINGXI_BLOCKED), handler 层另有 sensitive_path_gate。
    # allowed_commands=None 表示不设白名单(黑名单制, 与 bash 通道同语义)。
    from lingclaude.engine.bash_lingxi import BashlingxiExecutor

    return BashlingxiExecutor(
        timeout=ctx.runtime.config.optimizer.timeout_seconds,
    )


def _make_file_ops(ctx: CodingWiringContext) -> Any:
    from lingclaude.engine.file_ops import FileOps

    return FileOps()


def _make_file_edit(ctx: CodingWiringContext) -> Any:
    from lingclaude.engine.file_edit import FileEditTool

    return FileEditTool()


def _make_file_read(ctx: CodingWiringContext) -> Any:
    from lingclaude.engine.file_read import FileReadTool

    return FileReadTool()


def _make_grep_tool(ctx: CodingWiringContext) -> Any:
    from lingclaude.engine.grep import GrepTool

    return GrepTool()


def _make_registry(ctx: CodingWiringContext) -> Any:
    from lingclaude.engine.tools import ToolRegistry

    return ToolRegistry()


def _make_permissions(ctx: CodingWiringContext) -> Any:
    from lingclaude.core.permissions import PermissionContext

    return PermissionContext.from_config(
        deny_tools=ctx.runtime.config.permissions.deny_tools,
        deny_prefixes=ctx.runtime.config.permissions.deny_prefixes,
        mode=getattr(ctx.runtime.config.permissions, "mode", "ask"),
    )


def _make_evaluator(ctx: CodingWiringContext) -> Any:
    from lingclaude.self_optimizer import StructureEvaluator

    return StructureEvaluator()


def _make_optimizer(ctx: CodingWiringContext) -> Any:
    from lingclaude.self_optimizer import SynchronousOptimizer

    return SynchronousOptimizer()


def _make_advisor(ctx: CodingWiringContext) -> Any:
    from lingclaude.self_optimizer import OptimizationAdvisor

    return OptimizationAdvisor()


def _make_stt(ctx: CodingWiringContext) -> Any:
    # LINGKERNEL_v1 #2: stt 引擎原在注册块中部创建 — 注册数据外迁后仍属运行时构造
    from lingclaude.engine.stt import STTEngine

    return STTEngine()


def _make_tool_pipeline(ctx: CodingWiringContext) -> Any:
    from lingclaude.engine.tool_pipeline import ToolPipeline
    from lingclaude.engine.verification_gate import CRITICAL_TOOLS, WRITE_SCOPED_TOOLS

    return ToolPipeline(
        ctx.runtime.registry,  # 依赖 registry：必须在其后装配（见模块 docstring）
        write_scoped_tools=WRITE_SCOPED_TOOLS,
        critical_tools=CRITICAL_TOOLS,
        timeout_seconds=ctx.runtime.config.optimizer.timeout_seconds,
    )


class LazyToolPlugins:
    """启动提速（2026-09-24 第二轮）：工具插片惰性句柄。

    装配期只挂本句柄（O(1)，不 exec_module）；真实装载收口到
    tools.py::_ensure_tool_plugins_loaded（warm 线程 + 首调用兜底共用，
    exactly-once）。__bool__ 恒真：保持旧契约「装配后 runtime.tool_plugins
    非空」与 manifest 14 项 wired 名单不变（test_coding 回归网）。
    """

    __slots__ = ("_runtime",)

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def __bool__(self) -> bool:
        return True

    def __repr__(self) -> str:
        from lingclaude.engine import tools as _tools

        return (
            f"<LazyToolPlugins dir={_tools._PLUGINS_DIR!r} "
            f"loaded={_tools._PLUGIN_LOAD_DONE} err={_tools._PLUGIN_LOAD_ERROR!r}>"
        )

    def warm(self) -> None:
        """后台热身：由 cli 在欢迎横幅后起线程调用（非阻塞）。"""
        from lingclaude.engine.tools import _ensure_tool_plugins_loaded

        _ensure_tool_plugins_loaded()

    def load_now(self) -> list[str]:
        """同步装载（selftest/调试用）。返回当前已装载插件名（失败返回 []）。"""
        from lingclaude.engine.tools import _ensure_tool_plugins_loaded

        if not _ensure_tool_plugins_loaded():
            return []
        from lingclaude.core.seam import SeamRegistry, SeamType

        return sorted(SeamRegistry.list_names(SeamType.TOOL))


def _load_tool_plugins(ctx: CodingWiringContext) -> Any:
    """P0: 工具插片载体（plugins/tools/，注册进 SeamRegistry.TOOL）。

    灵元纪律：
    - 变化（工具实现）= 插片，目录内自包含 manifest + 实现。
    - 目录不存在/为空 → 装载返回空（fail-soft，不影响现有 SPECS 34 工具回归）。
    - 加载成功后注册进 SeamRegistry.TOOL，ToolRegistry.execute 优先走插件
      （tools.py Q1 热拔插通道：外部换血代理优先于内部 handler）。

    2026-09-24 启动提速：装载从构造期移出 —— 本工厂只返回惰性句柄，
    真实装载见 tools.py::_ensure_tool_plugins_loaded（warm + miss 回填）。
    """
    return LazyToolPlugins(ctx.runtime)


def load_tool_plugins_selftest() -> int:
    """插件自测独立入口（lingclaude selftest，2026-09-16 启动提速配套）。

    以 run_tests=True 全量跑各插件自带测试（灵元 E7 质量门禁），输出
    通过/失败摘要；有失败返回 1（CI/发布前门禁可据此拦截）。启动路径
    已默认跳过自测，本函数是质量门禁的兜底执行点。
    """
    import logging
    from lingclaude.core.plugin_loader import PluginLoader

    logger.setLevel(logging.INFO)
    plugins_dir = "lingclaude/plugins/tools"
    results = PluginLoader(run_tests=True).load_plugins_from_dir(plugins_dir)
    ok = [name for name, r in results.items() if r.is_ok]
    bad = {name: r.error for name, r in results.items() if not r.is_ok}
    print(f"插件自测: {len(ok)}/{len(results)} 通过")
    for name in sorted(ok):
        print(f"  ✅ {name}")
    for name, err in sorted(bad.items()):
        print(f"  ❌ {name}: {err}")
    return 1 if bad else 0


# ---------------------------------------------------------------------------
# CODING_WIRING_MANIFEST — 与原 _setup_tools() 逐字对齐
# 注意：registry 必须先于 tool_pipeline（构造期依赖），顺序即装配顺序。
# ---------------------------------------------------------------------------
CODING_WIRING_MANIFEST: tuple[CodingWiringSpec, ...] = (
    CodingWiringSpec("bash", _make_bash, note="bash 执行器（T0-10 沙箱策略）"),
    CodingWiringSpec("bash_lingxi", _make_bash_lingxi, note="lingxi MCP bash 通道（同源黑名单）"),
    CodingWiringSpec("file_ops", _make_file_ops, note="文件操作"),
    CodingWiringSpec("file_edit", _make_file_edit, note="文件编辑（带备份回滚）"),
    CodingWiringSpec("file_read", _make_file_read, note="文件读取"),
    CodingWiringSpec("grep_tool", _make_grep_tool, note="grep 搜索"),
    CodingWiringSpec("registry", _make_registry, note="工具注册表（register_all_tools 消费）"),
    CodingWiringSpec("tool_plugins", _load_tool_plugins, note="P0: 工具插件载体（plugins/tools/，2026-09-24 起惰性装载：LazyToolPlugins 句柄，warm+miss 回填见 tools.py）"),
    CodingWiringSpec("permissions", _make_permissions, note="权限上下文（deny/mode）"),
    CodingWiringSpec("evaluator", _make_evaluator, note="结构评估"),
    CodingWiringSpec("optimizer", _make_optimizer, note="同步优化器"),
    CodingWiringSpec("advisor", _make_advisor, note="优化顾问"),
    CodingWiringSpec("stt", _make_stt, note="语音识别引擎"),
    CodingWiringSpec("tool_pipeline", _make_tool_pipeline, note="5 段工具管线（依赖 registry）"),
)


def assemble_coding(ctx: CodingWiringContext, overrides: dict[str, Any] | None = None) -> list[str]:
    """按 manifest 装配 runtime 属性，返回实际装配的属性名列表。

    overrides 提供 seam：命中属性直接注入，跳过工厂构造
    （对位 QueryEngine wiring.assemble 的 P2.c 接缝语义）。
    """
    wired: list[str] = []
    for spec in CODING_WIRING_MANIFEST:
        if overrides and spec.attr in overrides:
            setattr(ctx.runtime, spec.attr, overrides[spec.attr])
            wired.append(spec.attr)
            continue
        setattr(ctx.runtime, spec.attr, spec.factory(ctx))
        wired.append(spec.attr)
    return wired
