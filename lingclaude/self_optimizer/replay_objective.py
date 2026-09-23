"""F1 行为参数目标函数 — 离线回放 error_log 序列，评分行为阈值参数。

F1 换尺子（2026-09-22）：optimizer 搜索空间从「benchmark 自身题集阈值」
（Goodhart 空转：调尺子让学生及格）换成被控对象 core/policies/
behavior_policy.yaml 的真实行为阈值（tool_repeat_limit /
consecutive_fail_limit）。

目标函数 = 在历史 error_log 序列上回放「假如阈值=X，干预会怎样」：
  - stuck：某 session 内同一工具连续失败 ≥ consecutive_fail_limit 次
    却从未转入其他工具/修复 → 应干预而未干预，每个漏网按 stuck_penalty 记罚。
  - churn：连续成功 ≥ tool_repeat_limit 次的重复调用被误拦 → 按误
    伤代价 churn_penalty 记罚。

两股代价对冲 → 内点最优解，数学上不可能退化到边界（结构防 Goodhart）。
误差侧单边数据即可评分：终端型错误要求早干预，瞬态型（timeout/rate）
要求留重试余地 —— 同一序列天然产生两类反例。

语料：data_flywheel.db error_log（session_id, tool_name, error_message,
occurred_at）。只读 ATTACH，不写任何库。

F6 双边事件流（2026-09-23，Phase 2 落地）：
  - 源头：DataFlywheel 新增 tool_events 表（session_id, tool_name,
    success, occurred_at），ToolCallExecutor 三条执行路径全量写入，
    成功侧不再缺席 → churn 反力项激活，rl 钳位放开 [3,6]→[2,8]。
  - 消费：load_traces 优先双边表，空/缺失回退 error_log 单边（此时
    churn 项自然归零，行为等同 F1）。
  - 事件 schema：events 升级 (tool, err, success) 三元组，旧二元组
    （success=False）兼容。
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
import sqlite3

_TRANSIENT_RE = re.compile(
    r"timed out|timeout|rate.?limit|429|5\d\d|connection|temporarily",
    re.IGNORECASE,
)


def is_transient(error_message: str) -> bool:
    """瞬态错误判定：超时/限流/连接类，值得给重试余地。"""
    return bool(_TRANSIENT_RE.search(error_message or ""))


@dataclass
class SessionTrace:
    """单 session 工具事件序列（按时间升序）。

    F6: events 升级为 (tool, err, success) 三元组；err 对成功事件为 ""。
    索引访问 e[0]/e[1]/e[2] 兼容旧 (tool, err) 二元消费方（长度=2 时
    success 视为 False，即纯失败语料）。
    """

    session_id: str
    events: list[tuple] = field(default_factory=list)


def load_traces(
    db_path: str | Path,
    max_sessions: int = 2000,
) -> list[SessionTrace]:
    """从 data_flywheel.db 读双边事件，按 session 聚合成回放轨迹。

    F6 (2026-09-23): 优先读 tool_events 双边表（成功+失败完整时序），
    表空/缺失时回退 error_log 单边语料（纯失败序列，success 全 False）。
    只读；库不存在时返回空表（调用方回退默认参数评分，graceful）。
    """

    path = Path(db_path).expanduser()
    if not path.exists():
        return []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name='tool_events'"
        ).fetchone()[0]
        if n and conn.execute("SELECT COUNT(*) FROM tool_events").fetchone()[0]:
            rows = [
                (sid, tool, "", bool(succ))
                for sid, tool, succ in conn.execute(
                    "SELECT session_id, tool_name, success FROM tool_events "
                    "ORDER BY occurred_at"
                ).fetchall()
            ]
        else:
            # 回退：单边失败语料（F1 原始路径）
            rows = [
                (sid, tool, err, False)
                for sid, tool, err in conn.execute(
                    "SELECT session_id, tool_name, error_message FROM error_log "
                    "ORDER BY occurred_at"
                ).fetchall()
            ]
    finally:
        conn.close()

    grouped: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)
    for sid, tool, err, succ in rows:
        grouped[str(sid)].append((str(tool or "?"), str(err or ""), succ))
    # 取"最活跃"的 max_sessions 个会话：噪声少、代表性强
    top = sorted(grouped.items(), key=lambda kv: -len(kv[1]))[:max_sessions]
    return [SessionTrace(session_id=k, events=v) for k, v in top]


def _tolerance_cost(k: int, cheap: float = 0.1, expensive: float = 1.0) -> float:
    """容忍第 k 次同工具重复失败的边际代价。

    前 2 次是模型看到报错后的正常自纠尝试（廉价）；第 3 次起
    大概率是同一姿势的空转（昂贵）。干预摩擦（nudge_cost）与
    「多容忍几次廉价自纠」对冲 → 内点最优，防边界退化。
    """
    return cheap if k <= 2 else expensive


def score_params(
    traces: list[SessionTrace],
    params: dict[str, float],
    heal_loss: float = 0.8,
    nudge_cost: float = 0.6,
) -> float:
    """回放评分（minimize）：params 阈值下的总代价。

    内点对冲设计（2026-09-22 真实数据曲线三次修正终型）：
      - 终端段（语法/参数类错误，重试无意义）：
        cost += Σ_{k=1..min(L,fl)} _tolerance_cost(k)
        fl 越大容忍的空转越多（第3次起每次1.0 → 压低 fl）
        若 fl ≤ L 触发干预，再 + nudge_cost
        （干预打断心流；对海量短段的无谓打扰 → 推高 fl）
      - 瞬态段（timeout/rate/连接类，重试可能自愈）：
        max(0, L - fl) × heal_loss（过早拦截剥夺自愈 → 推高 fl）
    内点最优由语料段长分布自然决定：短段主导 → 解偏保守；
    若未来长段（真 stuck）占比升高，最优解自动左移。数据说了算。

    tool_repeat_limit：F6 (2026-09-23) 双边事件流接入后放开的反力项——
    连续成功 ≥ rl 次的同工具重复调用若被误拦，按 churn_penalty × 次
    记罚（ rl 越小误拦越多 → 推高 rl）。误差侧反力（推低 fl）与成功
    侧反力（推高 rl）对冲 → rl 也进入内点。双边语料缺失时（回退单边）
    成功事件不存在，churn 项自然归零，行为退化回 F1 语义。
    """
    fl = max(1, int(params.get("consecutive_fail_limit", 3)))
    rl = max(1, int(params.get("tool_repeat_limit", 3)))
    churn_penalty = 0.4

    total = 0.0
    for trace in traces:
        events = trace.events
        n = len(events)
        i = 0
        while i < n:
            tool = events[i][0]
            j = i
            while j < n and events[j][0] == tool:
                j += 1
            run_len = j - i

            def _ok(e: tuple) -> bool:
                return len(e) > 2 and bool(e[2])

            ok_run = all(_ok(e) for e in events[i:j])
            if ok_run:
                # F6 churn 反力项：健康重复被误拦的代价（推高 rl）。
                # 第 rl 次之后的每次多余调用才是误拦伤害（拦的动作发生在
                # 调用后，第 rl 次调用本身无伤害 → run_len - rl）。
                if run_len > rl:
                    total += (run_len - rl) * churn_penalty
            elif any(is_transient(e[1]) for e in events[i:j]):
                total += max(0, run_len - fl) * heal_loss
            else:
                tolerated = min(run_len, fl)
                for k in range(1, tolerated + 1):
                    total += _tolerance_cost(k)
                if run_len >= fl:
                    total += nudge_cost
            i = j
    return total


class ReplayObjective:
    """lingminopt evaluate 契约封装：evaluate(params: dict) -> float。

    语料加载一次缓存复用；库缺失时退化为「缺省参数零代价」常函数，
    optimizer 会返回搜索空间起点（不报错，安全空转）。
    """

    def __init__(
        self,
        db_path: str | Path = "/home/ai/lingclaude/.lingclaude/data_flywheel.db",
        heal_loss: float = 0.8,
        nudge_cost: float = 0.6,
        max_sessions: int = 2000,
    ) -> None:
        self.traces = load_traces(db_path, max_sessions=max_sessions)
        self.heal_loss = heal_loss
        self.nudge_cost = nudge_cost

    def evaluate(self, params: dict[str, float]) -> float:
        if not self.traces:
            return 0.0
        return score_params(
            self.traces,
            params,
            heal_loss=self.heal_loss,
            nudge_cost=self.nudge_cost,
        )
