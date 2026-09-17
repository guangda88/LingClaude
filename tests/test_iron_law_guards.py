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
from typing import Any

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


# ── N7 横向耦合守卫（2026-09-18 用户裁决升格，修订史 20260918-06）─────────
# 来源：lc 审查发现清单 P2-1（lc_mcp_guard 函数内 import agent_lingxi 内部函数
# = 插片 A import 插片 B 内部，灵犀一改 lc-guard 就断；M3 只查纵向不拦此盲区）。
# 条文（已生效）：plugins/ 下的插片模块禁止 import 其他插片目录的内部符号——
# 插片间共享只走显式公共接缝（如 plugins/agents/mcp_common.py），共享面收敛
# 为接缝后即受 M3 同款"变化走接缝"管辖。
# 豁免：与 M1-M3 同款台账（arch_exemption，guard="N7"），只缩不放。
# 升格路径：草案（2026-09-18 早）→ 用户裁决"升格" → 修订史 20260918-06 enacted
# → 条文正式入 docs/LINGYUAN_IRON_LAW.md §二守卫件套表 N7 行。
PLUGINS = SRC / "plugins"
N7_PUBLIC_SEAMS = {"mcp_common"}  # 插片间合法共享面（显式公共接缝白名单）


def _plugin_dirs() -> set[str]:
    """插件目录名集合：plugins/<域>/<X>/plugin.py 存在的 X（与共享模块区分）。

    plugins/<域>/ 直下的单文件模块（如 agents/mcp_common.py）是公共接缝不是
    插片目录——import 它合法（N7_PUBLIC_SEAMS 是语义白名单，目录扫描是事实边界）。
    """
    dirs = set()
    for domain in PLUGINS.iterdir():
        if not domain.is_dir():
            continue
        for child in domain.iterdir():
            if child.is_dir() and (child / "plugin.py").exists():
                dirs.add(child.name)
    return dirs


def _n7_scan() -> list[str]:
    """扫 plugins/ 下跨插片目录的内部 import（AST 静态口径）。

    规则：文件属 plugins/<域>/<插片A>/，其 import 指向另一插片目录
    （lingclaude.plugins.<域>.<B>...，B≠A 且 B 是插件目录）即违规；
    指向 plugins/<域>/ 直下共享模块（mcp_common 等）合法。
    函数内 lazy import 同样捕获（P2-1 正是函数内 import）。
    """
    exempt = _active_exemptions("N7")
    plugin_dirs = _plugin_dirs()
    violations = []
    for f in _py_files(PLUGINS):
        rel = _label(f)
        if exempt.get(rel, "not-listed") is None:
            continue
        allowed_lines = exempt.get(rel) or []
        # 本文件所属插片目录：plugins/<域>/<插片>/...（取第 3 段）
        parts = rel.split("/")
        if len(parts) < 3 or parts[0] != "plugins":
            continue
        own = parts[2]
        tree = _parse(f)
        if tree is None:
            continue
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            elif isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            for m in mods:
                seg = m.split(".")
                # lingclaude.plugins.<域>.<X>[.子模块]：X=另一插件目录 → 横向耦合
                if len(seg) >= 4 and seg[0] == "lingclaude" and seg[1] == "plugins":
                    target = seg[3]
                    if target != own and target in plugin_dirs:
                        if node.lineno not in allowed_lines:
                            violations.append(
                                f"{rel}:{node.lineno} import {m}（插片 {own} → {target} 内部，"
                                f"共享应走 plugins/<域>/ 直下公共接缝模块）")
    return violations


def test_n7_no_cross_plugin_internal_imports():
    """N7（律，草案）：插片间禁止 import 他插片内部符号，共享走公共接缝。"""
    violations = _n7_scan()
    assert not violations, (
        "N7 违规（草案）：插片横向耦合——import 他插片内部模块"
        "（隐性耦合：他插片一改本插片即断；共享应收敛到显式公共接缝）:\n  "
        + "\n  ".join(violations)
    )


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


def test_exemptions_not_past_review():
    """豁免复审到期自动红：豁免≠永续（债务语法化，2026-09-17 整改）。

    铁律 4 语法：M1 批量豁免 94/95「转债务语法追踪」——豁免记录必须带
    复审账期 review_due（或结构性永久豁免 permanent=true + permanent_reason）。
    review_due < 今日且未复审（granted/reviewed 未更新）即红，与债务到期同款
    语义：可以豁免，但到期必须重新定价。
    """
    from datetime import date as _date

    today = _date.today().isoformat()
    stale = []
    for key, rec in _ledger_records(T_EXEMPT):
        if rec.get("state") != "active":
            continue
        if rec.get("permanent"):
            if not rec.get("permanent_reason"):
                stale.append(f"{key}: permanent 豁免缺 permanent_reason")
            continue
        due = rec.get("review_due")
        if not due:
            stale.append(f"{key}: 豁免无 review_due 账期（豁免不得永续）")
        elif due < today:
            stale.append(f"{key}: 复审到期 {due} 未复审（复审后更新 granted 或延期）")
    assert not stale, (
        "豁免复审到期未处理（债务语法：豁免须带账期，到期未复审即红；"
        "复审通过更新 granted/reviewed，延期改 review_due）:\n  "
        + "\n  ".join(stale)
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
    """M4（法）：主干原语在从未见过的业务域上原样跑通。

    2026-09-17 失败模式声明（audit-lingke-report-verified P1，如实入档）：
    条文原文「主干测试套件级换域」，本实现验证 StateStore 原语级换域
    （人造域 order/ticket 跑通三原语）。守卫强度低于条文处已如实声明：
    测试套件级换域（整套测试跑在陌生域 fixture 上）待 CI 侧改造后升格，
    当前以原语级换域 + M4 快照纪律（单口径结论不背书新行为）过渡。
    """
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
    """M5（法）：逐级拔插片，主干运转不中断；拔光后可重建。

    2026-09-17 整改（audit-lingke-report-verified P1）：原实现只拔自造 _Probe
    （单槽位），守卫强度低于条文「逐级 unregister（1→全部）+ 各 SeamType
    兑现等级」。现升格为三段：
      ① 声明完备性：PLUG_LEVELS 必须覆盖全部 SeamType，值域 {L1,L2,L3}
        （声明义务本身的机械化；未声明=默认 L3 最严检验）。
      ② _Probe 原语级拔插（L1 替换/L3 缺席语义，保留）。
      ③ 真实运行时插片逐级拔（1→全部）：快照→逐个 unregister→每步主干
        原语（registry 查询）不崩→恢复→恢复后与快照一致。
    守卫强度边界（如实声明）：L2 降级路径（Noop 范式）的行为级验证属各插片
    J2 域（sandbox fallback 实测在册），本守卫验证注册表层面的等级兑现。
    """
    from lingclaude.core.seam import PLUG_LEVELS, SeamRegistry, SeamType

    # ① 声明完备性：每类 SeamType 必须显式声明拔插等级（M5 前置义务）
    undeclared = [st.value for st in SeamType if st not in PLUG_LEVELS]
    assert not undeclared, (
        "M5 违规：SeamType 未声明拔插等级（铁律 §二 拔插等级声明义务，"
        f"未声明默认按 L3 检验）: {undeclared}"
    )
    bad_level = [f"{st.value}={lv}" for st, lv in PLUG_LEVELS.items()
                 if lv not in {"L1", "L2", "L3"}]
    assert not bad_level, f"M5 违规：PLUG_LEVELS 值域越界（合法 L1/L2/L3）: {bad_level}"

    class _Probe:
        name = "probe"

        def run(self, *a, **k):
            return "ok"

    class _Probe2(_Probe):
        name = "probe2"

    # ② 原语级：L3 缺席 + L1 替换 + 拔光可重建
    assert SeamRegistry.get_optional(SeamType.AGENT, "never-registered") is None
    SeamRegistry.register(SeamType.AGENT, "probe", _Probe())
    assert SeamRegistry.get(SeamType.AGENT, "probe").run() == "ok"
    assert SeamRegistry.unregister(SeamType.AGENT, "probe") is True
    SeamRegistry.register(SeamType.AGENT, "probe", _Probe2())
    assert SeamRegistry.get(SeamType.AGENT, "probe").name == "probe2"
    SeamRegistry.unregister(SeamType.AGENT, "probe")
    assert SeamRegistry.get_optional(SeamType.AGENT, "probe") is None
    SeamRegistry.register(SeamType.AGENT, "probe", _Probe())
    assert SeamRegistry.get(SeamType.AGENT, "probe") is not None
    SeamRegistry.unregister(SeamType.AGENT, "probe")  # 清场，不污染其他测试

    # ③ 真实运行时插片逐级拔（1→全部）：只对当前有真实注册的 SeamType 执行；
    #    全程 try/finally 保证恢复，恢复后逐键比对快照。
    touched: list[tuple[SeamType, str, Any]] = []
    try:
        for st in SeamType:
            snapshot = dict(SeamRegistry.get_all(st))
            if not snapshot:
                continue  # 无真实实现（测试进程未启动该域）不构造假象
            names = list(snapshot)
            for i, name in enumerate(names):  # 逐级：1 → 全部
                assert SeamRegistry.unregister(st, name) is True, \
                    f"M5 拔除失败（第 {i + 1}/{len(names)} 个）：{st.value}/{name}"
                # 主干原语不崩：拔插过程中 registry 查询语义恒常
                assert SeamRegistry.get_optional(st, name) is None
                assert isinstance(SeamRegistry.list_names(st), list)
            # 全部拔光：可查、可再注册（L3 缺席裸奔语义）
            assert SeamRegistry.get_all(st) == {}
            # 恢复（逆序无必要，registry 是名字寻址的平面表）
            for name, inst in snapshot.items():
                SeamRegistry.register(st, name, inst)
                touched.append((st, name, inst))
            # 恢复后与快照一致
            assert SeamRegistry.get_all(st) == snapshot, \
                f"M5 恢复失配：{st.value} 拔后重建与快照不一致"
    finally:
        # 双保险恢复：即使中途断言失败也要还原所有动过的槽位
        for st, name, inst in touched:
            if not SeamRegistry.has(st, name):
                SeamRegistry.register(st, name, inst)
