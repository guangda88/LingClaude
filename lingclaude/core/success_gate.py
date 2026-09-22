"""工具执行成功度 Noul 门控（NanoJev 契约消费层消费点⑨，2026-09-22）。

对齐 NanoJev/Jev Agent 循环「执行是否成功」决策点：传统上每个工具结果是否成功
靠 LLM 再判一次（"输出里看着像成功吗"），高频且昂贵。下沉为 Noul 概率门控——
**0 LLM token** 确定性判定工具输出是否表明操作成功，循环控制流直接读结构化判定。

设计（对齐 lc 红线：能模式匹配解决的不用模型）：
- 主路径 = 本地成功/失败信号先验（0 模型调用）：
  - 显式成功信号：exit=0 / "ok" / "success" / 测试 "passed" / "done"
  - 显式失败信号：非零 exit / "error" / "fail" / "exception" / "not found"
  - 信号强度（多信号加权）→ P(成功) ∈ [0,1]
- 兜底 = spec_decision Noul 头（spec_decision_enabled 启用时，对"无明显成功也无
  明显失败的灰区结果"做反向确认），P 低 → 判失败。
- fail-open = 门控故障 → 返回保守中性判定（p=0.5, is_success=None 不定论），
  不反噬工具执行本身。

停层声明（铁律 2 细则 5）：
- 内核 = gate_success(tool_name, result_fields) -> SuccessVerdict（纯判定无 I/O）
- 接缝 = 成功信号正则 + 可选 spec_decision 兜底协议
- 实现 = 单实现（本地先验 + 可选 Noul 兜底）
边界纪律：只给判定（P(成功)+依据），不改写工具结果（挂到结果面字段，
模型/循环可读）；主路径 0 模型调用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# 显式成功信号（命中即抬升 P(成功)）
_SUCCESS_RES = (
    re.compile(r"exit\s*=?\s*0\b", re.I),
    re.compile(r"\b(exit|status|return_code)\s*[:=]?\s*0\b", re.I),
    re.compile(r"\b(success|succeeded|ok|okay|done|completed)\b", re.I),
    re.compile(r"\b(passed|all (tests )?passed|✓)\b", re.I),
)
# 显式失败信号（命中即压降 P(成功)）
_FAILURE_RES = (
    re.compile(r"exit\s*=?\s*[1-9]\d*\b", re.I),
    re.compile(r"\b(error|failed|failure|exception|traceback)\b", re.I),
    re.compile(r"\b(not found|no such|cannot|could not|denied|refused)\b", re.I),
    re.compile(r"\b(ModuleNotFoundError|ImportError|SyntaxError|AssertionError)\b"),
)
# 灰区（既无强成功也无强失败）→ 需兜底门控判定的信号
_GRAY_HINT_RES = (
    re.compile(r"\b(warning|deprecated|partial)\b", re.I),
    re.compile(r"(部分|部分成功|未完成|incomplete|pending)", re.I),
)


@dataclass
class SuccessVerdict:
    """执行成功度判定。"""
    is_success: bool | None   # True/False 明确判定；None = 灰区不定论（交 LLM/用户）
    p_success: float          # P(成功) ∈ [0,1]
    evidence: str            # 命中信号（供结果面展示）
    source: str = "local"    # local（本地先验）/ noul_fallback（spec_decision 兜底）/ neutral

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_success": self.is_success,
            "p_success": round(self.p_success, 3),
            "evidence": self.evidence,
            "source": self.source,
        }


def gate_success(
    tool_name: str,
    result_fields: dict[str, Any] | None = None,
    *,
    exit_code: int | None = None,
) -> SuccessVerdict:
    """判定一次工具执行是否成功（主路径 0 模型调用）。

    信号优先级（高→低）：
    1. exit_code 显式非 None：==0 强成功，!=0 强失败（确定性，最高优先）
    2. result_fields 里嵌套的 exit/status 字段（bash 工具结果 dict）
    3. 文本 stdout/stderr 里的成功/失败信号正则
    4. 灰区（仅弱信号）→ spec_decision 兜底（启用时）；未启用 → is_success=None
    """
    rf = result_fields or {}
    text_bits: list[str] = []
    for k in ("stdout", "stderr", "output", "error"):
        v = rf.get(k)
        if isinstance(v, str):
            text_bits.append(v)
    text = "\n".join(text_bits)

    # 1. 显式 exit_code 参数（最高优先，确定性）
    if exit_code is not None:
        if exit_code == 0:
            return SuccessVerdict(True, 1.0, f"exit_code=0", "local")
        return SuccessVerdict(False, 0.0, f"exit_code={exit_code}", "local")

    # 2. 嵌套 exit/status 字段（bash 工具结果带 exit_code 键）
    for key in ("exit_code", "exit", "status", "return_code"):
        if key in rf and rf[key] is not None:
            try:
                code = int(rf[key])
            except (TypeError, ValueError):
                continue
            if code == 0:
                return SuccessVerdict(True, 1.0, f"{key}=0", "local")
            return SuccessVerdict(False, 0.0, f"{key}={code}", "local")

    # 3. 文本信号加权
    success_hits = [rx.pattern for rx in _SUCCESS_RES if rx.search(text)]
    failure_hits = [rx.pattern for rx in _FAILURE_RES if rx.search(text)]
    if failure_hits and not success_hits:
        return SuccessVerdict(False, 0.1, f"failure signal: {failure_hits[0][:30]}", "local")
    if success_hits and not failure_hits:
        p = min(0.6 + 0.1 * len(success_hits), 0.95)
        return SuccessVerdict(True, p, f"success signal: {success_hits[0][:30]}", "local")
    if success_hits and failure_hits:
        # 混合信号（如输出里既有 error 又 success）→ 灰区偏失败
        p = 0.3
        gray = "mixed success+failure signals"
        return _gray_gate(p, gray, text, tool_name)

    # 4. 无强信号（灰区）
    gray_hint = next((rx.pattern for rx in _GRAY_HINT_RES if rx.search(text)), "no strong signal")
    p_base = 0.5
    return _gray_gate(p_base, gray_hint, text, tool_name)


def _gray_gate(p_base: float, gray_reason: str, text: str, tool_name: str) -> SuccessVerdict:
    """灰区兜底：spec_decision Noul 头判定（启用时）；未启用/故障 → 保守不定论。"""
    try:
        from lingclaude.core.policy_loader import get as policy_get
        defaults = (policy_get("fan_out_questions") or {}).get("defaults", {})
        if defaults.get("spec_decision_enabled"):
            from lingclaude.model.spec_decision import get_engine
            state = f"工具={tool_name} 结果信号={gray_reason[:40]} 摘要={text[:60]}"
            verdict = get_engine().boolean(state, "该工具执行是否表明操作成功？")
            p = float(verdict.get("p_true", p_base))
            if p >= 0.5:
                return SuccessVerdict(True, p, gray_reason, "noul_fallback")
            return SuccessVerdict(False, p, gray_reason, "noul_fallback")
    except Exception:  # noqa: BLE001 — 兜底故障 fail-open
        pass
    # 未启用或故障：保守不定论（交 LLM/用户，不冒险武断）
    return SuccessVerdict(None, p_base, gray_reason, "neutral")
