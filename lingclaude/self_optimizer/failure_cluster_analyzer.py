"""failure_cluster_analyzer — 断点③：失败自动归因 → 生成修复建议。

灵元铁律对齐：
- 铁律 4（修剪语法）：同型失败聚类超阈值 → 凝练成 LearnedRule（可归因、可降权），
  而非流水账堆积；规则经 KnowledgeBase.add_rule 的同名合并语义幂等入库。
- 铁律 6（信任等级）：产出 status="draft"，须验证段（断点②）回放后才升 active——
  不默认放行，不假活。

职责边界（2026-09-28 首版）：
- 只读 error_log（只读 ATTACH，对齐 replay_objective 范式），不写飞轮库。
- 聚类键 = (tool_name, 归一化 error 首行)：去除路径/数字/会话 id 等易变位，
  让「同型失败」真正聚合（实测头部聚类 NoneType.execute_tool 28972 条即此类）。
- 降噪：error_log 实测混入测试夹具污染（MagicMock/「fail」字面量/会话封顶 30 条），
  故引入 min_sessions 跨会话判据 + 通用噪声词过滤，避免把测试噪声学成规则。

产出两路：
  1. LearnedRule(category=TOOL_ERROR) → knowledge.db（供检索注入，断点①下游）
  2. backlog 记录（data/selfopt/failure_backlog.jsonl）→ 修复建议待执行队列

这是「13 连败人工查 journal」→「daemon 自动聚类产规则」的机制化。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 默认飞轮库（只读消费，对齐 replay_objective 的 mode=ro）
DEFAULT_FLYWHEEL_DB = Path(".lingclaude/data_flywheel.db")
DEFAULT_BACKLOG = Path("data/selfopt/failure_backlog.jsonl")

# ── 降噪词表：实测 error_log 中测试夹具/通用噪声，不具可归因性 ──────────
_NOISE_SUBSTRINGS = (
    "magicmock",           # 测试夹具
    '"error": "fail"',     # 测试桩通用失败
    "test_",               # 测试路径
    "pytest",
)

# 归一化：把 error_message 里的易变位（路径/数字/会话 id/hex）抹平，
# 让同型失败聚合到同一 cluster_key。
_RE_ABS_PATH = re.compile(r"/[\w\-./]+")
# 数字含带单位形态（120s/99ms）：\d+ 后直接跟字母也抹平（\b 词边界对
# 「120s」中的 120 不匹配——s 是单词字符）。用 (?<![\w.]) 前向防误伤
# 标识符内数字（如 utf8 / base64 的 8/64 是语义一部分，保留）。
_RE_NUM = re.compile(r"(?<![\w.])\d+")
_RE_HEX = re.compile(r"\b[0-9a-f]{8,}\b")
# JSON 包裹的错误（{"error": "..."}）——抽取内层错误文本作为签名核心，
# 丢弃 error_code/tool_name 等结构化后缀（实测同型 NoneType.execute_tool
# 因后缀有无被拆成两簇）。
_RE_JSON_ERR = re.compile(r'^\{"error":\s*"(.+?)"(?:\s*[,}])', re.DOTALL)


def _normalize_error(msg: str) -> str:
    """归一化错误首行 → 稳定 cluster_key（同型聚合）。

    先抽取 JSON 内层错误文本（去结构化后缀），再抹平易变位，截断 80。
    """
    first = (msg or "unknown").strip().splitlines()[0]
    # JSON 包裹：提取 "error" 字段值作为签名（其余结构化字段丢弃）
    m = _RE_JSON_ERR.match(first)
    if m:
        first = m.group(1)
    first = _RE_ABS_PATH.sub("<PATH>", first)
    first = _RE_HEX.sub("<HEX>", first)
    first = _RE_NUM.sub("<N>", first)
    # 统一截断 80——区分错误类型足够，不带入尾部易变 payload。
    return first[:80]


def _is_noise(cluster_key: str, sample_msg: str) -> bool:
    """测试夹具/通用噪声判别——这类失败学不出可迁移规则。"""
    blob = (cluster_key + " " + (sample_msg or "")).lower()
    return any(n in blob for n in _NOISE_SUBSTRINGS)


@dataclass
class FailureCluster:
    """同型失败聚类（一次归因的最小单位）。"""

    cluster_key: str                 # (tool, 归一化错误) 的稳定键
    tool_name: str
    error_signature: str             # 归一化后的错误签名
    occurrences: int                 # 总出现次数
    distinct_sessions: int           # 跨会话数（判据：≥2 才信是真实模式）
    first_seen: str
    last_seen: str
    sample_message: str = ""         # 一条原始样本（供人工复核）
    hypothesis: str = ""             # 根因假设（启发式生成）
    fix_suggestion: str = ""         # 修复建议（进 backlog）


@dataclass
class ClusterAnalysisResult:
    """一次聚类分析的产出。"""

    ok: bool
    clusters_found: int = 0          # 窗口内扫到的聚类总数
    actionable: int = 0              # 跨会话 ≥min_sessions 的可行动聚类
    rules_written: int = 0           # 实际入库的 LearnedRule 数
    backlog_appended: int = 0        # 追加进 backlog 的修复建议数
    clusters: list[FailureCluster] = field(default_factory=list)
    error: str = ""


def _hypothesize(tool: str, sig: str) -> tuple[str, str]:
    """启发式根因假设 + 修复建议（首版规则化，不接 LLM——可解释优先）。

    返回 (hypothesis, fix_suggestion)。按错误签名模式分派。
    """
    s = sig.lower()
    if "nonetype" in s and "execute_tool" in s:
        return (
            "工具运行时未初始化（engine._runtime 缺失）即被调用——入口路径漏 set_runtime",
            "在工具执行入口加 runtime 就绪断言 + 失败时返回明确 ToolResult 而非裸 AttributeError",
        )
    if "runtime not initialized" in s or "no _runtime" in s:
        return (
            "Tool runtime 未初始化（entry path missed set_runtime）",
            "统一工具执行入口的 runtime 注入点，杜绝绕过 set_runtime 的直连调用",
        )
    if "连续模型调用失败" in s or "连续工具失败" in s:
        return (
            "连续失败硬中断——provider 连续失败触发熔断（多为配额/断网，非代码缺陷）",
            "派发前预检 provider 可用性 + 429 熔断切换 provider，避免无效重试放大配额消耗",
        )
    if "command failed" in s:
        return (
            "bash 命令执行失败（EXECUTION_ERROR）——多为命令本身非零退出或环境缺失",
            "对高频失败命令归类：环境缺失补依赖，命令错误则需上游修正拼参逻辑",
        )
    if "timed out" in s or "timeout" in s:
        return (
            "工具执行超时——长任务走了同步 bash 默认上限",
            "长任务改 run_in_background + job_status 轮询，或显式传更大 timeout",
        )
    if "permission" in s or "blocked by permissions" in s:
        return (
            "权限闸拦截——工具未获授权（可能是审批模式配置过严或路径误判）",
            "复核 permissions 配置与敏感路径标记，区分「正确拦截」与「误伤正常操作」",
        )
    if "connection" in s or "network" in s or "429" in s:
        return (
            "外部服务不可达/配额耗尽——provider 429 或断网",
            "派发前预检 provider 可用性 + 429 熔断切换，避免无效重试放大配额消耗",
        )
    if "no such file" in s or "not found" in s:
        return (
            "路径引用不存在——可能是相对路径基准漂移或产物未落盘",
            "路径引用前实测存在性（resolve + exists 校验），失败给明确缺失路径",
        )
    # 通用兜底
    return (
        f"工具 {tool} 反复同型失败（签名: {sig[:60]}）——需人工深挖根因",
        "对该聚类取样本人工复核，确认是可自动修复的系统性问题还是环境噪声",
    )


class FailureClusterAnalyzer:
    """失败聚类归因器：扫 error_log → 聚类 → 产规则 + 修复建议。

    只读飞轮库（ATTACH mode=ro），幂等入库（KnowledgeBase 同名合并语义）。
    """

    def __init__(
        self,
        flywheel_db: Path | str = DEFAULT_FLYWHEEL_DB,
        backlog_path: Path | str = DEFAULT_BACKLOG,
        *,
        min_occurrences: int = 3,
        min_sessions: int = 2,
        window_days: int = 7,
    ) -> None:
        self.flywheel_db = Path(flywheel_db)
        self.backlog_path = Path(backlog_path)
        self.min_occurrences = min_occurrences
        self.min_sessions = min_sessions
        self.window_days = window_days

    # ------------------------------------------------------------------ #
    # 段 1：聚类（只读扫 error_log）
    # ------------------------------------------------------------------ #
    def _cluster(self) -> list[FailureCluster]:
        """扫窗口内 error_log，按 (tool, 归一化错误) 聚类。"""
        if not self.flywheel_db.exists():
            logger.info("飞轮库不存在 %s，跳过聚类", self.flywheel_db)
            return []
        try:
            # 只读 ATTACH 范式（对齐 replay_objective：不污染主库连接）
            conn = sqlite3.connect(f"file:{self.flywheel_db}?mode=ro", uri=True)
        except sqlite3.Error as e:
            logger.warning("飞轮库只读打开失败: %s", e)
            return []
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT tool_name, error_message, session_id, occurred_at
                FROM error_log
                WHERE occurred_at >= datetime('now', ?)
                ORDER BY occurred_at DESC
                """,
                (f"-{self.window_days} days",),
            )
            rows = cur.fetchall()
        except sqlite3.Error as e:
            logger.warning("error_log 查询失败: %s", e)
            return []
        finally:
            conn.close()

        # 内存聚合：(tool, sig) → 聚类桶
        buckets: dict[tuple[str, str], dict[str, Any]] = {}
        for tool, msg, session, at in rows:
            sig = _normalize_error(msg)
            if _is_noise(sig, msg):
                continue
            key = (tool or "unknown", sig)
            b = buckets.setdefault(key, {
                "count": 0, "sessions": set(),
                "first": at, "last": at, "sample": msg,
            })
            b["count"] += 1
            b["sessions"].add(session or "")
            b["last"] = max(b["last"], at)
            b["first"] = min(b["first"], at)

        clusters: list[FailureCluster] = []
        for (tool, sig), b in buckets.items():
            if b["count"] < self.min_occurrences:
                continue
            hypo, fix = _hypothesize(tool, sig)
            clusters.append(FailureCluster(
                cluster_key=f"{tool}::{sig[:80]}",
                tool_name=tool,
                error_signature=sig,
                occurrences=b["count"],
                distinct_sessions=len(b["sessions"]),
                first_seen=b["first"],
                last_seen=b["last"],
                sample_message=(b["sample"] or "")[:200],
                hypothesis=hypo,
                fix_suggestion=fix,
            ))
        # 按跨会话数 × 频次排序，最可归因的在前
        clusters.sort(key=lambda c: (c.distinct_sessions, c.occurrences), reverse=True)
        return clusters

    # ------------------------------------------------------------------ #
    # 段 2：产 LearnedRule（幂等入库）
    # ------------------------------------------------------------------ #
    def _to_rule(self, c: FailureCluster):
        """聚类 → LearnedRule（category=TOOL_ERROR, status=draft）。

        id 含归一化签名哈希——同型失败复扫时 id 稳定，触发 add_rule 的
        同名合并（frequency 累加），不产生流水账新行。
        """
        from lingclaude.self_optimizer.learner.models import (
            FeedbackCategory,
            LearnedRule,
            Pattern,
        )
        import hashlib
        sig_hash = hashlib.sha1(c.cluster_key.encode()).hexdigest()[:10]
        # 置信度随跨会话证据累积：≥2 会话 0.6，≥5 会话 0.75（封顶留验证段上调空间）
        conf = 0.6 if c.distinct_sessions < 5 else 0.75
        return LearnedRule(
            id=f"failure_cluster_{c.tool_name}_{sig_hash}",
            name=f"失败归因:{c.tool_name}",
            description=(
                f"同型失败聚类：{c.error_signature[:80]} "
                f"（{c.occurrences}次/{c.distinct_sessions}会话 "
                f"{c.first_seen[:10]}~{c.last_seen[:10]}）。假设：{c.hypothesis}"
            ),
            category=FeedbackCategory.TOOL_ERROR,
            pattern=Pattern(
                context_keywords=(c.tool_name, "failure_cluster", c.error_signature[:40]),
                tool_support=(c.tool_name,),
            ),
            tools=(c.tool_name,),
            frequency=c.occurrences,
            confidence=conf,
            status="draft",  # 铁律 6：须验证段回放后才升 active
        )

    # ------------------------------------------------------------------ #
    # 段 3：主入口（分析 + 入库 + backlog）
    # ------------------------------------------------------------------ #
    def analyze(self, *, write: bool = True) -> ClusterAnalysisResult:
        """跑一次聚类归因。

        write=False 时只分析不落库（干跑，供测试/预览）。
        """
        clusters = self._cluster()
        actionable = [c for c in clusters if c.distinct_sessions >= self.min_sessions]
        result = ClusterAnalysisResult(
            ok=True,
            clusters_found=len(clusters),
            actionable=len(actionable),
            clusters=actionable,
        )
        if not write or not actionable:
            return result

        # 入库 LearnedRule
        try:
            from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
            kb = KnowledgeBase()
            for c in actionable:
                r = kb.add_rule(self._to_rule(c))
                if r.is_ok:
                    result.rules_written += 1
            kb.close()
        except Exception as e:
            logger.warning("LearnedRule 入库失败（fail-soft，不阻断 backlog）: %s", e)
            result.error = f"knowledge write: {e}"

        # 追加 backlog（修复建议待执行队列）
        # 2026-09-28 断点④闭环补：按 cluster_key 去重——此前每轮全量重扫 error_log
        # 会把同一聚类无限追加（实测 49 条 = 49 簇，但含 5 类重复 hypothesis）。
        # 已有未执行的同 key 条目不重复追加（pending/executing 态视为「在队」）。
        try:
            self.backlog_path.parent.mkdir(parents=True, exist_ok=True)
            existing_pending_keys = self._read_pending_cluster_keys()
            new_items = [c for c in actionable if c.cluster_key not in existing_pending_keys]
            with self.backlog_path.open("a", encoding="utf-8") as f:
                for c in new_items:
                    f.write(json.dumps({
                        "at": datetime.now().isoformat(),
                        "type": "failure_cluster_fix",
                        "status": "pending",  # 断点④：BacklogExecutor 消费后改 executed/skipped
                        "cluster_key": c.cluster_key,
                        "tool": c.tool_name,
                        "occurrences": c.occurrences,
                        "sessions": c.distinct_sessions,
                        "hypothesis": c.hypothesis,
                        "fix_suggestion": c.fix_suggestion,
                        "sample": c.sample_message,
                    }, ensure_ascii=False) + "\n")
                    result.backlog_appended += 1
            if not new_items and actionable:
                logger.debug("backlog 无新增（%d 簇已在队列中）", len(actionable))
        except OSError as e:
            logger.warning("backlog 写入失败: %s", e)
            result.error = (result.error + f" | backlog: {e}").strip(" |")

        logger.info(
            "失败聚类归因：扫到 %d 聚类，可行动 %d，入库规则 %d，backlog +%d",
            result.clusters_found, result.actionable,
            result.rules_written, result.backlog_appended,
        )
        return result

    # ------------------------------------------------------------------ #
    # 辅助：读取 backlog 中 pending/executing 状态的 cluster_key 集合
    # ------------------------------------------------------------------ #
    def _read_pending_cluster_keys(self) -> set[str]:
        """读 backlog 里未终结（pending/executing）状态的 cluster_key。

        用于去重：同一聚类在队列中已有待执行项时，不重复追加。
        返回空集表示无 pending（或 backlog 文件不存在/损坏——fail-soft）。
        """
        keys: set[str] = set()
        if not self.backlog_path.exists():
            return keys
        try:
            with self.backlog_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    status = entry.get("status", "pending")
                    if status in ("pending", "executing"):
                        keys.add(entry.get("cluster_key", ""))
        except OSError:
            pass  # fail-soft：读失败就当无 pending，让 analyzer 继续追加
        return keys


def analyze_failure_clusters(
    flywheel_db: Path | str = DEFAULT_FLYWHEEL_DB,
    *,
    write: bool = True,
    **kwargs: Any,
) -> ClusterAnalysisResult:
    """便捷入口：默认库跑一次失败聚类归因。"""
    return FailureClusterAnalyzer(flywheel_db, **kwargs).analyze(write=write)
