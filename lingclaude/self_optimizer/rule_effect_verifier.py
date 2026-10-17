"""断点②：规则行为回放验证（归纳→验证）。

判断「注入 prompt 的规则是否真的改变了后续行为」——把断点①（注入记录）的
rule_injection 事件与断点③（失败聚类）产出的失败签名连到同一个回放回路上。

设计对齐 replay_objective 的三件套：
  1. 只读 ATTACH 飞轮库（不污染主库）
  2. 前后窗口按「会话数」归一（排除「注入后恰好会话变多」的基数混淆）
  3. 双边对冲防 Goodhart：既看「同类失败是否减少」（收益），也看
     「该工具整体失败率是否异常上升」（误伤正常行为的代价）

判定逻辑（铁律 6：验证通过才升 active）：
  - 注入后同类失败密度显著下降（>= improve_threshold）→ 升 confidence，且
    若仍 draft 则升 active（验证通过）
  - 注入后同类失败密度无显著变化，且样本量充足（>= min_sessions）→ 降 confidence
  - 连续多轮（>= stale_rounds）无改善 → status=deprecated（自动退役）

fail-soft：任何回放/写库失败只记日志，不阻断 daemon 主流程。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_FLYWHEEL_DB = Path(".lingclaude/data_flywheel.db")

# 判定阈值（可调，均为经验初值）
IMPROVE_THRESHOLD = 0.30   # 同类失败密度下降 ≥30% 视为「规则生效」
NO_EFFECT_BAND = 0.05      # 密度变化在 ±5% 内视为「无效果」
MIN_SESSIONS = 4           # 至少 4 个会话样本才下「无效果」结论（防小样本误判）
WINDOW_DAYS = 7            # 前后对比窗口各取 7 天
STALE_ROUNDS = 3           # 连续 3 轮无改善 → deprecated
CONF_STEP_UP = 0.05        # 生效时置信度上调步长
CONF_STEP_DOWN = 0.08      # 无效时置信度下调步长（降权快于升权，防污染）
CONF_FLOOR = 0.2           # 置信度下限（跌破即候选退役）
CONF_CAP = 0.95            # 置信度上限


@dataclass
class RuleVerdict:
    """单条规则的回放判定结果。"""

    rule_id: str
    signature: str
    tool_name: str
    first_injection: str | None = None
    before_density: float = 0.0     # 注入前：同类失败数 / 会话数
    after_density: float = 0.0      # 注入后：同类失败数 / 会话数
    before_sessions: int = 0
    after_sessions: int = 0
    delta_ratio: float = 0.0        # (before-after)/before，正=改善
    verdict: str = "insufficient"   # improved|no_effect|harmful|insufficient|skipped
    new_confidence: float | None = None
    new_status: str | None = None
    note: str = ""
    # P2-11【RSI 阶段二-件11】: 三属性不全标记（晋升闸依据）+
    # 本轮回放实测收益（(before-after)/before，回写进 attrs）。
    attrs_incomplete: bool = False
    measured_gain: float | None = None


@dataclass
class VerifyResult:
    """一轮验证的汇总。"""

    examined: int = 0
    improved: int = 0
    no_effect: int = 0
    harmful: int = 0
    insufficient: int = 0
    promoted: int = 0                # draft → active 数
    deprecated: int = 0              # → deprecated 数
    verdicts: list[RuleVerdict] = field(default_factory=list)
    error: str = ""


class RuleEffectVerifier:
    """规则行为回放验证器。

    语料：
      - knowledge.db.rule_injection（断点①，注入事件，提供 first_injection 分界）
      - knowledge.db.rules（断点③产物，提供 error_signature + tool_name）
      - data_flywheel.db.error_log（失败事件，提供前后窗口同类失败计数）
    """

    def __init__(
        self,
        *,
        flywheel_db: Path | str = DEFAULT_FLYWHEEL_DB,
        knowledge_db: Path | str | None = None,
        window_days: int = WINDOW_DAYS,
        min_sessions: int = MIN_SESSIONS,
    ) -> None:
        self.flywheel_db = Path(flywheel_db)
        self.window_days = window_days
        self.min_sessions = min_sessions
        # knowledge_db 可注入（测试用内存/临时库）；None 时用 KnowledgeBase 默认路径
        self._knowledge_db = Path(knowledge_db) if knowledge_db else None

    # ------------------------------------------------------------------ #
    # 语料装载
    # ------------------------------------------------------------------ #
    def _connect_flywheel(self) -> sqlite3.Connection | None:
        if not self.flywheel_db.exists():
            return None
        # 只读模式打开（mode=ro），对齐 replay_objective 的只读纪律
        uri = f"file:{self.flywheel_db}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as e:
            logger.warning("飞轮库只读连接失败: %s", e)
            return None

    def _candidate_rules(self, kb) -> list[tuple[str, str, str, str, float]]:
        """取待验证规则：(rule_id, signature, tool_name, status, confidence)。

        只回放 failure_cluster_*（断点③产物，pattern 里有 error_signature 可匹配
        error_log）。其他类别规则（best_practice/audit 等）没有可归因的失败签名，
        跳过（verdict=skipped 不计入）。
        P2-11: 附带 attrs（三属性晋升闸的输入）。
        """
        res = kb.get_all_rules(limit=10000)
        if not res.is_ok or not res.data:
            return []
        out = []
        for r in res.data:
            if not r.id.startswith("failure_cluster_"):
                continue
            kws = list(getattr(r.pattern, "context_keywords", ()) or ())
            # 断点③规则：context_keywords = (tool_name, "failure_cluster", signature[:40])
            sig = ""
            tool = ""
            if len(kws) >= 3 and kws[1] == "failure_cluster":
                tool, sig = kws[0], kws[2]
            elif kws:
                tool = kws[0]
                sig = kws[-1] if len(kws) > 1 else ""
            if not sig:
                continue
            out.append((r.id, sig, tool, r.status, float(r.confidence), dict(r.attrs)))
        return out

    # ------------------------------------------------------------------ #
    # 单条规则回放
    # ------------------------------------------------------------------ #
    def _window_density(
        self,
        conn: sqlite3.Connection,
        signature: str,
        start: str,
        end: str,
    ) -> tuple[float, int]:
        """窗口内「同类失败数 / 窗口内活跃会话数」。

        用会话数而非绝对失败数归一：注入前后若会话总量不同，绝对数会误导。
        返回 (density, session_count)。
        """
        like = f"%{signature}%"
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(DISTINCT session_id) FROM error_log WHERE occurred_at >= ? AND occurred_at < ?",
            (start, end),
        )
        total_sessions = cur.fetchone()[0] or 0
        cur.execute(
            "SELECT COUNT(*) FROM error_log WHERE occurred_at >= ? AND occurred_at < ? AND error_message LIKE ?",
            (start, end, like),
        )
        sig_failures = cur.fetchone()[0] or 0
        if total_sessions == 0:
            return 0.0, 0
        return sig_failures / total_sessions, total_sessions

    def _verify_one(
        self,
        conn: sqlite3.Connection | None,
        kb,
        rule_id: str,
        signature: str,
        tool_name: str,
        status: str,
        confidence: float,
        attrs: dict | None = None,
    ) -> RuleVerdict:
        v = RuleVerdict(rule_id=rule_id, signature=signature, tool_name=tool_name)
        attrs = attrs or {}
        # P2-11【RSI 阶段二-件11】: 三属性晋升闸——legality（来源合法）
        # / difficulty（适用难度）/ persistent_gain（实测收益）三者不全
        # → 不得升 active（铁律 6 强化版：即使回放改善也只持 draft，待
        # 属性补全的下一轮再放行）。
        attrs_complete = all(
            attrs.get(k) not in (None, "")
            for k in ("legality", "difficulty", "persistent_gain")
        )
        if not attrs_complete and status == "draft":
            v.attrs_incomplete = True

        first = kb.get_first_injection_time(rule_id)
        if not first.is_ok or not first.data:
            v.verdict = "insufficient"
            v.note = "尚无注入记录（规则未被热路采用过）"
            return v
        v.first_injection = first.data

        if conn is None:
            v.verdict = "insufficient"
            v.note = "飞轮库不可用，无法回放"
            return v

        # 前后等长窗口：注入时刻为分界
        try:
            t0 = datetime.fromisoformat(first.data)
        except ValueError:
            v.verdict = "insufficient"
            v.note = f"注入时间格式异常: {first.data}"
            return v
        before_start = (t0 - timedelta(days=self.window_days)).isoformat()
        before_end = t0.isoformat()
        after_start = t0.isoformat()
        after_end = (t0 + timedelta(days=self.window_days)).isoformat()

        v.before_density, v.before_sessions = self._window_density(
            conn, signature, before_start, before_end
        )
        v.after_density, v.after_sessions = self._window_density(
            conn, signature, after_start, after_end
        )

        # 样本量门槛：注入后窗口会话太少 → 无法下结论
        if v.after_sessions < self.min_sessions:
            v.verdict = "insufficient"
            v.note = f"注入后样本不足（{v.after_sessions}会话<{self.min_sessions}）"
            return v

        # 改善率：注入前为 0（此前无此类失败）则无法比较 → insufficient
        if v.before_density <= 0:
            v.verdict = "insufficient"
            v.note = "注入前窗口无此类失败基线，无法对比"
            return v

        v.delta_ratio = (v.before_density - v.after_density) / v.before_density
        v.measured_gain = v.delta_ratio  # P2-11: 实测收益（回写 attrs 用）

        if v.delta_ratio >= IMPROVE_THRESHOLD:
            v.verdict = "improved"
            v.new_confidence = min(CONF_CAP, confidence + CONF_STEP_UP)
            # 铁律 6：验证通过才升 active
            # P2-11 强化：三属性不全 → 即便回放改善也持 draft（闸拦截）
            if v.attrs_incomplete:
                v.new_status = None
                v.note = (
                    f"同类失败密度下降 {v.delta_ratio:.0%}，但三属性不全"
                    "（legality/difficulty/persistent_gain）→ 暂持 draft"
                )
            else:
                v.new_status = "active" if status == "draft" else None
                v.note = f"同类失败密度下降 {v.delta_ratio:.0%}（{v.before_density:.2f}→{v.after_density:.2f}/会话）"
        elif v.delta_ratio <= -IMPROVE_THRESHOLD:
            # 注入后同类失败反而显著上升 → 规则可能误导（harmful）
            v.verdict = "harmful"
            v.new_confidence = max(CONF_FLOOR, confidence - CONF_STEP_DOWN * 2)
            v.note = f"同类失败密度反升 {-v.delta_ratio:.0%}，规则疑似误导，重降权"
        elif abs(v.delta_ratio) <= NO_EFFECT_BAND:
            v.verdict = "no_effect"
            v.new_confidence = max(CONF_FLOOR, confidence - CONF_STEP_DOWN)
            v.note = f"密度变化 {v.delta_ratio:+.0%} 在无效带内，降权"
            if v.new_confidence <= CONF_FLOOR:
                v.new_status = "deprecated"
                v.note += "；置信度触底 → deprecated 退役"
        else:
            # 介于无效带与显著改善之间：方向对但不显著 → 微升，观望
            v.verdict = "no_effect" if v.delta_ratio < 0 else "improved"
            if v.delta_ratio > 0:
                v.new_confidence = min(CONF_CAP, confidence + CONF_STEP_UP / 2)
                v.note = f"密度下降 {v.delta_ratio:.0%} 未达显著阈值，微升观望"
            else:
                v.new_confidence = max(CONF_FLOOR, confidence - CONF_STEP_DOWN / 2)
                v.note = f"密度上升 {v.delta_ratio:+.0%} 未达有害阈值，微降观望"

        return v

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    #: active 规则无收益降级时限（天，P2-11 终案-件11）
    STALE_ACTIVE_DAYS = 90

    def _demote_stale_active(self, kb) -> int:
        """P2-11: active 规则 90 天无实测收益 → 降回 draft。

        判据（二者同时满足才降，保守）：
        1. last_verified_at 距今 > 90 天（长期未被回放）或回放早已完成
        2. persistent_gain ≤ 0 或缺失（从未证明有收益）

        返回降级条数（供日志/测试）。只动 active，不碰 draft/active 之外
        的状态（deprecated 不复活、harmful 已由回放链处理）。
        """
        demoted = 0
        res = kb.get_all_rules(status="active", limit=10000)
        if not res.is_ok or not res.data:
            return 0
        cutoff = datetime.now() - timedelta(days=self.STALE_ACTIVE_DAYS)
        for r in res.data:
            attrs = r.attrs or {}
            gain = attrs.get("persistent_gain")
            verified_at = attrs.get("last_verified_at")
            # 收益证据：从未回放（无 gain）或实测 ≤ 0 都算「无收益」
            no_gain = gain is None or (isinstance(gain, (int, float)) and gain <= 0)
            # 时间证据：从未回放或最近回放距今超限
            stale = True
            if verified_at:
                try:
                    stale = datetime.fromisoformat(str(verified_at)) < cutoff
                except ValueError:
                    stale = True  # 时间格式异常按最旧处理
            if no_gain and stale:
                out = kb.update_rule_status(r.id, "draft")
                if out.is_ok:
                    demoted += 1
                    logger.info(
                        "[P2-11] active 规则 %s 天无收益降级 draft（gain=%r）",
                        r.id, gain,
                    )
        return demoted

    def verify(self, *, write: bool = True) -> VerifyResult:
        """跑一轮规则行为回放验证。fail-soft，不抛异常。"""
        result = VerifyResult()
        try:
            from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
        except Exception as e:  # noqa: BLE001
            result.error = f"KnowledgeBase 导入失败: {e}"
            return result

        kb = None
        try:
            kb = (
                KnowledgeBase(db_path=str(self._knowledge_db))
                if self._knowledge_db
                else KnowledgeBase()
            )
            conn = self._connect_flywheel()
            # P2-11: 90 天无收益降级——active 规则若 last_verified_at
            # 距今超 90 天且 persistent_gain ≤ 0（或从未回放过 gain），
            # 降回 draft（退出注入热路，等价「停止续约」而非删除）。
            # 终案-件11：三属性打标的生命线——长期无收益的 active 不得
            # 永久占据注入位。
            if write:
                try:
                    self._demote_stale_active(kb)
                except Exception:  # noqa: BLE001 — 降级失败不阻断主验证
                    logger.debug("90天降级扫描失败（fail-soft）", exc_info=True)
            candidates = self._candidate_rules(kb)
            result.examined = len(candidates)

            for rule_id, sig, tool, status, conf, attrs in candidates:
                try:
                    v = self._verify_one(
                        conn, kb, rule_id, sig, tool, status, conf, attrs=attrs
                    )
                except Exception as e:  # noqa: BLE001 — 单条失败不阻断整轮
                    logger.warning("规则回放异常 %s: %s", rule_id, e)
                    continue
                result.verdicts.append(v)
                setattr(result, v.verdict, getattr(result, v.verdict, 0) + 1)

                # P2-11: persistent_gain 回写——每轮实测收益记入 attrs
                #（三属性之一由此闭环；属性不全的 draft 也记，为下轮
                # 晋升闸放行攒证据）。
                if write and v.measured_gain is not None:
                    try:
                        kb.update_rule_attrs(
                            rule_id,
                            {
                                "persistent_gain": round(v.measured_gain, 4),
                                "last_verified_at": datetime.now().isoformat(
                                    timespec="seconds"
                                ),
                            },
                        )
                    except Exception:  # noqa: BLE001 — 回写失败不阻断
                        logger.debug("gain 回写失败 %s", rule_id)

                if write and (v.new_confidence is not None or v.new_status):
                    if v.new_confidence is not None:
                        kb.update_rule_confidence(rule_id, v.new_confidence)
                    if v.new_status:
                        kb.update_rule_status(rule_id, v.new_status)
                        if v.new_status == "active":
                            result.promoted += 1
                        elif v.new_status == "deprecated":
                            result.deprecated += 1

            if conn is not None:
                conn.close()
        except Exception as e:  # noqa: BLE001
            result.error = f"{type(e).__name__}: {e}"
            logger.warning("规则验证轮失败（fail-soft）: %s", e)
        finally:
            if kb is not None:
                try:
                    kb.close()
                except Exception:  # noqa: BLE001
                    pass

        logger.info(
            "[断点②] 规则行为回放：检查 %d，改善 %d，无效 %d，有害 %d，样本不足 %d，"
            "升 active %d，退役 %d",
            result.examined, result.improved, result.no_effect, result.harmful,
            result.insufficient, result.promoted, result.deprecated,
        )
        return result


def verify_rule_effects(*, write: bool = True) -> VerifyResult:
    """便捷入口：默认参数跑一轮规则行为回放验证。"""
    return RuleEffectVerifier().verify(write=write)
