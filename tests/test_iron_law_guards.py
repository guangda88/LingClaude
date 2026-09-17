"""灵元铁律 J5 守卫件套 M1-M5 + 债务到期守卫（2026-09-17 用户确认，见 docs/LINGYUAN_IRON_LAW.md）。

分层：律（M1-M3 静态 AST 检查，红即触发人工审查）/
      法（M4-M5 行为测试，语义终审）。
判据：
  M1 概念清白度：core/ 标识符与字符串字面量不得命中领域词汇表
  M2 特判禁令：core/ 不得对 SeamType 枚举值做具体分支判断
  M3 依赖方向契约：core/ import 闭包不得指向 plugins//engine/（含动态 import）
  M4 换域测试：合成 registry（人造业务域）装配 StateStore 跑通全原语
  M5 截肢测试：逐级 unregister 插片，主干不崩
  债务守卫：台账中 open 且过期的 arch_debt 即红（修剪语法"违规定价"执行点）

豁免只缩不放；新增豁免经 scripts/arch_ledger.py 入册（StateStore），注明理由与日期。

停层声明（铁律 2 细则 5，2026-09-17 守卫入册——守卫不是特权模块）：
  本插片内核 = 台账读取（_ledger_records/_active_exemptions，状态归 StateStore 原语）
               + AST 扫描工具（_py_files/_parse/_label）。
  子插片接缝 = ①领域词汇表（tests/fixtures/core_vocabulary.yaml，M1 可换表）
               ②豁免/债务台账（data/arch_ledger/，经 arch_ledger.py 增删）
               ③SeamType/SeamRegistry/StateStore 公开协议（M4/M5 消费面）。
  当前实现数 = 各接缝均 1（yaml 词汇表、StateStore json 后端）；替换实现
  （词汇表改存储、StateStore 换 LingYi 后端）不应触改本文件——分形递归进守卫层。
  自身豁免/债务走同款台账（arch_exemption/arch_debt），与其他插片同权：
  "算子革命先革自己的命"（2026-09-17 铁律返审结论）。
"""
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "lingclaude"
CORE = SRC / "core"
VOCAB_FILE = Path(__file__).resolve().parent / "fixtures" / "core_vocabulary.yaml"


def _py_files(base: Path):
    return sorted(base.rglob("*.py"))


def _parse(f: Path):
    try:
        return ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
    except SyntaxError:
        return None


def _label(f: Path) -> str:
    return f.relative_to(SRC).as_posix()


# ── 台账（arch_ledger，StateStore 归原语）────────────────────────────────
# 2026-09-17 铁律返审（"算子革命先革自己的命"）：豁免/债务从代码内 dict 与
# fixtures 基线文件迁入 StateStore（type=arch_exemption / arch_debt），
# 根目录 data/arch_ledger/。豁免只缩不放（arch_ledger.py exemption remove），
# 债务到期未清 = 守卫红（见 test_debts_not_expired）。
_LEDGER_ROOT = ROOT / "data" / "arch_ledger"
T_DEBT = "arch_debt"
T_EXEMPT = "arch_exemption"


def _ledger_records(record_type: str) -> list[tuple[str, dict]]:
    """读台账某 type 全部 record —— 走 StateStore API（J4 归原语，守卫自我一致）。

    key 空间经 list_keys 枚举，payload 经 load 读取；台账换后端
    （json→LingYi）时本函数不改，兑现 docstring 的分形承诺。
    """
    from lingclaude.core.state_store import StateStore

    store = StateStore(backend="json", root=_LEDGER_ROOT)
    out = []
    try:
        for key in store.list_keys(record_type):
            rec = store.load(record_type, key)
            if rec is not None:
                out.append((key, rec))
    finally:
        store.close()
    return out


def _active_exemptions(guard: str) -> dict[str, list[int] | None]:
    """{rel_path(或 guard:rel_path key): lines|None}，state=active 才算。"""
    out = {}
    for key, rec in _ledger_records(T_EXEMPT):
        if rec.get("guard") != guard or rec.get("state") != "active":
            continue
        out[rec.get("file", key)] = rec.get("lines")  # None=整文件豁免
    return out


def test_debts_not_expired():
    """债务到期自动红：台账中 open 且 due < 今日的债务使守卫失败。

    这是修剪语法"违规定价"的执行点——债务可以存在，但到期未清即红，
    且红的信息里有 location + due + reason（守卫即查询）。
    """
    from datetime import date as _date

    today = _date.today().isoformat()
    expired = [
        f"{slug}: location={rec.get('location')} due={rec.get('due')} reason={rec.get('reason')}"
        for slug, rec in _ledger_records(T_DEBT)
        if rec.get("state") == "open" and rec.get("due", "9999") < today
    ]
    assert not expired, (
        "架构债务到期未清（修剪语法：debt 到期即红；清偿用 "
        f"`scripts/arch_ledger.py debt resolve <slug>` 或申请延期）:\n  "
        + "\n  ".join(expired)
    )


# ── M1 概念清白度 ──────────────────────────────────────────────────────────
# core/ 介质的合法自有词：状态机原语 + 装配调度。词汇表之外的领域词命中即红。
# core 自有词（不在领域词汇表内，允许出现在 core/）：record/event/transition/
# state/type/seam/wiring/registry/store/plugin/manifest/loader/backend 等由
# 词汇表文件驱动判定 —— 凡 vocabulary 中的词在 core/ 出现即违例。
# 豁免走台账（arch_exemption/M1，整文件级），只缩不放。


def _load_vocabulary() -> set[str]:
    if not VOCAB_FILE.is_file():
        pytest.fail(f"领域词汇表缺失: {VOCAB_FILE}")
    # 最小 yaml 解析（避免新增依赖）：仅支持 "  - word" 列表项
    words = set()
    for ln in VOCAB_FILE.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s.startswith("- "):
            words.add(s[2:].strip())
    return words


def _ident_parts(name: str) -> set[str]:
    """标识符按 snake_case 分段（保留原始词与小写词）。"""
    parts = set()
    for seg in name.split("."):
        for p in seg.split("_"):
            if p:
                parts.add(p.lower())
    return parts


def _m1_scan() -> list[str]:
    vocab = _load_vocabulary()
    exempt = _active_exemptions("M1")  # {rel_path: None}=整文件豁免（行级不用于 M1）
    violations = []
    for f in _py_files(CORE):
        rel = _label(f)
        if rel in exempt:
            continue
        tree = _parse(f)
        if tree is None:
            continue
        hits = set()
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Name):
                names = [node.id]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, ast.arg):
                names = [node.arg]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # 字符串字面量：领域词常作为 dict key/type 字符串泄漏进主干
                for w in vocab:
                    if w in node.value:
                        names = [f"str:{w}"]
                        break
            for n in names:
                if n.startswith("str:"):
                    hits.add(n)  # 字符串命中按词计
                else:
                    hits |= {f"id:{w}" for w in _ident_parts(n) if w in vocab}
        line_hits = {h: [] for h in hits}
        # 行号定位：重扫一遍取首个命中行
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Name):
                names = [node.id]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [node.name]
            elif isinstance(node, ast.arg):
                names = [node.arg]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                for w in vocab:
                    if w in node.value:
                        names = [f"str:{w}"]
            for n in names:
                if n.startswith("str:"):
                    line_hits.setdefault(n, []).append(node.lineno)
                else:
                    for w in _ident_parts(n) & vocab:
                        line_hits.setdefault(f"id:{w}", []).append(node.lineno)
        for key, lines in sorted(line_hits.items()):
            if lines:
                violations.append(f"{rel} [{key}] 行{sorted(set(lines))[:5]}")
    return violations


def test_m1_core_concept_cleanliness():
    """M1（律）：core/ 概念清白 —— 领域词汇表命中为 0（豁免只缩不放）。"""
    violations = _m1_scan()
    assert not violations, (
        "M1 违规：core/ 出现领域概念词汇（主干须概念封闭，见铁律 M1；"
        "如确属主干自有语义，改词或经用户确认登记豁免）:\n  "
        + "\n  ".join(violations)
    )


# ── M2 特判禁令 ────────────────────────────────────────────────────────────
# core/ 及装配器不得对 SeamType 枚举成员做具体分支判断。
# 合法形态：seam.get(type) 透传、isinstance 协议检查；非法：if type == SeamType.X。
# 豁免：core/seam.py 本身（枚举定义地）、core/plugin_loader.py 存量特判（登记只缩不放）。
# M2 豁免走台账（arch_exemption/M2）：core/seam.py（枚举定义地，整文件）、
# core/plugin_loader.py（违例存量，行级 265/285，对应 arch_debt 债务
# seamtype-specialcase-plugin-loader，due 2026-10-31，清偿后豁免同撤）。


def _m2_scan() -> list[str]:
    try:
        from lingclaude.core.seam import SeamType  # noqa: F401
    except ImportError:
        return []
    members = {m.name for m in SeamType}
    exempt = _active_exemptions("M2")
    violations = []
    for f in _py_files(CORE):
        rel = _label(f)
        if exempt.get(rel, "not-listed") is None:
            continue  # 整文件豁免
        allowed_lines = exempt.get(rel) or []
        tree = _parse(f)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.If, ast.While)):
                continue
            # 收集条件子树中 SeamType.<MEMBER> 属性引用
            for sub in ast.walk(node.test):
                if (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "SeamType"
                    and sub.attr in members
                ):
                    if node.lineno not in allowed_lines:
                        violations.append(f"{rel}:{node.lineno} (SeamType.{sub.attr})")
    return violations


def test_m2_no_seamtype_special_casing():
    """M2（律）：core/ 特判禁令 —— 不含对具体 SeamType 成员的分支判断。"""
    violations = _m2_scan()
    assert not violations, (
        "M2 违规：core/ 出现 SeamType 特判（主干不得认识具体插片类型，"
        "见铁律 M2；新接缝应经注册表透传）:\n  " + "\n  ".join(violations)
    )


# ── M3 依赖方向契约 ────────────────────────────────────────────────────────
# core/ import 闭包不得指向 plugins/、engine/（含 importlib/__import__ 动态调用）。
# G11 已覆盖静态 import plugins；本守卫补 G11 缺口：engine/ 方向 + 动态 import 点。
# 豁免走台账（arch_exemption/M3，行级）：与 G1 白名单同源的 S3 纪律存量。
M3_FORBIDDEN_PREFIXES = ("lingclaude.engine", "lingclaude.plugins", "engine", "plugins")


def _m3_scan() -> list[str]:
    exempt = _active_exemptions("M3")
    violations = []
    for f in _py_files(CORE):
        rel = _label(f)
        if exempt.get(rel, "not-listed") is None:
            continue  # 整文件豁免
        allowed_lines = exempt.get(rel) or []
        tree = _parse(f)
        if tree is None:
            continue
        for node in ast.walk(tree):
            mods = []
            kind = None
            if isinstance(node, ast.ImportFrom) and node.module:
                mods, kind = [node.module], "import"
            elif isinstance(node, ast.Import):
                mods, kind = [a.name for a in node.names], "import"
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("__import__", "import_module")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                mods, kind = [node.args[0].value], "dynamic"
            for m in mods:
                if m.startswith(M3_FORBIDDEN_PREFIXES):
                    if node.lineno not in allowed_lines:
                        violations.append(f"{rel}:{node.lineno} [{kind}] {m}")
    return violations


def test_m3_dependency_direction():
    """M3（律）：core/ 依赖封闭 —— 不 import/动态加载 plugins/、engine/ 新增点。"""
    violations = _m3_scan()
    assert not violations, (
        "M3 违规：core/ 新增对 plugins//engine/ 的依赖（含动态 import；"
        "见铁律 M3；存量豁免与 G1 白名单同源，只缩不放）:\n  "
        + "\n  ".join(violations)
    )


# ── M4 换域测试 ────────────────────────────────────────────────────────────
# 合成 registry（人造业务域：订单+工单，灵元诞生同款）装配 StateStore，
# 跑通三原语 —— "换个 registry 就是另一个系统"的可测化。
def test_m4_domain_portability():
    """M4（法）：主干原语在从未见过的业务域上原样跑通。"""
    import shutil

    from lingclaude.core.state_store import StateStore

    synth_root = ROOT / "data" / "m4_synth"
    # 人造域：与灵克业务零交集（灵元诞生同款跨域验证）
    for dtype in ("order", "ticket"):
        store = StateStore(backend="json", root=synth_root)
        try:
            store.save(dtype, "synthetic-1", {"domain": "synthetic", "stage": "new"})
            rec = store.load(dtype, "synthetic-1")
            assert rec is not None, f"{dtype}: save 后 load 不得为空"
            assert rec["stage"] == "new"
            # 幂等重写 + 第二 key（原语语义在陌生域上不变）
            store.save(dtype, "synthetic-1", {"domain": "synthetic", "stage": "done"})
            store.save(dtype, "synthetic-2", {"stage": "new"})
            assert store.load(dtype, "synthetic-1")["stage"] == "done"
            assert store.load(dtype, "synthetic-2")["stage"] == "new"
            assert store.load(dtype, "no-such-key") is None
        finally:
            store.close()
            shutil.rmtree(synth_root, ignore_errors=True)


# ── M5 截肢测试 ────────────────────────────────────────────────────────────
# 逐级 unregister 插片：主干与其余插片不感知、不崩溃（J2 的机械化，
# 覆盖 L1 替换/L3 缺席语义 —— 拔光后 registry 仍可查、可再注册）。
def test_m5_amputation():
    """M5（法）：逐级拔插片，主干运转不中断；拔光后可重建。"""
    from lingclaude.core.seam import SeamRegistry, SeamType

    class _Probe:
        name = "probe"

        def run(self, *a, **k):
            return "ok"

    class _Probe2(_Probe):
        name = "probe2"

    # L3 缺席：get_optional 返回 None，主干原语照常
    assert SeamRegistry.get_optional(SeamType.AGENT, "never-registered") is None
    # L1 替换：换实现不崩（probe → probe2 → probe）
    SeamRegistry.register(SeamType.AGENT, "probe", _Probe())
    assert SeamRegistry.get(SeamType.AGENT, "probe").run() == "ok"
    assert SeamRegistry.unregister(SeamType.AGENT, "probe") is True
    SeamRegistry.register(SeamType.AGENT, "probe", _Probe2())
    assert SeamRegistry.get(SeamType.AGENT, "probe").name == "probe2"
    # 拔光后 registry 仍可用（可再注册，主干零依赖具体插片）
    SeamRegistry.unregister(SeamType.AGENT, "probe")
    assert SeamRegistry.get_optional(SeamType.AGENT, "probe") is None
    SeamRegistry.register(SeamType.AGENT, "probe", _Probe())
    assert SeamRegistry.get(SeamType.AGENT, "probe") is not None
    SeamRegistry.unregister(SeamType.AGENT, "probe")  # 清场，不污染其他测试
