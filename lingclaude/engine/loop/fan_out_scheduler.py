"""P1-0 调度器真身（B1，2026-09-22）：FanOutScheduler——投机扇出策略实现。

接 P1-0 Thread.set_fan_out_scheduler 挂点（orchestrator.loop_stage 命名空间）。
契约边界（docs/CORE_SURFACE_CONTRACT.md §四）：本类只做「何时投机/探什么/何时终止」
的调度决策，不碰循环体；投机结果采纳由 verify 决定，永不拖垮主路径。

策略设计（对齐 P1-4 fan_out_questions.yaml data-driven + Laya fast lane）：
- plan：读 policies/fan_out_questions.yaml 的 fan_out_tags + 可选 Laya fast lane
  预分类；仅当判定值得投机（任务非纯问答、有并行子目标）时返回分支列表，
  否则 None（默认直通，零行为分叉）。
- verify：投机结果非空且非分支错误标记 → 采纳。
- should_continue：plan 为 None（未投机）恒 True（原行为）；已投机且后续轮次
  与投机分支重复度高时提前终止（避免投机已命中还空跑轮次）。

投机分支的 prompt 由分支 prompt 模板填充（当前最小实现：echo 模板 + 问题集
tag，真场景由 P1-4 策略文件扩展 prompt 模板列驱动）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 投机分支错误标记前缀（run_speculative 逐分支 fail-soft 捕获后打的标记）
_BRANCH_ERROR_PREFIX = "[speculative-branch-error]"


@dataclass
class FanOutPlan:
    """一次投机扇出的计划（LoopHooks.pre_decide 返回值）。"""
    fan_out: list[dict[str, str]]
    token_budget: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"fan_out": self.fan_out, "token_budget": self.token_budget, "reason": self.reason}


class FanOutScheduler:
    """投机扇出调度器（duck-typed：plan/verify/should_continue 三方法，接 Thread 挂点）。

    构造参数（全部可选，全默认时 scheduler 等价于「直通」——不投机，零行为分叉）：
    - max_branches: 单次投机最大分支数（默认 2，封顶防 token 膨胀）
    - min_prompt_chars: 低于此长度的 prompt 不投机（短 prompt 投机无收益）
    - enable: 总开关（False 时 plan 恒 None；默认 False = 挂上不启用，
      由调用方显式 enable=True 才投机——保守默认，与 P1-1 env 门禁同哲学）
    - branch_prompt: 分支 prompt 模板（{question_tag}/{state} 占位；None 时用内置默认）
    - pre_classifier: 可选预分类器（prompt -> dict|None，如 Laya fast_route 门控后结果）
    """

    def __init__(
        self,
        *,
        enable: bool = False,
        max_branches: int = 2,
        min_prompt_chars: int = 40,
        branch_prompt: str | None = None,
        pre_classifier: Any = None,
    ) -> None:
        self.enable = enable
        self.max_branches = max(1, min(4, max_branches))
        self.min_prompt_chars = min_prompt_chars
        self.branch_prompt = branch_prompt
        self._pre_classifier = pre_classifier
        self._plans: list[tuple[str, FanOutPlan | None]] = []  # 最近计划（should_continue 用）

    # ── duck-typed 三方法（Thread.set_fan_out_scheduler 接通 LoopHooks 三钩子）──

    def plan(self, prompt: str, messages: list) -> dict | None:
        """轮次前决策：是否投机、投哪些分支。

        返回 FanOutPlan.to_dict() 或 None（直通）。判定链（任一不满足 → None）：
        1. enable 总开关
        2. prompt 足够长（投机收益 > 开销）
        3. fan_out_questions.yaml 给出 fan_out_tags（数据驱动，读失败 → 不投机）
        4. 预分类器（可选）判定值得投机
        """
        self._plans.append((prompt, None))  # 先登记占位，下方回填
        if not self.enable:
            return None
        if len(prompt or "") < self.min_prompt_chars:
            return None
        tags = self._load_fan_out_tags()
        if not tags:
            return None
        if self._pre_classifier is not None:
            verdict = self._pre_classifier(prompt, messages)
            if verdict is None:
                # 预分类器回退（Laya 门控 None）= 不可投机（质量验证：低置信不冒进）
                return None
            # B1-T：difficulty 分桶门控——低难度任务投机无收益，直接走单轮路径
            if not self._difficulty_gate(verdict):
                return None
        plan = FanOutPlan(
            fan_out=[{"prompt": self._make_branch_prompt(t), "tag": t} for t in tags[: self.max_branches]],
            reason=f"fan_out_tags={tags[: self.max_branches]}",
        )
        self._plans[-1] = (prompt, plan)
        return plan.to_dict()

    def verify(self, tag: str, result: Any) -> bool:
        """投机结果校验：非空、非分支错误标记 → 采纳（True）。

        保守语义：只有「真跑出了内容」才采纳；错误/空结果丢弃该分支。
        """
        if not isinstance(result, str):
            result = str(result) if result is not None else ""
        if not result or result.startswith(_BRANCH_ERROR_PREFIX):
            return False
        return len(result.strip()) > 0

    def should_continue(self, prompt: str, round_idx: int, state: Any) -> bool:
        """轮次边界：plan 为 None（未投机）恒 True（原行为）；已投机时——
        后续轮次继续（投机结果在轮次间被消费），终止时机留给外层调度，
        本策略保守不主动终止（避免误杀正常多轮工具链）。

        B1 扩展（2026-09-22）：重复度终止判定——state 为 fan_plan（LoopHooks
        的 fan_out 计划 dict）且最近计划已投机时，若本轮 prompt 与投机分支
        prompt 高度重复（token 集 Jaccard >= fan_out_reuse_threshold，策略文件
        热更，默认 0.8 保守）→ 返回 False 提前终止（投机已命中还空跑无收益）。
        阈值 0.0 / 读失败 / 未投机 / 无历史计划 → 恒 True（原保守语义兜底）。
        """
        last = self._latest_plan()
        if last is None:
            return True
        prev_prompt, plan = last
        threshold = self._load_reuse_threshold()
        if threshold <= 0.0:
            return True  # 策略关闭重复度终止（保持原恒 True）
        return self._reuse_ratio(prompt, prev_prompt) < threshold

    # ── 内部 ──

    def _latest_plan(self) -> tuple[str, dict] | None:
        """最近一次投机计划（plan 非 None 的登记项），连同其 prompt 一并返回；
        无投机 → None。"""
        for prompt, plan in reversed(self._plans):
            if plan is not None:
                return prompt, plan.to_dict()
        return None

    def _load_reuse_threshold(self) -> float:
        """P1-4/B1 扩展：defaults.fan_out_reuse_threshold（热更；读失败→0.0 关闭）。"""
        try:
            from lingclaude.core.policy_loader import get as policy_get
            data = policy_get("fan_out_questions")
            v = (data or {}).get("defaults", {}).get("fan_out_reuse_threshold")
            if isinstance(v, (int, float)):
                return float(v)
        except Exception:
            logger.debug("fan_out_reuse_threshold 读取失败（关闭终止判定）", exc_info=True)
        return 0.0

    def _reuse_ratio(self, current_prompt: str, prev_prompt: str) -> float:
        """本轮 prompt 与上一次已投机 prompt 的话题相似度（Jaccard）。

        语义修正（2026-09-22）：终止判定的基准是「上一次投机的 prompt」而非
        分支模板词——分支 prompt 是固定模板（就 (本轮上下文) 回答子目标「X」），
        与用户 prompt 本体零交叠，拿来比永远≈0，终止判形同虚设。
        正确基准：本轮 prompt 与上一次投机 prompt 高度同话题（投机刚问过同类，
        再空跑一轮无新收益）才终止。分词：CJK 逐字 + 拉丁按词（纯 split 对
        中文无空格文本失准，「就 本轮上下文 回答子目标」切不出词）。
        """
        import re

        def _tokens(text: str) -> set[str]:
            t = (text or "").lower()
            cjk = re.findall(r"[\u4e00-\u9fff]", t)
            latin = [w for w in re.findall(r"[a-z0-9]+", t) if len(w) > 1]
            return set(cjk) | set(latin)

        a = _tokens(current_prompt)
        b = _tokens(prev_prompt)
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    def _load_fan_out_tags(self) -> list[str]:
        """P1-4：fan_out_questions.yaml 的 defaults.fan_out_tags（data-driven）。"""
        try:
            from lingclaude.core.policy_loader import get as policy_get
            data = policy_get("fan_out_questions")
            tags = (data or {}).get("defaults", {}).get("fan_out_tags")
            if isinstance(tags, list) and tags:
                return [str(t) for t in tags]
        except Exception:
            logger.debug("fan_out_questions.yaml fan_out_tags 读取失败（不投机）", exc_info=True)
        return []

    # difficulty 分桶（0-3 数值，legend: 0=trivial/1=easy/2=moderate/3=hard）：
    # 低难度（<1.5，trivial/easy）投机无收益——单轮直接回答更快；
    # 高难度（>=1.5，moderate/hard）多步推理投机收益最大（可并行拆子目标）。
    _SPECULATIVE_MIN_DIFFICULTY = 1.5

    def _difficulty_gate(self, verdict: dict | None) -> bool:
        """B1-T/B2-R：difficulty 分桶门控——verdict 带 difficulty score 且
        >= 阈值才允许投机。阈值走策略文件 fan_out_speculative_min_difficulty
        （默认 0.5，热更；读失败回退内置 1.5——保守不放大投机面）。

        verdict 无 difficulty 答案（triage 问题集）或 score 缺失 → 保守放行（None 语义=
        无法判难度时按原 plan 逻辑走，由 verify 兜底）。
        """
        if not isinstance(verdict, dict):
            return True
        diff = (verdict.get("answers") or {}).get("difficulty")
        if not isinstance(diff, dict):
            return True
        score = diff.get("score")
        if not isinstance(score, (int, float)):
            return True
        return score >= self._speculative_min_difficulty()

    def _speculative_min_difficulty(self) -> float:
        """B2-R：投机 difficulty 门槛（策略文件热更；读失败回退内置 1.5）。"""
        try:
            from lingclaude.core.policy_loader import get as policy_get
            v = (policy_get("fan_out_questions") or {}).get("defaults", {}).get(
                "fan_out_speculative_min_difficulty")
            if isinstance(v, (int, float)):
                return float(v)
        except Exception:
            logger.debug("fan_out_speculative_min_difficulty 读取失败（用内置 1.5）",
                         exc_info=True)
        return self._SPECULATIVE_MIN_DIFFICULTY

    def _make_branch_prompt(self, tag: str) -> str:
        """分支 prompt：模板可配（P1-4/B1 策略扩展），默认最小 echo 模板。

        模板优先级：构造参数 branch_prompt > 策略文件 defaults.branch_prompt_template
        > 内置 echo 模板（读失败/键缺失回退，不崩）。
        """
        tpl = self.branch_prompt or self._load_branch_template()
        return tpl.replace("{question_tag}", tag).replace("{state}", "(本轮上下文)")

    def _load_branch_template(self) -> str:
        """B1 扩展：defaults.branch_prompt_template（热更；读失败回退内置 echo 模板）。"""
        _BUILTIN = "就 {state} 回答子目标「{question_tag}」"
        try:
            from lingclaude.core.policy_loader import get as policy_get
            data = policy_get("fan_out_questions")
            tpl = (data or {}).get("defaults", {}).get("branch_prompt_template")
            if isinstance(tpl, str) and tpl:
                return tpl
        except Exception:
            logger.debug("branch_prompt_template 读取失败（回退内置模板）", exc_info=True)
        return _BUILTIN


def default_scheduler() -> FanOutScheduler:
    """装配助手：默认配置（enable=False 直通挂接，零行为分叉）。"""
    return FanOutScheduler()


def laya_pre_classifier(prompt: str, messages: list) -> dict | None:
    """fast lane 门控版 pre_classifier（B2-R 放宽后）——FanOutScheduler 的消费方。

    接 Laya fast_route（fast_lane.py 主链同款：NOT_GOOD_AT 回退 + B2 confidence/
    薄弱域门控 + difficulty 分桶在调度器侧二次筛）作为预分类器：
    - 返回 fast_route verdict（dict，含 answers.domain/difficulty）
    - None = 不可投机（Laya 不可用/低置信/薄弱域）——调度器据此放弃本轮 fan-out
    这让 fast lane 从「分类抢跑（resolve 链）」升级为「投机预分类（FanOut 链）」
    ——有真实下游消费方（投机分支命中），解决挂账 fastlane-dead-plugin-review 的
    「无消费方」死路径。
    """
    try:
        from laya.presets import router_questions
        from lingclaude.model.fast_lane import fast_route
        return fast_route(prompt[:500], router_questions())
    except Exception:  # noqa: BLE001 — pre_classifier 故障 → None（不投机，fail-soft）
        return None


def assemble_fan_out_scheduler(*, enable: bool = True,
                               use_laya_pre_classifier: bool = True) -> FanOutScheduler:
    """P1-0/B2-R 装配：FanOutScheduler(enable=True) + 可选 Laya pre_classifier。

    - enable=True 才投机（False 走 default_scheduler 直通）
    - use_laya_pre_classifier=True 时接 fast lane 门控版 pre_classifier；
      False 则不用预分类器（只靠 prompt 长度 + fan_out_tags + difficulty 分桶）
    """
    if not enable:
        return default_scheduler()
    pre = laya_pre_classifier if use_laya_pre_classifier else None
    return FanOutScheduler(enable=enable, pre_classifier=pre)
