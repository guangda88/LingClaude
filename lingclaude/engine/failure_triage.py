"""测试失败分诊（NanoJev 契约消费层消费点②，2026-09-22）。

对齐 NanoJev/Jev 测试失败分诊场景：Coding Agent 遇到测试失败/编译错误时，
先判定错误类型，再决定是否值得调用昂贵的前沿模型——0 LLM token 确定性处置：

| 失败类别 | 信号 | 处置 | LLM token |
|---------|------|------|-----------|
| 依赖缺失 | ModuleNotFoundError / No module named / Cannot find module | 确定性安装命令（pip/npm install） | 0 |
| 瞬时故障 | 网络超时 / ECONNREFUSED / 端口占用 / timeout | 自动重试一次（0 代码改动） | 0 |
| 循环重构 | 同一修复尝试 ≥3 次（doom loop） | abort 门控（exit 1 语义）停止循环告警 | 0 |
| 简单拼写/格式 | 语法错误 / lint | 路由本地脚本或更轻量模型 | 低 |

红线（同 verify_cadence）：主路径纯字符串模式匹配（0 模型调用，可离线单测）；
spec_decision boolean 头只作**不确定兜底门控**——三类都没命中时问"该失败是否
值得 escalate 给 LLM"（Noul），P 低于阈值则就地降级处理，不烧推理。

停层声明（铁律 2 细则 5）：
- 内核 = TriageVerdict（纯判定函数，无 I/O）
- 接缝 = triage_failure(error_text, history) -> TriageVerdict 协议
- 实现 = 单实现（正则分类 + 可选 spec_decision 兜底门控）
边界纪律：主分类零模型调用；spec_decision 故障时 fail-open（分类照常返回，
门控跳过，不反噬既有判定）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


class TriageClass:
    """失败类别（枚举面，对齐 NanoJev 三类 + 简单拼写第四类）。"""
    MISSING_DEP = "missing_dependency"
    TRANSIENT = "transient"
    DOOM_LOOP = "doom_loop"
    SYNTAX_FORMAT = "syntax_format"
    UNCLASSIFIED = "unclassified"


@dataclass
class TriageVerdict:
    """分诊判定结果。"""
    triage_class: str                    # TriageClass 值
    action: str                          # 确定性处置指令（install/retry/abort/route_light）
    detail: str = ""                     # 命中信号（供日志/模型可见）
    escalate_to_llm: bool = False        # 是否值得升级给昂贵模型（多数失败=不值得）
    confidence: float = 1.0             # 判定置信（正则命中=1.0，兜底门控=概率值）

    def to_dict(self) -> dict[str, Any]:
        return {
            "triage_class": self.triage_class,
            "action": self.action,
            "detail": self.detail,
            "escalate_to_llm": self.escalate_to_llm,
            "confidence": self.confidence,
        }


# ── 失败信号正则（0 模型调用，可离线单测）──
_DEP_MISSING_RES = (
    re.compile(r"ModuleNotFoundError|No module named|ImportError:\s*cannot import", re.I),
    re.compile(r"Cannot find module|Cannot find package|ERR_MODULE_NOT_FOUND", re.I),
    re.compile(r"(command|Package) .* not found|npm ERR! 404", re.I),
)
_TRANSIENT_RES = (
    re.compile(r"timeout|timed?\s*out|ETIMEDOUT|socket hang up", re.I),
    re.compile(r"ECONNREFUSED|ECONNRESET|EAI_AGAIN|ENETUNREACH|EHOSTUNREACH", re.I),
    re.compile(r"Connection (reset|refused|aborted)|Network is unreachable", re.I),
    re.compile(r"port .* (already )?in use|Address already in use", re.I),
    re.compile(r"503 (Service Unavailable|temporar)|502|504|rate ?limit|too many requests", re.I),
)
_SYNTAX_RES = (
    re.compile(r"SyntaxError|Unexpected token|Unexpected identifier", re.I),
    re.compile(r"IndentationError|TabError", re.I),
    re.compile(r"expected (one of )?[;,]\b|\bunexpected end of input\b", re.I),
)
# 循环重构信号：同一错误在 history 里重复出现 ≥ 阈值
_DOOM_LOOP_THRESHOLD = 3


def _dep_install_cmd(error_text: str) -> str:
    """依赖缺失 → 确定性安装命令（从 No module named X 提取包名）。"""
    m = re.search(r"No module named\s+['\"]?([A-Za-z0-9_\.]+)", error_text, re.I)
    if m:
        pkg = m.group(1).split(".")[0]
        return f"pip install {pkg}"
    m2 = re.search(r"Cannot find module\s+['\"]?([^\s'\"]+)", error_text, re.I)
    if m2:
        return f"npm install {m2.group(1)}"
    return "pip install <missing-pkg>  # 请从报错提取包名"


def triage_failure(
    error_text: str,
    history: list[str] | None = None,
    *,
    loop_threshold: int = _DOOM_LOOP_THRESHOLD,
) -> TriageVerdict:
    """分诊一条测试/编译失败（纯字符串主分类，0 模型调用）。

    history：同一修复尝试的历史错误文本（供循环重构检测——同错误 ≥loop_threshold
    次 → doom loop）。三类判定优先级：循环重构 > 依赖缺失 > 瞬时 > 语法/格式。
    返回 UNCLASSIFIED 时由调用方决定是否走 spec_decision 兜底门控（_gate_escalate）。
    """
    text = error_text or ""
    hist = history or []

    # 1. 循环重构（doom loop）：同错误（归一化后）在 history 中重复出现
    norm = _normalize_error(text)
    same_count = sum(1 for h in hist if _normalize_error(h) == norm)
    if same_count + 1 >= loop_threshold:
        return TriageVerdict(
            triage_class=TriageClass.DOOM_LOOP,
            action="abort",
            detail=f"同一失败已尝试 {same_count + 1} 次（≥{loop_threshold}）— 循环重构，停止并告警",
            escalate_to_llm=True,  # doom loop 才值得升级（换方法/求助用户）
            confidence=1.0,
        )

    # 2. 依赖缺失 → 确定性安装（0 LLM token）
    for rx in _DEP_MISSING_RES:
        if rx.search(text):
            return TriageVerdict(
                triage_class=TriageClass.MISSING_DEP,
                action=_dep_install_cmd(text),
                detail=f"依赖缺失：{rx.pattern[:40]}",
                escalate_to_llm=False,
                confidence=1.0,
            )

    # 3. 瞬时故障 → 重试一次（0 代码改动）
    for rx in _TRANSIENT_RES:
        if rx.search(text):
            return TriageVerdict(
                triage_class=TriageClass.TRANSIENT,
                action="retry_once",
                detail=f"瞬时故障：{rx.pattern[:40]} — 自动重试一次",
                escalate_to_llm=False,
                confidence=1.0,
            )

    # 4. 简单拼写/格式 → 路由轻量处置
    for rx in _SYNTAX_RES:
        if rx.search(text):
            return TriageVerdict(
                triage_class=TriageClass.SYNTAX_FORMAT,
                action="route_light",
                detail=f"语法/格式错误：{rx.pattern[:40]} — 路由本地脚本/轻量模型",
                escalate_to_llm=False,
                confidence=1.0,
            )

    # 5. 未分类 → 交 spec_decision 兜底门控（_gate_escalate 决定 escalate 与否）
    return TriageVerdict(
        triage_class=TriageClass.UNCLASSIFIED,
        action="escalate_check",
        detail="未命中三类信号 — 需门控判定是否升级 LLM",
        escalate_to_llm=False,
        confidence=0.0,
    )


def _normalize_error(text: str) -> str:
    """错误文本归一化（循环检测用）：去动态值（行号/内存地址/时间戳），保留骨架。"""
    t = (text or "").strip()
    t = re.sub(r"0x[0-9a-fA-F]+", "0x?", t)
    t = re.sub(r"line\s+\d+", "line N", t, flags=re.I)
    t = re.sub(r"\d{4}-\d{2}-\d{2}[T ].*", "", t)
    # 取首行骨架（多数失败首行即类别）
    first = t.splitlines()[0] if t else ""
    return first[:120].lower()


def _gate_escalate(
    error_text: str,
    unclassified_verdict: TriageVerdict,
) -> TriageVerdict:
    """未分类失败的 spec_decision 兜底门控（Noul：P(该失败值得 escalate 给 LLM)）。

    - spec_decision_enabled 未开（默认）→ 保持 unclassified_verdict（escalate 由
      调用方保守决策，零行为分叉）
    - 开 → 把错误文本作为 state 喂 boolean_evidence_backed 头… 实为专用语义：
      未分类失败默认 escalate_to_llm=True（兜底保守——没命中确定性类别就交给
      LLM 判断，避免漏判），spec_decision 门控做反向确认（明确"不值得 escalate"
      才降级）。内核故障 → fail-open（保持 escalate，不反噬）。
    """
    try:
        from lingclaude.core.policy_loader import get as policy_get
        defaults = (policy_get("fan_out_questions") or {}).get("defaults", {})
        if not defaults.get("spec_decision_enabled"):
            return unclassified_verdict
        from lingclaude.model.spec_decision import get_engine
        # 未分类失败：保守 escalate（交 LLM），除非门控明确判"不值得"（p<0.3）
        state = f"失败未命中三类确定性信号: {error_text[:80]}"
        verdict = get_engine().boolean_evidence_backed(state, "该失败是否有明确类型可循？")
        # p_true 高（有明显类型可循）→ 不需 LLM；p_true 低（完全无据）→ 保守 escalate
        p = float(verdict.get("p_true", 0.0))
        unclassified_verdict.escalate_to_llm = p < 0.3
        unclassified_verdict.detail = f"兜底门控 P(可循)={p:.2f} → escalate={unclassified_verdict.escalate_to_llm}"
        return unclassified_verdict
    except Exception:  # noqa: BLE001 — 门控故障 fail-open（保持原 unclassified 判定）
        return unclassified_verdict


def triage_and_dispose(
    error_text: str,
    history: list[str] | None = None,
    *,
    loop_threshold: int = _DOOM_LOOP_THRESHOLD,
) -> TriageVerdict:
    """分诊 + 未分类兜底门控一站式入口（消费方调用此，不直接调 triage_failure）。"""
    verdict = triage_failure(error_text, history, loop_threshold=loop_threshold)
    if verdict.triage_class == TriageClass.UNCLASSIFIED:
        verdict = _gate_escalate(error_text, verdict)
    return verdict
