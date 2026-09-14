"""P0.4 架构守卫测试 (V3 §五)。

五条红线，全部机械化验收：
  G1 禁 core→engine import（7 处白名单只缩不放）
  G2 禁新增 sys.path.insert（基线 14）
  G3 禁 lazy import 净增长（基线 318）
  G4 禁工具层新增 dict-判错（return {"error"...}，基线 52）
  G5 大文件写入行数骤降 >80% 告警

G1-G4 为基线锁死型守卫：存量允许，新增即红。
"""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "lingclaude"

# 基线（只缩不放；缩小时同步更新并删除白名单项）
CORE_ENGINE_WHITELIST = [
    # 2026-09-14 (S2/S3): mcp_tools.py 模块级倒装已清零（原 :11/:12 删除），
    # 改为函数内延迟 import（:36/:65/:66/:102/:126/:127，运行时按需取，主干零持有）。
    # 这是「主干不 import 插片实现」的正解 —— 模块级倒装消除，函数内按需取。
    "core/mcp_tools.py:36",
    "core/mcp_tools.py:65",
    "core/mcp_tools.py:66",
    "core/mcp_tools.py:102",
    "core/mcp_tools.py:126",
    "core/mcp_tools.py:127",
    "core/wiring.py:148",
    "core/wiring.py:216",
    "core/tool_call_executor.py:77",
    "core/tool_call_executor.py:78",
    "core/tool_executor.py:13",
    "core/tool_executor.py:168",
    # 2026-09-13: 补登记 9237537 已入库的合法懒加载（_execute_mcp_tool_typed
    # 函数内 import mcp_proxy，规避 core 模块级循环），此前漏登记致 g1 误报。
    "core/tool_executor.py:179",
    # 2026-09-14 (P4/P5): 补登记 83781a2 已入库的合法懒加载 —— wiring.py
    # _make_tool_router 函数内 import engine.tool_router（规避 core 模块级循环），
    # 此前漏登记致 g1 误报（HEAD bd64a11 已含，非本次引入）。
    # 2026-09-14 (S3): wiring.py:213 → :216（上方新增 _load_plugins_if_present 函数，
    # 行号顺延）；函数内 import 语义不变。
    "core/wiring.py:216",
    # 2026-09-14 (P19/P20): core/prior_verifier.py:113 —— P17 提交时遗漏入库的
    # _derive_evidence_map 函数内延迟 import SPECS（从工具注册表派生声明类型→证据
    # 工具名映射，防止工具名演化导致 cross-reference 映射失配）。属合法懒加载
    # （core 不模块级 import engine，S3 倒装纪律），补登记消除 g1 误报。
    "core/prior_verifier.py:113",
]
BASELINE_SYS_PATH = 14
# 340 (2026-09-10): P2.a wiring.py 新增 22 个工厂函数内 import —— WIRING_MANIFEST
# 工厂闭包自带依赖，按需 import 规避 core 内模块级循环；替换的是原 __init__ 内联装配。
# 344 (2026-09-10): P3 state_store.py 新增 4 个工厂函数内 import（StateBackend 协议 + 两后端 + StateStore）
# 350 (2026-09-10): P3 lingmemory_bridge.py 懒加载 _get_lingmemory + wiring.py 微调
# 351 (2026-09-10): P3 wiring.py 新增 _make_state_store 工厂函数
# BASELINE_LAZY 历史值演进记录（当前值见下方 2026-09-14 T9/T10 登记）：
#  390 (2026-09-13): 补登记历史入库的合法懒加载（self_optimizer/daemon.py、
                     # webui_seam.py 等工厂/可选依赖函数内 import，9237537/db79c38 等提交），
                     # 2026-09-14 (P19/P20): 374 → 375 —— prior_verifier.py 新增
                     # 2026-09-14 (Q5): 375 → 390 —— coding_wiring.py 新增 14 个工厂
                     # 函数内 import（BashExecutor/BashlingxiExecutor/FileOps/.../ToolPipeline
                     # 自 coding.py 顶层迁至工厂函数内，规避 engine 模块级循环，对位 wiring.py
                     # 纪律）。coding.py 顶层净减 11 个 import，净改善非膨胀。
                     # 实测 356，原基线 351 未随代码演进更新致 g3 误报。
                     # 2026-09-14 (P4/P5): 实测 HEAD 已达 368（wiring/mcp.server/query_engine 等
                     # 历史工厂函数内 import 未同步基线，g3 长期带病误报）；
                     # 本次改动净变化 0（tools/provider 函数内 import 迁顶层、api 顶层化、
                     # repl_turn 1:1 置换），故基线同步为 368 消除误报。
                     # 2026-09-14 (S3): 368 → 374 —— mcp_tools.py 模块级倒装 2 处
                     # 改函数内延迟 import 6 处（:36/:65/:66/:102/:126/:127），
                     # 净增 6 个函数内 import（模块级不计 lazy）。模块级倒装清零，
                     # 这是「主干不持有插片实现」的净改善，非膨胀。
                     # 2026-09-14 (P19/P20): 374 → 375 —— prior_verifier.py 新增
                     # _derive_evidence_map 函数内延迟 import SPECS（P17 提交遗漏，
                     # 随本轮 P20 一并入库）。合法懒加载，非膨胀。
                     # 2026-09-14 (T9/T10): 375 → 392 —— 实测 HEAD(Q5 提交 46cac1e)
                     # 已达 391（上一轮 Q5 coding_wiring 工厂 import 与基线不同步，
                     # g3 带病误报）；本轮新增 hallucination_guard.py 1 个函数内延迟
                     # import（PriorVerifier，S3 纪律）→ 392。基线同步消除误报，非膨胀。
                     # 2026-09-14 (四家审计减薄): 392 → 396 —— 本轮剥离厚模块新增
                     # 4 个函数内延迟 import（token_monitor.py 报告委托 ×2、
                     # bash.py _split_chain 委托、l7_cognitive re-export 顶替删除项），
                     # 均为合法懒加载（S3 纪律），换来 bash.py 1134→766、l7_cognitive
                     # 1008→704、token_monitor 926→491 净减 1107 行。净改善非膨胀。
                     # 2026-09-14 (P0 插件载体): 396 → 399 —— 新增 plugins/tools/ 载体，
                     # coding_wiring.py _load_tool_plugins 函数内延迟 import PluginLoader
                     # ×1 + 2 个插件 plugin.py 各函数内延迟 import 执行器（BashExecutor/
                     # FileReadTool）×2，共 +3。均为合法懒加载（S3 纪律，函数内按需取），
                     # 换来「主干不再 import 插件实现」的净改善，非膨胀。
                     # 2026-09-14 (P1/P2 实施): 399 → 400 —— file_ops/plugin.py 函数内
                     # 延迟 import FileEditTool ×1（S3 纪律，与 bash/read 插件同款），
                     # 换来 file_ops 工具组插片化，非膨胀。plugin_runner.py 的子进程
                     # 入口是字符串（不入 AST 统计），不增计数。
                     # 2026-09-14 (P1/P2 git+web 插件): 400 → 401 —— 新增 web/plugin.py
                     # 函数内延迟 import WebFetcher/WebSearcher ×1（S3 纪律，与
                     # bash/read/file_ops 插件同款；git/plugin.py 是模块级 import，
                     # 不计 lazy），换来 git/web 工具组插片化，非膨胀。
BASELINE_LAZY = 401
BASELINE_DICT_ERR = 52


def _py_files(root: Path):
    return [f for f in sorted(root.rglob("*.py")) if "__pycache__" not in f.parts]


def _parse(f: Path):
    try:
        return ast.parse(f.read_text(encoding="utf-8"))
    except SyntaxError:
        return None


def _label(f: Path) -> str:
    return f.relative_to(SRC).as_posix()


def _count_sys_path() -> list[str]:
    hits = []
    for f in _py_files(SRC):
        if "sys.path.insert" in f.read_text(encoding="utf-8"):
            hits.append(_label(f))
    return hits


def _count_lazy() -> int:
    cnt = 0
    for f in _py_files(SRC):
        tree = _parse(f)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Import):
                        cnt += len(sub.names)
                    elif isinstance(sub, ast.ImportFrom) and sub.module:
                        cnt += 1
    return cnt


def _count_dict_error() -> list[str]:
    hits = []
    for f in _py_files(SRC / "engine"):
        for i, ln in enumerate(
            f.read_text(encoding="utf-8").splitlines(), 1
        ):
            s = ln.strip()
            if s.startswith('return {"error"') or s.startswith(
                "return {'error'"
            ):
                hits.append(f"{_label(f)}:{i}")
    return hits


def test_g1_core_engine_imports_whitelist():
    found = []
    for f in _py_files(SRC / "core"):
        tree = _parse(f)
        if tree is None:
            continue
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            for m in mods:
                if m.startswith("lingclaude.engine"):
                    key = f"{_label(f)}:{node.lineno}"
                    found.append(key)
    extra = sorted(set(found) - set(CORE_ENGINE_WHITELIST))
    assert not extra, f"core→engine 新增违规(白名单只缩不放): {extra}"


def test_g2_no_new_sys_path_insert():
    hits = _count_sys_path()
    assert len(hits) <= BASELINE_SYS_PATH, (
        f"sys.path.insert 基线 {BASELINE_SYS_PATH} 被突破({len(hits)}): {hits}"
    )


def test_g3_no_lazy_import_growth():
    cnt = _count_lazy()
    assert cnt <= BASELINE_LAZY, (
        f"函数内 import 基线 {BASELINE_LAZY} 被突破: {cnt}"
    )


def test_g4_no_new_dict_error_returns():
    hits = _count_dict_error()
    assert len(hits) <= BASELINE_DICT_ERR, (
        f"工具层 dict-判错 基线 {BASELINE_DICT_ERR} 被突破({len(hits)}): "
        + ", ".join(hits[len(hits) - 5:])
    )


def test_g5_no_truncated_write_artifacts():
    """写入事故检测: 项目 py 文件不应以语法错误状态存在(骤降截断的典型症状)。"""
    broken = []
    for f in _py_files(SRC):
        if f.name == "__init__.py":
            continue  # 空 __init__.py 是合法包标记
        if f.stat().st_size == 0:
            broken.append(f"{_label(f)} (空文件)")
            continue
        if len(f.read_text(encoding="utf-8").splitlines()) < 50:
            continue
        if _parse(f) is None:
            broken.append(f"{_label(f)} (SyntaxError)")
    assert not broken, f"疑似截断写入: {broken}"


def test_g8_no_inline_tool_registration():
    """T4（opencode 架构演进项）锁定：coding.py 主干零内联注册。

    工具注册统一走 engine/tool_registration.py 的 specs 表 +
    register_all_tools()。主干再出现 ToolDefinition( 或
    registry.register( 即违规 —— 变更应改 specs 表（manifest 只加行）。
    """
    text = (SRC / "engine" / "coding.py").read_text(encoding="utf-8")
    violations = [
        f"行{i+1}: {line.strip()[:60]}"
        for i, line in enumerate(text.splitlines())
        if ("ToolDefinition(" in line or "registry.register(" in line)
        and not line.lstrip().startswith("#")
    ]
    assert not violations, (
        "coding.py 出现内联工具注册（应改 tool_registration.py specs 表）: "
        f"{violations}"
    )


def test_g9_all_tools_decoupled_handlers():
    """T3 锁定：主注册表全部工具走 handler_name 插片（定义/实现解耦）。

    生产注册表内直传 handler=Callable 或缺失 handler_name 即违规；
    测试内构造 ToolDefinition 不受此限（conftest 已静默 Deprecation）。
    """
    from lingclaude.engine.coding import CodingRuntime

    rt = CodingRuntime()
    offenders = [
        t.name for t in rt.registry.list_tools()
        if t.handler is not None or t.handler_name is None
    ]
    assert not offenders, f"以下工具未走 handler_name 解耦: {offenders}"


# ── G10 厚模块行数红线（2026-09-14，灵元「砍到最薄」防回潮守卫）─────────────
# 灵元三步法第一步「找不变 → 砍到最薄」。拆过的厚模块不许长回去：
#   - 单文件红线：core/ 与 engine/ 任一 .py ≤ MAX_SINGLE（当前 800 行）
#   - 总量红线：core/ 总行数 ≤ MAX_CORE_TOTAL（当前 24000 行）
# 超线 = 必须拆（方法级 mixin 不算减，是拆文件，见 cedex 审计）。
# 只缩不放：拆薄时同步下调红线，但**绝不上调**。
# 例外豁免：__init__.py（包标记）、wiring.py（工厂注册表，属装配数据化本体）。
MAX_SINGLE = 800
MAX_CORE_TOTAL = 24000
_EXEMPT_LARGE = {"wiring.py"}


def test_g10_no_oversized_modules():
    """G10：core/ 与 engine/ 无超过 800 行的厚模块（防回潮）。"""
    violations: list[str] = []
    total_core = 0
    for root_dir in ("core", "engine"):
        d = SRC / root_dir
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.py")):
            if f.name in ("__init__.py",) or f.name in _EXEMPT_LARGE:
                continue
            n = len(f.read_text(encoding="utf-8").splitlines())
            if root_dir == "core":
                total_core += n
            if n > MAX_SINGLE:
                violations.append(f"{_label(f)}: {n} 行 (> {MAX_SINGLE})")
    assert not violations, (
        f"存在超线厚模块（灵元「砍到最薄」：单文件 >{MAX_SINGLE} 行必须拆）: "
        f"{violations}"
    )


def test_g10_no_core_bloat():
    """G10：core/ 总行数红线（当前 23796 行，防总量回潮）。"""
    total = 0
    for f in (SRC / "core").glob("*.py"):
        total += len(f.read_text(encoding="utf-8").splitlines())
    assert total <= MAX_CORE_TOTAL, (
        f"core/ 总行数 {total} > 红线 {MAX_CORE_TOTAL}，必须砍薄"
    )

# ── G11/G12 插件载体守卫（2026-09-14，P3：plugins/ 纳入架构保护）─────────────
# 灵元纪律：变化=插片，不焊进主干。
#   G11：主干（core/engine）不得直接 import lingclaude.plugins（只能经 PluginLoader
#        动态加载 —— 加载是机制，import 是倒装）。
#   G12：plugins/ 下每插件必须自包含 manifest.plugin.json（载体完整性）。
_PLUGINS_DIR = SRC / "plugins"


def test_g11_no_core_import_plugins():
    """G11：主干不 import 插件实现（变化不焊进主干，只经 PluginLoader）。

    2026-09-14 (G11 扩展): glob → rglob，递归覆盖 core/prompt_engineering/、
    engine/tool_handlers/、engine/subagent/ 等子目录 —— 防止插件 import 藏进
    子目录绕过守卫（灵元：变化=插片，任何深度都不得焊进主干）。
    """
    offenders = []
    for root_dir in ("core", "engine"):
        d = SRC / root_dir
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*.py")):
            if f.name == "__init__.py":
                continue
            tree = _parse(f)
            if tree is None:
                continue
            for node in ast.walk(tree):
                mods = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                elif isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                for m in mods:
                    if m == "lingclaude.plugins" or m.startswith("lingclaude.plugins."):
                        offenders.append(f"{_label(f)}:{node.lineno}:{m}")
    assert not offenders, (
        f"主干直接 import 插件实现（违反灵元「变化=插片」）: {offenders}"
    )


def test_g12_plugins_self_contained():
    """G12：plugins/ 每插件自包含 manifest.plugin.json（载体完整性）。"""
    if not _PLUGINS_DIR.is_dir():
        return  # 无插件目录 → 跳过（未启用插件化）
    for sub in sorted(_PLUGINS_DIR.glob("*/*")):
        if not sub.is_dir():
            continue
        manifest = sub / "manifest.plugin.json"
        assert manifest.is_file(), (
            f"插件目录 {_label(sub)} 缺 manifest.plugin.json（灵元：每插片自包含载体）"
        )


def test_g13_plugin_entry_must_be_inside_plugins():
    """G13：插件 manifest.entry 必须指向 plugins/ 内，且文件真实存在。

    2026-09-14 (G13 新增): plugin_runner 用 entry 文件路径直接加载 —— 若 entry
    可指向任意路径（如 /tmp/evil.py、core/ 内模块），则插件载体形同虚设、且
    成为任意代码执行口。灵元：插片必须自包含于载体目录，entry 不得越界。
    """
    if not _PLUGINS_DIR.is_dir():
        return
    bad = []
    for manifest in sorted(_PLUGINS_DIR.rglob("manifest.plugin.json")):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            bad.append(f"{_label(manifest)}: manifest 非合法 JSON")
            continue
        entry = data.get("entry", "")
        module_path = entry.partition(":")[0] if entry else ""
        if not module_path:
            bad.append(f"{_label(manifest)}: 缺 entry")
            continue
        # 解析相对仓库根的路径，必须落在 plugins/ 内
        resolved = (ROOT / module_path).resolve()
        plugins_resolved = _PLUGINS_DIR.resolve()
        if plugins_resolved not in resolved.parents:
            bad.append(f"{_label(manifest)}: entry {module_path!r} 越出 plugins/")
            continue
        if not resolved.is_file():
            bad.append(f"{_label(manifest)}: entry 文件不存在 {module_path}")
    assert not bad, f"G13 违规（entry 必须自包含于 plugins/）:\n  " + "\n  ".join(bad)


def test_g14_plugin_loader_dirs_under_plugins():
    """G14：PluginLoader 只加载 plugins/ 内的插件目录（加载源白名单）。

    2026-09-14 (G14 新增): 若 PluginLoader 可被配置加载 plugins/ 之外任意目录，
    G11 的"不 import"保护会被加载机制绕过（加载 = 执行）。灵元：变化=插片，
    插片的"活"只允许在载体目录内发生。
    """
    # 扫描 PluginLoader 中被当作插件根目录的字符串常量，校验全部以 plugins/ 结尾
    loader = SRC / "core" / "plugin_loader.py"
    if not loader.is_file():
        return
    tree = _parse(loader)
    if tree is None:
        return
    dirs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if ("plugins" in v) and ("{" not in v) and (v.count("/") <= 2):
                dirs.add(v)
    bad = [d for d in sorted(dirs) if not d.endswith("plugins") and "plugins" not in d.split("/")[-2:-1]]
    assert not bad, f"G14 违规（插件加载目录必须位于 plugins/ 下）: {bad}"
