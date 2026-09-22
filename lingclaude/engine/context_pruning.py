"""工具输出相关性剪枝（NanoJev 契约消费层消费点③，2026-09-22）。

对齐 dsh-jev-tools 的 Context Pruning 语义：把工具结果（read/grep/web_fetch 等）
超长输出切成段落，逐段判定「是否包含 Agent 当前步骤所需信息」，只保留高相关
段 + 确定性保底（首段与末段永远保留——报错/结果核心常在这两处），fail-open
（剪枝任何故障 → 回退原固定截断，不影响任务）。

设计（对齐 lc 红线：能模式匹配解决的不用模型）：
- 主路径 = 本地相关性先验（段落与当前任务关键词的 token 交叠 + 段落显著性），
  0 模型调用，可离线单测。
- 兜底 = spec_decision Noul 头（段落相关性概率），仅 spec_decision_enabled
  启用时对「本地先验判不相关但段落显著」的灰区段做反向确认。
- 保底 = 首段 + 末段 + 高置信段永留（确定性，不因剪枝丢失关键信息）。
- fail-open = 剪枝结果比原输出还短到不可用 / 内核故障 → 回退调用方的固定截断。

停层声明（铁律 2 细则 5）：
- 内核 = prune_by_relevance(text, task_hint) -> 剪枝后文本（纯函数无 I/O）
- 接缝 = 段落切分 + 相关性判定协议（段落 = 空行/行边界）
- 实现 = 单实现（本地先验 + 可选 spec_decision 兜底）
边界纪律：只排序保留、不卡死阈值（首尾保底确定性）；剪枝失败 fail-open
不反噬；不改工具执行层（工具仍拿到全量，剪的是「进历史」的那份）。
"""
from __future__ import annotations

import re
from typing import Any

# 保底：无论相关性如何，首尾各保留的段落数（报错/结果核心常在此）
_KEEP_HEAD_PARAS = 1
_KEEP_TAIL_PARAS = 1
# 本地先验：段落与 task_hint 的 token 交叠权重（命中越多越相关）
_RELEVANCE_TOKEN_MIN = 1  # 至少 1 个 token 交叠才判「可能相关」


def _split_paragraphs(text: str) -> list[str]:
    """按空行/换行切段（保留非空段，顺序即原文序）。"""
    # 先按空行切（段落级），再对超长段按行切（防单段独大）
    blocks = re.split(r"\n\s*\n", text)
    out: list[str] = []
    for b in blocks:
        b = b.strip()
        if not b:
            continue
        # 单块过长（>2000 字符）按行再切，避免一段吞掉全预算
        if len(b) > 2000:
            for line in b.splitlines():
                if line.strip():
                    out.append(line)
        else:
            out.append(b)
    return out


def _para_relevance(para: str, task_hint: str) -> float:
    """段落与当前任务的相关性先验 0.0-1.0（纯本地 token 交叠，无模型）。

    信号：段落 token 与 task_hint token 的交叠占比（交叠多=相关）+
    段落显著性（含数字/路径/关键字等实义内容加分，纯噪声降分）。
    """
    p_tokens = set(re.findall(r"[a-z0-9_.\-]+", (para or "").lower()))
    t_tokens = set(re.findall(r"[a-z0-9_.\-]+", (task_hint or "").lower()))
    if not t_tokens:
        # 无任务提示（task_hint 空）→ 退化为段落显著性（实义内容 > 噪声）
        sig = _para_significance(para)
        return sig
    inter = p_tokens & t_tokens
    # 交叠占比（相对任务 token，避免段落巨量无关 token 稀释）
    overlap_ratio = len(inter) / len(t_tokens) if t_tokens else 0.0
    significance = _para_significance(para)
    # 相关 = 有交叠（主要）+ 显著性（次要）；纯交叠但段落是噪声仍要保底
    return min(overlap_ratio * 0.7 + significance * 0.3, 1.0)


def _para_significance(para: str) -> float:
    """段落显著性先验：含实义信号（数字/路径/代码标记/关键动词）→ 高。"""
    p = para or ""
    sig = 0.0
    if re.search(r"\d", p):
        sig += 0.2
    if re.search(r"/|~|\.py|\.ts|\.js|\.rs|\.go", p):
        sig += 0.2
    if re.search(r"error|exception|traceback|fail|warning", p, re.I):
        sig += 0.3  # 报错段显著性最高（结果核心）
    if re.search(r"^\s*```|^def |^class |^import ", p, re.M):
        sig += 0.2  # 代码/结构标记
    return min(sig, 1.0)


def prune_by_relevance(
    text: str,
    task_hint: str = "",
    *,
    max_chars: int = 1600,
    keep_ratio: float = 0.5,
) -> str | None:
    """相关性剪枝：保留高相关段 + 首尾保底，返回剪枝后文本。

    - 段落 ≤2 或剪枝后比固定截断还长 → 返回 None（调用方回退固定截断，
      短文本/已够短的不需要剪）。
    - 否则保留「相关性 ≥ 0.3 的段 + 首 _KEEP_HEAD_PARAS 段 + 末 _KEEP_TAIL_PARAS
      段」，按原序拼接，超 max_chars*keep_ratio 时按相关性降序再筛（保底段除外）。
    - 纯本地先验（0 模型调用），spec_decision 兜底由调用方按需触发（本函数
      主路径不碰模型，红线：能模式匹配解决的不用模型）。

    返回 None = 剪枝无收益或不可用（fail-open 信号），调用方回退原截断。
    """
    if text is None:
        return ""
    paras = _split_paragraphs(text)
    if len(paras) <= _KEEP_HEAD_PARAS + _KEEP_TAIL_PARAS:
        return None  # 段太少，剪枝无意义
    scored = [(_para_relevance(p, task_hint), i, p) for i, p in enumerate(paras)]
    # 保底段：首 + 末（顺序保留，防报错/结果核心丢失）
    head_set = set(range(_KEEP_HEAD_PARAS))
    tail_set = set(range(len(paras) - _KEEP_TAIL_PARAS, len(paras)))
    keep: set[int] = head_set | tail_set
    # 相关性段：≥0.3 的非保底段
    for rel, idx, _p in scored:
        if rel >= 0.3:
            keep.add(idx)
    kept_text = "\n".join(paras[i] for i in sorted(keep))
    # 剪枝无收益（保留段仍 ≥ 原长的 keep_ratio 以上）→ 回退固定截断
    if len(kept_text) >= len(text) * keep_ratio:
        return None
    # 超预算按相关性降序再筛（保底段不丢）
    if len(kept_text) > max_chars:
        non_protected = [i for i in sorted(keep) if i not in head_set and i not in tail_set]
        non_protected.sort(key=lambda i: -_para_relevance(paras[i], task_hint))
        final_keep = set(head_set) | tail_set | set(non_protected)
        # 逐个移除低相关段直到入预算
        for i in reversed(non_protected):
            trial = "\n".join(paras[j] for j in sorted(final_keep - {i}))
            if len(trial) <= max_chars or len(final_keep) <= len(head_set) + len(tail_set):
                final_keep.discard(i)
                break
            final_keep.discard(i)
        kept_text = "\n".join(paras[i] for i in sorted(final_keep))
    kept_text += (
        f"\n[工具输出相关性剪枝: 原文 {len(text)} 字符 {len(paras)} 段，"
        f"保留 {len([p for p in kept_text.split(chr(10)) if p])} 段（首尾保底+相关段）。"
        f"如需被剪除段落请重新调用工具取全量。]"
    )
    return kept_text


def should_prune(text: str, task_hint: str = "", *, max_chars: int = 1600) -> bool:
    """该工具结果是否值得走相关性剪枝（段数足够多 + 文本够长）。"""
    if text is None or len(text) <= max_chars:
        return False
    return len(_split_paragraphs(text)) > _KEEP_HEAD_PARAS + _KEEP_TAIL_PARAS
