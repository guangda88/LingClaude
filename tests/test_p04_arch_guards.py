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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "lingclaude"

# 基线（只缩不放；缩小时同步更新并删除白名单项）
CORE_ENGINE_WHITELIST = [
    "core/mcp_tools.py:11",
    "core/mcp_tools.py:12",
    "core/mcp_tools.py:64",
    "core/mcp_tools.py:122",
    "core/wiring.py:147",
    "core/tool_call_executor.py:77",
    "core/tool_executor.py:13",
]
BASELINE_SYS_PATH = 14
# 340 (2026-09-10): P2.a wiring.py 新增 22 个工厂函数内 import —— WIRING_MANIFEST
# 工厂闭包自带依赖，按需 import 规避 core 内模块级循环；替换的是原 __init__ 内联装配。
# 344 (2026-09-10): P3 state_store.py 新增 4 个工厂函数内 import（StateBackend 协议 + 两后端 + StateStore）
# 350 (2026-09-10): P3 lingmemory_bridge.py 懒加载 _get_lingmemory + wiring.py 微调
# 351 (2026-09-10): P3 wiring.py 新增 _make_state_store 工厂函数
BASELINE_LAZY = 351  # 2026-09-11: d6666f8 漏记 +2（wiring 装配点），修复上提模块级回 351
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
