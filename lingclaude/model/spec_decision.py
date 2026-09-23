"""NanoJev 契约消费层（lc 侧自研三原语等价内核，零外部模型依赖）。

对齐 TianyuCodings/NanoJev 的 TypeSafe 问题契约（TYPESAFE_CONTRACT.md）：
每个请求提供 state + question + candidates，一次前向出完整概率分布、
零 token 解码。lc 侧价值 = 把 fast lane / FanOut 的投机决策从「启发式
（Jaccard 重复度 / difficulty 分桶阈值）」升级为「概率判定内核」。

设计（铁律 2 停层声明）：
- 内核 = SpecDecisionEngine（三原语 Choice/Boolean/Score 的本地等价实现，
  纯函数、无 I/O、可全离线单测）
- 接缝 = DecisionBackend 协议（predict(states, questions) -> 分布）
- 实现 = 当前单实现 = HeuristicBackend（lc 会话数据驱动的本地先验，
  不下载 C-Tianyu 权重，零外部模型）；预留 = 未来 NanoJev SFT 后训练的
  lc 特化 checkpoint（经 DecisionBackend 注入，内核不动）
边界纪律：启发式后端是「占位先验」——给出概率分布的接口形态与门控
语义（confidence/期望值），让消费方（fast lane 门控、FanOut should_continue
投机价值判定）先跑通；真·概率质量待 lc 数据 SFT 后训练兑现（挂账）。

语义对齐 NanoJev 三原语（契约 §primitives）：
- Choice：N 候选 → 归一化概率分布 + 选定项 + confidence（max 概率）
- Boolean(Noul)：命题 → P(yes) ∈ [0,1]（sigmoid 语义）
- Score：2-10 有序层级 → 层级概率分布 + 期望值 sum(i*p_i)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ── 分布结果类型 ──

@runtime_checkable
class ChoiceResult(Protocol):
    """Choice 原语结果：候选键 → 概率 的归一化分布 + 选定 + confidence。"""
    distribution: dict[str, float]   # {candidate: p}，sum≈1
    chosen: str                       # argmax 候选键
    confidence: float                 # max 概率（NanoJev Choice 含 confidence 字段）

@runtime_checkable
class BooleanResult(Protocol):
    """Boolean(Noul) 原语结果：命题 P(yes)。"""
    p_true: float                     # sigmoid 语义概率
    yes: bool                         # 阈值化（0.5）

@runtime_checkable
class ScoreResult(Protocol):
    """Score 原语结果：有序层级分布 + 期望值。"""
    distribution: list[float]         # 各层级概率（长度=层数）
    score: float                      # 概率加权层级期望 sum(i*p_i)
    level: int                        # argmax 层级索引


# ── 后端接缝（协议，duck-typed）──

@runtime_checkable
class DecisionBackend(Protocol):
    """投机决策后端接缝：给定 state + 三类问题，返回对应原语结果。

    当前唯一实现 = HeuristicBackend（本地先验）。未来 NanoJev lc 特化
    checkpoint 经此接缝注入（kernel 不动，实现可热换）。
    """

    def choice(self, state: str, question: str, candidates: list[str]) -> Any:
        """..."""
        ...

    def boolean(self, state: str, question: str) -> Any:
        """..."""
        ...

    def score(self, state: str, question: str, levels: list[str]) -> Any:
        """..."""
        ...


class HeuristicBackend:
    """本地先验后端（零外部模型）：用 lc 侧可观测信号构造三原语概率。

    这是占位先验——接口形态 + 门控语义先跑通消费方；概率质量是启发式
    拟合（非 SFT 后训练的模型输出），兑现「真概率」需 lc 会话数据
    做 NanoJev 式后训练（挂账 nanODEV-lc-sft-review）。
    """

    # 投机价值命题（boolean 头）：哪些信号预示「本轮值得投机扇出」
    _SIGNAL_WEIGHTS: dict[str, float] = {
        "multi_step": 0.35,   # 多步/长任务 → 投机可并行拆子目标
        "complexity": 0.30,   # 难度信号 → 投机收益最大
        "repetition": 0.25,   # 与已投机话题重复度 → 重复度越高投机价值越低
    }

    def choice(self, state: str, question: str, candidates: list[str]) -> dict[str, Any]:
        """Choice：候选概率分布。本地先验 = 候选长度/匹配度信号 + 均匀兜底。

        （任务特化 domain 判定属 Laya 域；本内核只做契约对齐，不抢 Laya 的活。
        消费方接 domain 判定仍走 fast_lane 门控链；此 Choice 头为未来 SFT
        lc 特化 checkpoint 预留——接口先就位。）
        """
        cand = [c for c in (candidates or []) if c]
        if not cand:
            return {"distribution": {}, "chosen": "", "confidence": 0.0}
        scored: dict[str, float] = {}
        for c in cand:
            sc = self._match_score(state, c)
            scored[c] = sc
        total = sum(scored.values()) or 1.0
        dist = {k: v / total for k, v in scored.items()}
        chosen = max(dist, key=dist.get)
        return {"distribution": dist, "chosen": chosen, "confidence": dist[chosen]}

    def boolean(self, state: str, question: str) -> dict[str, Any]:
        """Boolean(Noul)：P(命题为真) —— 消费方「本轮投机是否有价值」（投机价值判定）。

        state 拼接「复用度=X 本轮=<prompt>」，本方法解析复用度/难度信号联合判定。
        消费方 = FanOut should_continue。
        """
        p = self._speculative_value(state)
        return {"p_true": p, "yes": p >= 0.5}

    def boolean_evidence_backed(self, state: str, question: str) -> dict[str, Any]:
        """Boolean(Noul) 专用头：P(完成声明被证据真正支撑) —— 证据完整度单信号主导。

        与 boolean()（投机价值，难度+证据−复用三信号联合）区分：完成声明核验
        场景（evidence_protocol.ClaimVerifier._claim_gate）的 P 应由证据完整度
        本身决定——证据越扎实 P 越高，与任务难度无关（"测试全部通过"是短文本
        低难度，但证据扎实就应判支撑）。state 拼接「证据完整度=X」，P=该值
        （sigmoid 语义直通，阈值化交给消费方按 claim_gate_threshold 判）。
        """
        ev = 0.0
        m = re.search(r"证据完整度=([01]\.\d+)", state)
        if m:
            ev = float(m.group(1))
        return {"p_true": ev, "yes": ev >= 0.5}

    def score(self, state: str, question: str, levels: list[str]) -> dict[str, Any]:
        """Score：有序层级分布 + 期望值。消费方 = difficulty 分桶升级。

        levels = ["trivial","easy","moderate","hard"]（lc 现有 4 级）。
        本地先验：prompt 长度/复杂度信号 → 层级概率，期望 = sum(i*p_i)。
        """
        lvl = [l for l in (levels or []) if l]
        n = len(lvl)
        if n < 2:
            n = 2
            lvl = lvl or ["trivial", "hard"]
        base = self._difficulty_signal(state)  # 0.0-1.0 难度先验
        # 高斯-ish 分布在难度先验上（层级数越多越平滑）
        centers = [i / (n - 1) for i in range(n)]
        raw = [max(0.0, 1.0 - abs(c - base) * 1.5) for c in centers]
        total = sum(raw) or 1.0
        dist = [v / total for v in raw]
        expect = sum(i * p for i, p in enumerate(dist))
        argmax = int(max(range(n), key=lambda i: dist[i]))
        return {"distribution": dist, "score": expect, "level": argmax}

    # ── 内部信号拟合（本地可观测，非模型输出）──

    @staticmethod
    def _match_score(state: str, cand: str) -> float:
        """候选与 state 的词面匹配度（token 交叠，兜底均匀）。"""
        s_tok = set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", (state or "").lower()))
        c_tok = set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", (cand or "").lower()))
        if not s_tok:
            return 1.0
        overlap = len(s_tok & c_tok)
        return 1.0 + overlap  # ≥1，纯兜底时全候选等权 1.0

    @staticmethod
    def _difficulty_signal(state: str) -> float:
        """难度先验 0.0-1.0：prompt 长度 + 多步/长任务/规划类关键词加权。

        关键词覆盖 2026-09-22 扩充：除技术类（设计/架构/并发/重构/优化）外，
        补规划/核对/明细/行程/步骤/排查/分析类（"规划三天行程并核对预算"也是
        多步任务）——避免"非技术词的多步任务"难度先验偏低导致投机价值误判。
        """
        t = (state or "").lower()
        length_norm = min(len(t) / 300.0, 1.0)
        kw = 0.0
        if re.search(
            r"(设计|架构|系统|重构|优化|并发|分布式|多步|multi[- ]step|refactor)"
            r"|(规划|核对|明细|行程|步骤|排查|分析|评估|梳理|拆解)"
            r"|(plan|budget|schedule|trace|debug|analyse|multi[- ]step)",
            t,
        ):
            kw += 0.4
        if re.search(r"(边界|异常|错误|死锁|竞态|边缘|edge[- ]case|预算|成本)", t):
            kw += 0.2
        return min(length_norm * 0.4 + kw, 1.0)

    @staticmethod
    def _speculative_value(state: str) -> float:
        """投机价值 P(yes)：难度高 → 投机可并行拆子目标 → 高；
        复用度高（同话题刚投机过）→ 投机价值被压低 → 低；
        证据完整度高（完成声明核验场景）→ 声明被支撑 → 抬升 P(yes)。

        state 约定（消费方拼接信号前缀）：
        - FanOut should_continue：「复用度=X 本轮=<prompt>」
        - 完成声明核验（_claim_gate）：「证据完整度=X 本轮完成声明=<文本>」
        本方法解析三个信号做联合判定——难度抬升投机价值、复用度压降投机价值
        （同话题重复投机收益递减）、证据完整度抬升 P(达成)（证据扎实则声明可信）。
        信号缺失时按 0 兜底（保守）。
        """
        reuse = 0.0
        m = re.search(r"复用度=([01]\.\d+)", state)
        if m:
            reuse = float(m.group(1))
        evidence = 0.0
        me = re.search(r"证据完整度=([01]\.\d+)", state)
        if me:
            evidence = float(me.group(1))
        diff = HeuristicBackend._difficulty_signal(state)
        # 联合：难度抬升 + 证据抬升 − 复用度压降，线性归一到 [0,1]
        # 复用度 1.0（完全重复）时即便难度高也压到 ~0.15（投机无新收益）
        # 证据完整度 0.9 时抬升 ~0.5（证据扎实，声明可信）
        value = diff + evidence * 0.5 - reuse * (0.5 + 0.4 * diff)
        return min(max(value, 0.0), 1.0)


class SpecDecisionEngine:
    """三原语投机决策引擎（内核：绑定一个 DecisionBackend，纯函数无 I/O）。

    用法（对齐 NanoJev 契约 state+question→分布）：
        eng = SpecDecisionEngine(HeuristicBackend())
        eng.choice(state, "domain?", ["code","writing","factual_lookup"])
        eng.boolean(state, "本轮值得投机扇出?")
        eng.score(state, "难度?", ["trivial","easy","moderate","hard"])
    """

    def __init__(self, backend: DecisionBackend | None = None) -> None:
        self._backend = backend or HeuristicBackend()

    def choice(self, state: str, question: str, candidates: list[str]) -> dict[str, Any]:
        """Choice 原语 → {distribution, chosen, confidence}。"""
        return self._backend.choice(state, question, candidates)

    def boolean(self, state: str, question: str) -> dict[str, Any]:
        """Boolean(Noul) 原语 → {p_true, yes}。"""
        return self._backend.boolean(state, question)

    def boolean_evidence_backed(self, state: str, question: str) -> dict[str, Any]:
        """Boolean(Noul) 专用头（完成声明核验消费方）→ {p_true, yes}，
        证据完整度单信号主导（与 boolean() 三信号联立的投机价值判定区分）。"""
        return self._backend.boolean_evidence_backed(state, question)

    def score(self, state: str, question: str, levels: list[str]) -> dict[str, Any]:
        """Score 原语 → {distribution, score, level}。"""
        return self._backend.score(state, question, levels)


# ── 进程级单例（消费方共享，避免每轮重建）──

_ENGINE: SpecDecisionEngine | None = None


def get_engine() -> SpecDecisionEngine:
    """取进程级 SpecDecisionEngine 单例（消费方：FanOut should_continue /
    fast lane 门控 / 未来 SFT lc 特化 checkpoint 注入点）。"""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = SpecDecisionEngine(HeuristicBackend())
    return _ENGINE


def set_engine(engine: SpecDecisionEngine) -> None:
    """注入自研 / SFT 后训练 checkpoint 的引擎（未来 NanoJev lc 特化
    后端落位时调用；测试亦用此换 fake backend）。"""
    global _ENGINE
    _ENGINE = engine


def reset_engine() -> None:
    _ENGINE = None
