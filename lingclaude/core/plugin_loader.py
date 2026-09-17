"""灵元 R2: PluginLoader —— importlib 动态加载插件（只加载变化的插片）。

P3-2 (2026-09-14, 以灵元 1.0 为尺):
  - importlib.util.spec_from_file_location 按 manifest.entry 动态加载
  - 只用于加载「变化的插片」，绝不用于 reload 主干（灵元纪律）
  - 加载成功后按 manifest.type 注册进 SeamRegistry（注册即生效）
  - 加载失败 → Result.fail（fail fast 但不炸进程，调用方决定处置）

用法:
    loader = PluginLoader(registry=SeamRegistry)
    result = loader.load_plugin(manifest)   # -> Result[Any]（实例）
    result = loader.unload_plugin(manifest) # 从 registry 热拔插

entry 格式: "path/to/plugin.py:ClassName" 或 "path/to/plugin.py"（取模块级同名）
"""
from __future__ import annotations

import os
import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lingclaude.core.plugin_manifest import PluginManifest
from lingclaude.core.seam import SeamRegistry, SeamType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadResult:
    """加载结果（Result 语义，非异常）。"""

    ok: bool
    instance: Any = None
    error: str = ""
    manifest: PluginManifest | None = None

    @property
    def is_ok(self) -> bool:
        return self.ok

# 模块加载序号（每次加载唯一模块名用，绕开 sys.modules 缓存）
_LOAD_SEQ = [0]


class _PluginAliasProxy:
    """工具名 → 插件的别名代理（P2 深化：provides 工具名注册进 seam）。

    ToolRegistry.execute(name) 按工具名查 seam（tools.py:175），而插件注册名是
    manifest.name（read_plugin）。别名代理让工具名（read）也能命中插件：

        SeamRegistry.register(TOOL, "read", _PluginAliasProxy(instance, "read"))

    execute 自动把工具名作 name 传给插件 —— 按名分派型插件（git/web/ast/file_ops
    的 execute(name, **kwargs)）正确路由；*args/**kwargs 型插件（bash/read）
    name 进位置参数被吸收，无副作用。
    """

    __slots__ = ("name", "_instance", "_tool_name")

    def __init__(self, instance: Any, tool_name: str) -> None:
        self.name = tool_name
        self._instance = instance
        self._tool_name = tool_name

    def execute(self, **kwargs: Any) -> Any:
        return self._instance.execute(self._tool_name, **kwargs)


class PluginLoader:
    """动态插件加载器：manifest → importlib → SeamRegistry 注册。"""

    # 门禁缓存（类级）：{name@version: True} —— 跨实例共享，进程内每插件只跑一次测试
    _gate_passed: set[str] = set()

    def __init__(
        self,
        registry: type[SeamRegistry] | None = None,
        *,
        run_tests: bool | None = None,
    ) -> None:
        self._registry = registry or SeamRegistry
        self._loaded: dict[str, LoadResult] = {}
        # 2026-09-16 启动提速：插件自测默认移出启动路径（实测 6 插件逐个
        # subprocess pytest ≈40s，web_plugin 独占 32s）。默认 run_tests=False
        # 只注册不跑测试；自测改为独立入口 lingclaude --selftest（CLI）或
        # PluginLoader(run_tests=True) 显式开启（CI/发布前质量门禁仍全量跑）。
        # None → 环境变量 LINGCLAUDE_PLUGIN_SELFTEST=1 亦可开启（不改调用方）。
        if run_tests is None:
            run_tests = os.environ.get("LINGCLAUDE_PLUGIN_SELFTEST", "") == "1"
        self._run_tests_enabled = run_tests
        # 门禁缓存（类级，跨实例共享）：{name@version: True} —— 同一进程内已通过
        # 自带测试的插件不再重复跑（版本变化强制重跑，热更语义）。灵元「测试是
        # 资产，跑过一次即复用结果，不是每次加载都全量重验」。类级而非实例级：
        # 测试套件每用例新建 PluginLoader 实例，实例级缓存会让每用例重复跑
        # subprocess pytest（~2s×N），类级保证整个进程每插件只跑一次。
        self._gate_passed: set[str] = PluginLoader._gate_passed

    def _gate_key(self, manifest: PluginManifest) -> str:
        return f"{manifest.name}@{manifest.version}"

    def _run_plugin_tests(self, manifest: PluginManifest) -> tuple[bool, str] | None:
        """灵元质量门禁：跑插件自带测试（manifest.test_entry）。

        返回:
          - None        → 未声明 test_entry（跳过门禁，调用方记录 warning）
          - (True, 概要) → 测试全绿
          - (False, 错误) → 测试红 / 测试文件不存在 / 跑挂
        """
        if not manifest.test_entry:
            logger.warning(
                "PluginLoader: 插件 %s 未声明 test_entry（灵元「插片无测试=非法插片」，"
                "建议补齐自包含测试后声明）",
                manifest.name,
            )
            return None

        key = self._gate_key(manifest)
        if key in self._gate_passed:
            return True, f"{manifest.test_entry} (cached)"

        test_path = Path(manifest.test_entry)
        if not test_path.is_file():
            test_path = Path.cwd() / manifest.test_entry
        if not test_path.is_file():
            return False, f"test_entry 文件不存在: {manifest.test_entry}"

        import pytest

        try:
            from _pytest.config import ExitCode
        except ImportError:  # pragma: no cover — 极老版本兜底
            ExitCode = type("ExitCode", (), {"OK": 0})  # type: ignore[assignment]

        try:
            # 插件测试设计为「自包含」：从插件目录内 `from plugin import ...` 导入，
            # 因此必须把 cwd 切到测试文件所在目录再跑（否则嵌套 pytest 在仓库根
            # 找不到 plugin 模块）。用 subprocess 隔离 cwd，避免污染调用方进程。
            import subprocess
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--no-header", "--tb=short",
                 "-p", "no:cacheprovider", test_path.name],
                cwd=str(test_path.parent),
                capture_output=True,
                text=True,
                timeout=120,
            )
            code = proc.returncode
        except subprocess.TimeoutExpired:
            return False, "测试执行超时(120s)"
        except Exception as exc:  # noqa: BLE001 — 门禁异常转 fail，不炸进程
            return False, f"测试执行异常: {exc}"

        ok = code in (0, getattr(ExitCode, "OK", 0))
        summary = f"{test_path.name} exit={code}"
        if ok:
            self._gate_passed.add(key)
        return (ok, summary)


    def load_plugin(self, manifest: PluginManifest) -> LoadResult:
        """按 manifest 加载插件并注册进 SeamRegistry。

        幂等：同名插件已加载且文件未变 → 直接返回已加载实例。
        文件变了 → 重载（热更语义：unregister 旧实例再 register 新的）。
        """
        errors = manifest.validate()
        if errors:
            return LoadResult(False, error="manifest 非法: " + "; ".join(errors), manifest=manifest)

        # 铁律 2「停层声明」门禁（J3 制度化，2026-09-15）：
        # 每插片必须声明自己的内核 / 子插片接缝 / 实现数（停层显式化）。
        #   - 未声明 → warning 不阻断（存量兼容，审计留痕，限期补齐）
        #   - 声明了但格式非法（validate 已报）→ fail fast 拒绝入册
        if manifest.stop_layer is None:
            logger.warning(
                "PluginLoader: 插件 %s 未声明 stop_layer（铁律 2 停层显式化："
                "内核 / 子插片接缝 / 实现数），建议补齐后声明",
                manifest.name,
            )

        if not manifest.enabled:
            return LoadResult(False, error=f"插件 {manifest.name} 被禁用（enabled=false）", manifest=manifest)

        module_name, _, class_name = manifest.entry.partition(":")
        if not module_name:
            return LoadResult(False, error=f"entry {manifest.entry!r} 缺模块路径", manifest=manifest)

        # 解析模块路径（相对当前目录或绝对）
        path = Path(module_name)
        if not path.is_file():
            path = Path.cwd() / module_name
        if not path.is_file():
            return LoadResult(False, error=f"插件文件不存在: {module_name}", manifest=manifest)

        # 灵元「插片无测试 = 非法插片」质量门禁（E7, 2026-09-15）：
        # manifest 声明 test_entry（相对仓库根或绝对路径）→ 注册前强制跑测试，
        # 全绿才允许挂载；测试红 → fail fast（不注册，返回 LoadResult 错误）。
        # 未声明 test_entry → 记录 warning 不阻断（演进中：存量插件逐步补齐自包含测试）。
        # 2026-09-16 启动提速：run_tests=False（新默认）时跳过测试直接挂载，
        # 质量门禁改由 --selftest / CI 全量跑兜底（启动路径只做注册）。
        if not self._run_tests_enabled:
            logger.debug("PluginLoader: 插件 %s 跳过自测（run_tests=False 启动提速）", manifest.name)
        else:
            test_result = self._run_plugin_tests(manifest)
            if test_result is not None:
                if not test_result[0]:
                    return LoadResult(
                        False,
                        error=f"插件 {manifest.name} 自带测试未通过（灵元门禁）: {test_result[1]}",
                        manifest=manifest,
                    )
                logger.info("PluginLoader: %s 自带测试通过（%s）", manifest.name, test_result[1])

        try:
            # 每次加载用唯一模块名（时间戳），彻底绕开 sys.modules 缓存：
            # 同名模块即使已被旧加载占用，也不影响本次 exec 真正重跑。
            unique_name = f"lingclaude_plugin_{manifest.name}_{id(self)}_{_LOAD_SEQ[0]}"
            _LOAD_SEQ[0] += 1
            spec = importlib.util.spec_from_file_location(unique_name, path)
            if spec is None or spec.loader is None:
                return LoadResult(False, error=f"无法创建模块 spec: {path}", manifest=manifest)
            # 热更语义：清除 __pycache__ 字节码缓存，否则同路径文件即使
            # 已修改，exec_module 仍命中旧 .pyc（实测 mtime 变化也不重编）。
            try:
                cache_path = Path(importlib.util.cache_from_source(str(path)))
                if cache_path.is_file():
                    cache_path.unlink()
            except (OSError, ValueError):
                pass
            module = importlib.util.module_from_spec(spec)
            sys.modules[unique_name] = module
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 — 插件加载失败转 Result，不炸进程
            logger.exception("PluginLoader: 加载 %s 失败", path)
            return LoadResult(False, error=f"插件执行失败: {exc}", manifest=manifest)

        # 解析实例：entry 带类名 → 取类；否则模块级同名单例/工厂
        if class_name:
            cls = getattr(module, class_name, None)
            if cls is None:
                return LoadResult(False, error=f"插件类 {class_name} 不存在于 {path}", manifest=manifest)
            try:
                instance = cls()
            except Exception as exc:  # noqa: BLE001
                return LoadResult(False, error=f"插件实例化失败: {exc}", manifest=manifest)
        else:
            instance = getattr(module, manifest.name, None)
            if instance is None:
                return LoadResult(False, error=f"插件模块无 {manifest.name} 符号", manifest=manifest)

        # 注册进 SeamRegistry（热更：先注销旧的再注册新的）
        self._registry.unregister(manifest.type, manifest.name)
        self._registry.register(manifest.type, manifest.name, instance)
        # 2026-09-14 (P2 深化): tool 型插件把 provides 工具名也注册为别名代理 ——
        # ToolRegistry.execute(name) 按工具名查 seam（tools.py:175 get_optional），
        # 此前只注册 manifest.name（read_plugin），工具名（read）永远 miss，
        # 热拔插通道在工具执行路径上从未真正接通。别名代理 execute 自动注入
        # 工具名作 name 分派（git/web/ast/file_ops 的 execute(name, **kwargs) 正确
        # 路由；bash/read 的 *args/**kwargs 吸收 name 无副作用）。
        if manifest.type == SeamType.TOOL and manifest.provides:
            for tool_name in manifest.provides:
                if not tool_name or tool_name == manifest.name:
                    continue
                self._registry.register(
                    manifest.type, tool_name,
                    _PluginAliasProxy(instance, tool_name),
                )
        result = LoadResult(True, instance=instance, manifest=manifest)
        self._loaded[manifest.name] = result
        logger.info("PluginLoader: 加载并注册 %s/%s <- %s", manifest.type.value, manifest.name, path)
        return result

    def unload_plugin(self, manifest: PluginManifest) -> bool:
        """热拔插：从 SeamRegistry 注销。幂等。

        同时注销 provides 工具名别名代理（P2 深化：加载时注册的别名，卸载时
        必须一并清掉，否则工具名残留命中已卸载插件的代理）。
        """
        removed = self._registry.unregister(manifest.type, manifest.name)
        if manifest.type == SeamType.TOOL and manifest.provides:
            for tool_name in manifest.provides:
                if not tool_name or tool_name == manifest.name:
                    continue
                self._registry.unregister(manifest.type, tool_name)
        self._loaded.pop(manifest.name, None)
        return removed

    def load_plugins_from_dir(self, directory: str | Path) -> dict[str, LoadResult]:
        """批量加载目录下所有插件 manifest（灵元：新增插件 = 加 manifest 文件）。

        S4: PluginLoader 从「库」变「机制」——约定目录扫描 *.plugin.json，
        逐个 from_dict + load_plugin，返回 {name: LoadResult}。

        - 目录不存在 → 返回 {}（fail-soft，引擎启动无 manifest 目录不报错）
        - 单个插件失败不影响其他（各自 Result.fail，汇总可见）
        - enabled=false 的插件由 load_plugin 内部拒绝（返回 fail）
        """
        directory = Path(directory)
        results: dict[str, LoadResult] = {}
        if not directory.is_dir():
            return results
        # P0: 支持「每插片自包含目录」结构 —— 先扫 */manifest.plugin.json，
        # 再扫扁平 *.plugin.json（向后兼容旧布局）。两种都收集、去重、排序。
        manifest_files: dict[Path, None] = {}
        for mf in sorted(directory.glob("*/manifest.plugin.json")):
            manifest_files[mf] = None
        for mf in sorted(directory.glob("*.plugin.json")):
            manifest_files[mf] = None
        for manifest_file in sorted(manifest_files):
            try:
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
                manifest = PluginManifest.from_dict(data)
            except Exception as exc:  # noqa: BLE001 — 单个 manifest 坏不影响批量
                logger.warning("S4: manifest %s 解析失败: %s", manifest_file.name, exc)
                results[manifest_file.stem] = LoadResult(
                    False, error=f"manifest 解析失败: {exc}"
                )
                continue
            res = self.load_plugin(manifest)
            results[manifest.name] = res
            if not res.is_ok:
                logger.warning(
                    "S4: 插件 %s 加载失败: %s", manifest.name, res.error
                )
        return results

    def loaded(self) -> dict[str, LoadResult]:
        """已加载插件快照。"""
        return dict(self._loaded)

    def get(self, seam_type: SeamType, name: str) -> Any | None:
        """从 registry 取已注册实例（不存在返回 None）。"""
        return self._registry.get_optional(seam_type, name)
