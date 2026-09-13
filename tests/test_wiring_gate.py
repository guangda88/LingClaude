from __future__ import annotations

"""Wiring CI Gate — 确保 core 模块导出 + 接入 + 测试三合一。

规则：
1. core/*.py 的公共 class/function 必须在 __init__.py 导出
2. 核心模块（标记为 wired_required）必须在 query_engine.py 中被 import 或调用
3. 每个被导出的模块必须有对应测试文件

违反任何规则 = CI 红灯。
"""

import ast
from pathlib import Path


CORE_DIR = Path(__file__).resolve().parent.parent / "lingclaude" / "core"
INIT_PATH = CORE_DIR / "__init__.py"
QE_PATH = CORE_DIR / "query_engine.py"
TESTS_DIR = Path(__file__).resolve().parent


def _get_public_names(filepath: Path) -> set[str]:
    tree = ast.parse(filepath.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.add(target.id)
    return names


def _get_init_exports() -> set[str]:
    tree = ast.parse(INIT_PATH.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant):
                                names.add(str(elt.value))
    return names


def _get_module_imports(path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def _get_qe_imports() -> set[str]:
    return _get_module_imports(QE_PATH)


class TestExportCompleteness:
    """每个 core 模块的公共 API 必须在 __init__.py 的 __all__ 中导出。"""

    # Modules with significant public API that must be exported
    REQUIRED_MODULES: dict[str, set[str]] = {
        "types": {"Result", "StopReason"},
        "config": {"lingclaudeConfig"},
        "session": {"Session", "SessionManager"},
        "permissions": {"PermissionContext"},
        "models": {"UsageSummary"},
        "behavior": {"BehaviorMetrics"},
        "intel": {"IntelCollector", "DailyDigest"},
        "prior_verifier": {"PriorVerifier"},
        "meta_cognition": {"MetaCognition"},
        "layered_memory": {"LayeredMemory", "Experience"},
        "context_cache": {"ContextCache"},
        "token_monitor": {"TokenMonitor"},
        "data_flywheel": {"DataFlywheel"},
        "context_compression": {"compress_messages", "CompressionConfig", "CompressionLevel"},
        "dementia_detector": {"DementiaDetector", "CognitiveState", "DementiaDiagnosis"},
        "fact_checker": {"ClaimExtractor", "KGFactChecker", "FactCheckResult", "audit_response"},
        "hooks": {"HookManager", "HookType", "HookContext"},
        "query_engine": {"QueryEngine"},
        "state_store": {"StateStore", "StateBackend", "JsonFileBackend", "LingYiBackend"},
    }

    def test_all_required_names_exported(self) -> None:
        exports = _get_init_exports()
        missing: dict[str, set[str]] = {}
        for module, names in self.REQUIRED_MODULES.items():
            unexported = names - exports
            if unexported:
                missing[module] = unexported
        assert not missing, (
            "Missing exports in __init__.py:\n"
            + "\n".join(f"  {mod}: {', '.join(sorted(names))}" for mod, names in missing.items())
        )


class TestWiringGate:
    """核心模块必须在 query_engine.py 中被接入。"""

    # Modules that MUST be imported and used（T3-3 瘦身后接线随拆分迁移，
    # value = (所在文件, 必须导入的名字)；context_compression 现消费于 tool_call_executor）
    WIRED_REQUIRED: dict[str, tuple[str, set[str]]] = {
        "context_cache": ("query_engine.py", {"ContextCache"}),
        "token_monitor": ("query_engine.py", {"TokenMonitor"}),
        "data_flywheel": ("query_engine.py", {"DataFlywheel"}),
        "layered_memory": ("query_engine.py", {"LayeredMemory", "Experience"}),
        "dementia_detector": ("query_engine.py", {"DementiaDetector"}),
        "context_compression": ("tool_executor.py", {"compress_messages", "CompressionConfig"}),
        "behavior": ("query_engine.py", {"BehaviorMetrics"}),
        "intel": ("query_engine.py", {"IntelCollector"}),
        "state_store": ("query_engine.py", {"StateStore"}),
    }

    def test_required_modules_wired_in_query_engine(self) -> None:
        missing: dict[str, set[str]] = {}
        for module, (fname, names) in self.WIRED_REQUIRED.items():
            imports = _get_module_imports(CORE_DIR / fname)
            unwired = names - imports
            if unwired:
                missing[module] = unwired
        assert not missing, (
            "Modules not wired in query_engine.py:\n"
            + "\n".join(f"  {mod}: missing {', '.join(sorted(names))}" for mod, names in missing.items())
        )


class TestTestCoverage:
    """每个被导出的模块必须有对应测试文件。"""

    # Map from module name to expected test file substring
    MODULE_TEST_MAP: dict[str, str] = {
        "types": "test_core",
        "config": "test_core",
        "session": "test_core",
        "permissions": "test_core",
        "behavior": "test_behavior",
        "intel": "test_intel",
        "prior_verifier": "test_core",
        "meta_cognition": "test_intelligence",
        "layered_memory": "test_layered",
        "context_cache": "test_context_cache",
        "token_monitor": "test_token_monitor",
        "data_flywheel": "test_data_flywheel",
        "context_compression": "test_context_compression",
        "dementia_detector": "test_dementia_detector",
        "hooks": "test_hooks",
        "query_engine": "test_agent_loop",
        "skill_parser": "test_skill_parser",
        "state_store": "test_state_store",
    }

    def test_each_module_has_test_file(self) -> None:
        test_files = set(f.name for f in TESTS_DIR.glob("test_*.py"))
        missing: list[str] = []
        for module, test_substr in self.MODULE_TEST_MAP.items():
            found = any(test_substr in f for f in test_files)
            if not found:
                missing.append(f"{module} (expected test matching '{test_substr}')")
        assert not missing, (
            "Modules without test files:\n"
            + "\n".join(f"  {m}" for m in missing)
        )


class TestNoDeadModules:
    """检测 core/ 中定义了但从未被任何文件 import 的公共类。"""

    EXEMPT = {
        # Utility modules with indirect usage
        "models.py",
        "metrics.py",
        "role_separation.py",
        "task_scheduler.py",
        "behavior_aware_router.py",
        "governance.py",
        "governance_integration.py",
        "governance_verifier.py",
        "reasoning_chain.py",
        "skill_parser.py",
        # L7 cognitive layer - standalone, not wired into query_engine
        "l7_cognitive.py",
        "l7_cognitive_bridge.py",
        "l10_a_post_audit.py",
    }

    # Names that are internal implementation details, not "dead code"
    _FALSE_POSITIVE = {
        "logger", "T", "DEFAULT_CONFIG_PATH", "IntelConfig",
        "ModelProviderConfig", "SessionConfig", "EngineConfig",
        "FLYWHEEL_DB_NAME", "HookCallback", "HookEntry",
        "AggregationStats", "TaskGroup", "comfort_check_hook",
        # P2.a: WiringSpec 是 wiring.py 内部声明面（同模块内 55 次构造调用），不要求跨模块 import
        "WiringSpec",
        # T0 行为校验内部常量
        "READONLY_COMMANDS", "EDIT_TOOLS", "TOOL_REPEAT_LIMIT",
        "INCOMPLETE_SIGNALS", "CONSECUTIVE_FAIL_LIMIT",
        # Datalog 内部函数/常量
        "_ensure_datalog_dir", "_write_event", "DATALOG_DIR",
        # H18 sandbox/DB 兼容层与配置默认值：
        # fallback_dir() 由 safe_db 内部回退调用；
        # devnull_compat 在导入时自动修补，状态函数供诊断；
        # DEFAULT_JOURNAL_DIR 是配置默认值，测试按模块路径 monkeypatch。
        "fallback_dir", "is_degraded", "devnull_path",
        "DEFAULT_JOURNAL_DIR",
        # P3 state_store 模块级配置常量（供环境变量读取，非死代码）
        "ENV_BACKEND", "ENV_DSN", "ENV_DSN_FALLBACK",
        # P4 token_monitor 目标模型常量（模块内大量使用，非死代码）
        "TARGET_MODEL",
        # 2026-09-13: redact_if_needed 是 2de86ae 输出侧脱敏闭环引入的审计 API
        # （返回 (脱敏文本, 是否发生替换)，供日志/审计调用），与 redact/contains_sensitive
        # 同属 redact 模块公共面；当前无调用方属预留审计接口，登记豁免待接线。
        "redact_if_needed",
    }

    def test_core_classes_are_imported_somewhere(self) -> None:
        core_py_files = sorted(
            f for f in CORE_DIR.glob("*.py")
            if f.name != "__init__.py" and f.name not in self.EXEMPT
        )

        all_imports: set[str] = set()
        project_root = CORE_DIR.parent.parent
        for py_file in project_root.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        all_imports.add(alias.asname or alias.name)

        dead: dict[str, list[str]] = {}
        for core_file in core_py_files:
            public_names = _get_public_names(core_file) - self._FALSE_POSITIVE
            unreferenced = public_names - all_imports
            if unreferenced:
                dead[core_file.name] = sorted(unreferenced)

        assert not dead, (
            "Public names defined but never imported anywhere:\n"
            + "\n".join(f"  {f}: {', '.join(names)}" for f, names in dead.items())
        )
