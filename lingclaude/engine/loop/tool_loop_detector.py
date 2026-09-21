"""P0-A L0 批次 3（2026-09-22）：工具打转检测器 + 循环常量（自 core/model_call.py 迁入）。

L0 只挪不改：_ToolLoopDetector / _R5_THRESHOLDS / _LOOP_WARN_HINT /
_LOOP_ABORT_MSG 逐字迁移，行为零变化。消费方（core/model_call.py、
engine/coding.py、tests）直切本模块（契约 §七：不落 shim，M3 铁律）。
"""
from __future__ import annotations

_LOOP_WARN_HINT = (
    "[系统提示] 检测到与历史完全相同的工具调用（原地打转）。"
    "请改变方法/参数，或直接基于已有信息作答，不要重复同一调用。"
    "若下一轮仍出现完全相同调用，任务将被熔断中止。"
)
# 熔断后的继续指引：告诉用户会话仍存活、计数已清零、如何继续。
_LOOP_RECOVER_GUIDE = (
    "[继续指引] 熔断仅中止本轮工具循环，会话与输入通道完好，打转计数已清零。"
    "请直接重新提问即可继续，无需重启进程——"
    "① 改变目标/范围（例如'先只做A，别碰B'）；"
    "② 拆小任务，逐步推进，每步都有新产出；"
    "③ 给出明确方向（例如'按X方案做，先验证Y'）。"
)
_LOOP_ABORT_MSG = (
    "[循环检测] 连续两轮重复完全相同的工具调用（原地打转），任务已熔断停止。"
    "已完成的部分结果如上。"
    + _LOOP_RECOVER_GUIDE
)


class _ToolLoopDetector:
    """区分"原地打转"（重复相同调用）与"正常推进"（每轮有新产出）。

    判定：一轮工具调用的签名 (name, arguments) 全部在历史中出现过 = 打转轮。
    - 连续第 1 次打转 → warn（把纠偏提示注入下一轮消息，给模型改错机会）
    - 连续第 2 次打转 → abort（熔断）
    - 有任何新调用   → 正常推进，streak 清零
    纯文本轮（无工具调用）不参与判定（那是回答，不是循环）。

    5b：denial 熔断（独立分支,按 rule_id 聚合）。
    - 同 rule_id 连续 N 次触发 → 返回 "denial_warn" / "denial_abort"
    - 与现有打转检测并存:打转管 (name, arguments) 重复、denial 管 rule_id 重复
    - 阈值 R5_THRESHOLDS 字典:读工具放宽、写工具收紧（探索类/危险类分开）
    """

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()
        self._streak = 0
        # 5b: rule_id 熔断状态
        self._denial_streak: dict[str, int] = {}  # rule_id -> 连续触发计数

    def observe_round(self, calls: list[tuple[str, str]]) -> str | None:
        """观察一轮调用，返回 'warn' / 'abort' / None。"""
        if not calls:
            return None
        sig = set(calls)
        has_new = any(c not in self._seen for c in sig)
        self._seen.update(sig)
        if has_new:
            self._streak = 0
            return None
        self._streak += 1
        if self._streak >= 2:
            return "abort"
        return "warn"

    def observe_denial(self, rule_id: str, tool_name: str, threshold: int = 2) -> str | None:
        """5b：观察一次 denial，按 rule_id 聚合。

        Args:
            rule_id: 5a 结构化规则标识（如 "config.deny_tools.exact"）
            tool_name: 关联工具名（用于日志 / 后续扩展;不计入判定）
            threshold: 触发熔断的连续次数（默认 2,与现有 abort 阈值一致）

        Returns:
            None              : 未达阈值,正常放行
            "denial_warn"     : 第 1 次,告警（不再注入 system note,本轮已经记录）
            "denial_abort"    : 达到阈值,熔断（暂停+升级语义见 §7.4）
        """
        self._denial_streak[rule_id] = self._denial_streak.get(rule_id, 0) + 1
        n = self._denial_streak[rule_id]
        if n >= threshold:
            return "denial_abort"
        return "denial_warn"

    def reset_denial(self, rule_id: str) -> None:
        """当一轮成功（无 denial）时,调用此清空对应 rule_id 计数。

        与 observe_round 的 has_new 清零 streak 一致——只要有一次成功就重置。
        """
        self._denial_streak.pop(rule_id, None)


# 5b 阈值表（按工具类型分维度调整;危险工具收紧,只读工具放宽）
# 默认门槛 2,与现有 _ToolLoopDetector 行为一致;后续可按工具类型 read/write 区分
_R5_THRESHOLDS: dict[str, int] = {
    "default": 2,
    # 未来可扩展:
    # "config.deny_tools.exact": {"bash": 1, "read": 5},  # bash 一次性熔断
    # "strict_mode.non_readonly": {"write": 1},  # 写工具一次性熔断
}
