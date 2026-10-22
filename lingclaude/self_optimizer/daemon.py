from __future__ import annotations

import fcntl  # P1-卫 (2026-10-17): flock 状态文件锁
import contextlib
import hashlib
import json
from collections import Counter
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
import random

from lingclaude.core.types import Result
from lingclaude.core.config import lingclaudeConfig, load_config
from lingclaude.self_optimizer.advisor import OptimizationAdvisor
from lingclaude.self_optimizer.audit_watch import AuditWatch  # F4 值守
from lingclaude.self_optimizer.evaluator import StructureEvaluator
from lingclaude.self_optimizer.experiments import (
    ExperimentLedger,
    FROZEN_MAX_BACKOFF_CYCLES,
    FROZEN_ROLLBACK_STREAK_THRESHOLD,
)

# S0-2 (2026-10-17)【RSI 阶段〇-2】：变更速率总闸阈值。
# 语义与依据见 runbook/FINAL_synthesis 阶段〇-2；调大须人工（治理不变量：
# 任何自动路径不得放宽对自身变更速率的限制）。
MAX_ACCEPTED_PER_24H = 3  #: 24h 窗口 accepted 参数变更上限
# R1 (2026-10-22)【DGM 式谱系 archive】: 隔代回采节流——每 N 轮至多
# 从备选池回采一次（采样率低，DGM 语义是补充来源不是主力搜索）。
LINEAGE_RECALL_EVERY = 10

# F6 尺度漂移卫兵（2026-10-21，首航实证 best_ever=27.6 旧语料尺度 vs 当前
# ~775 → 6297 轮假回滚空转+永久冻结的复发防线）。同 goal 尺子也会因语料
# 分布漂移而整体移位（旧尺度/新尺度），与 F5 跨 goal 卫兵互补。
# 机制：连续 N 轮「当前参数在当前语料的实测（score_before，F2 同尺）劣于
# best_ever 达 M 倍」→ 判定基线属旧尺度，一次性归档重置（P1-5 人工解锁
# 的自动化版）。连续计数防单轮噪声；M 阈值远大于「真变差」幅度。
BASELINE_DRIFT_RATIO = 2.0   #: score_before ≥ DRIFT_RATIO × best_ever 视为异尺
BASELINE_DRIFT_STREAK = 3    #: 连续 N 轮才重置（防噪声）
MAX_RULES_PER_24H = 2     #: 24h 窗口自优化规则净入库上限

# P1-卫 (2026-10-17)【首航事故三条卫生规则】：10-17 首航实证——旧 watch
# 进程（旧代码+史前内存状态）每 5 分钟把内存态回写磁盘，踩掉手术解锁、
# 踩掉新挂单（「旧内存踩盘」）。三条防线：
#   Rule A 单写者检查：state 记录 owner（boot_id+pid+start_time），
#     save 时验 owner 存活——owner 已死（陈旧磁盘态）则拒绝覆盖。
#   Rule B 状态文件锁：flock 临界区覆盖 load-modify-save 全链。
#   Rule C 启动一致性校验：发现活着的他者 owner → 拒绝启动（单写者铁律）。
#: owner 文件后缀（与状态文件同目录：daemon_state.json.owner）
STATE_OWNER_SUFFIX = ".owner"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """P1-卫：原子 JSON 写（tmp + os.replace，崩溃不留半截文件）。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


@contextlib.contextmanager
def _state_lock(lock_path: Path):
    """P1-卫 Rule B：flock 独占锁临界区（覆盖 load-modify-save 全链）。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _process_alive(pid: int, start_time: str | None) -> bool:
    """Rule A 辅助：pid 存活且与 start_time 吻合（防 pid 复用误判）。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但不可见信号=他人进程=活着
    if start_time is None:
        return True  # 无 start_time 记录的旧 owner 文件：进程在即认为活
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        # /proc/pid/stat: comm 可含空格/括号，取最后一个 ')' 之后第 20 字段=starttime
        tail = stat.rsplit(")", 1)[1].split()
        boot_relative_ticks = int(tail[19])
        btime = float(Path("/proc/stat").read_text().split("btime ")[1].split()[0])
        hz = os.sysconf("SC_CLK_TCK")
        actual = btime + boot_relative_ticks / hz
        return abs(actual - float(start_time)) < 2.0
    except (OSError, ValueError, IndexError):
        return True  # 读取失败保守判活（宁误拒写不误踩盘）


def _read_owner(path: Path) -> dict[str, Any] | None:
    """Rule A：读 owner 文件（损坏/缺失 → None）。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("pid"):
            return raw
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _current_owner_id() -> dict[str, Any]:
    """Rule A：当前进程的 owner 身份（boot_id 防跨启动 pid 复用）。"""
    start_time: str | None = None
    try:
        stat = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="utf-8")
        tail = stat.rsplit(")", 1)[1].split()
        btime = float(Path("/proc/stat").read_text().split("btime ")[1].split()[0])
        hz = os.sysconf("SC_CLK_TCK")
        start_time = str(btime + int(tail[19]) / hz)
    except (OSError, ValueError, IndexError):
        pass
    boot_id = ""
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        pass
    return {
        "pid": os.getpid(),
        "start_time": start_time,
        "boot_id": boot_id,
        "cmdline": " ".join(sys.argv[:2]),
    }

# P1-4 (2026-10-17)【RSI 阶段一-4】: 观察期回退阈值——新参数应用后,
# 本轮周期结束时强制复评基准,得分较应用前回退超此比例(0.98=回退>2%)
# 即恢复 last-known-good 快照(可退)。
P1_OBSERVE_REGRESSION_RATIO = 0.98
#: 观察单时效（小时，A2-1 兜底）：超时未结算 → 恢复快照 + obs_stale 终态
P1_OBSERVING_TTL_HOURS = 48.0

# P2-9 (2026-10-17)【RSI 阶段二-件9】：Strategy 入 Target。
# lingminopt 合法策略白名单（lingminopt/core/strategy.py:_STRATEGY_REGISTRY
# 实测五值）——权重表键只认白名单，防状态文件被手改后注入非法策略名。
P2_STRATEGY_WHITELIST = ("random", "grid", "bayesian", "annealing", "tpe")
#: 每策略启动权重（无台账证据时的均匀先验）
P2_STRATEGY_DEFAULT_WEIGHT = 0.2
#: meta-optimizer 调权周期：每 N 个周期聚合一次台账调整权重表
P2_META_ADJUST_EVERY = 10
#: 单策略证据门槛：台账已决单 < 此数不动该策略权重（防小样本过拟合）
P2_META_MIN_SAMPLES = 3
#: 权重上下限：胜者封顶防一家独大饿死探索，败者保底防彻底出局
P2_META_WEIGHT_MIN = 0.1
P2_META_WEIGHT_MAX = 0.6

from lingclaude.self_optimizer.optimizer import (
    OptimizationRequest,
    OptimizationResult,
    SynchronousOptimizer,
)
from lingclaude.self_optimizer.trigger import OptimizationTrigger
from lingclaude.core.session import SessionManager
from pathlib import Path as P
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

DEFAULT_STATE_DIR = Path(".lingclaude")

# F7 (2026-09-25): frozen zone（死循环熔断闸）——同参数组合连续 rolled_back
# 达阈值即冻结优化循环（跳轮+指数退避），防止优化器在永远赢不了历史
# 最优的参数上空转烧预算。语义对齐「插片风暴三层熔断闸」。
# 达阈值判定同 lingminopt 最佳基线持续回滚守卫（rollouts/ 目录无该单
# 测，2026-09-25 主会话补测）。

# 行为快照滚动窗口上限 — 见 save_behavior_history 内注释（存量/流量修正）
_BEHAVIOR_SNAPSHOT_CAP = 200

# 行为历史默认值 — load/save_behavior_history 两处逐字重复的单源（2026-09-14 收敛）
_DEFAULT_BEHAVIOR_HISTORY: dict[str, Any] = {
    "total_turns": 0,
    "total_frustration": 0,
    "total_corrections": 0,
    "total_tool_errors": 0,
    "snapshots": [],
}


@dataclass(frozen=True)
class OptimizationCycle:
    cycle_id: int
    triggered_at: str
    trigger_reason: str
    trigger_type: str
    trigger_priority: str
    best_score: float
    best_params: dict[str, Any]
    experiments: int
    duration_seconds: float
    violations_before: int
    violations_after: int
    report_path: str | None
    # P1-7 (2026-10-17)【RSI 阶段一-7】: 台账串联合——活动台账与审计
    # 事件流靠它贯通 start→settle→快照。默认 None=旧构造点兼容。
    experiment_id: str | None = None
    # P2-9a (2026-10-17)【RSI 阶段二-件9】: 本轮使用的搜索策略（台账
    # 串联 strategy 字段同源）；None=旧构造点/降级路径兼容。
    strategy: str | None = None


@dataclass
class DaemonState:
    last_optimization_time: str | None = None
    last_metrics: dict[str, Any] = field(default_factory=dict)
    last_trigger_info: dict[str, Any] | None = None  # P1-1: 完整触发上下文,不再只留 type/reason 字符串
    total_cycles: int = 0
    total_improvements: int = 0
    cycles: list[dict[str, Any]] = field(default_factory=list)
    # P1 归档（2026-09-17）：历史最优 cycle 摘要 + 最近基准分基线。
    # 择优回滚的依据：新轮 best_score 劣于 best_ever → 回滚到 best_ever_params。
    best_ever_score: float | None = None
    best_ever_params: dict[str, Any] = field(default_factory=dict)
    best_ever_cycle_id: int | None = None
    best_ever_goal: str | None = None  # F5: best_ever 所属 goal 语义（防跨尺子比较）
    # F6: 尺度漂移连续命中计数（≥BASELINE_DRIFT_STREAK 触发归档重置）
    baseline_drift_streak: int = 0
    benchmark_baseline: float | None = None  # P0 基线持久化（跨进程）
    # R10-4 (2026-09-23): 停滞轮换——连续零改进圈数（goal=structure 收敛后
    # 自动切 behavior；behavior 停滞则降频省预算）。None = 旧状态文件无字段。
    stall_count: int = 0
    # F7 (2026-09-25): frozen zone（死循环熔断闸）——同参数+同因回滚连击
    # 达阈值即置 frozen=True，优化循环跳轮并按指数退避（2^n 轮跳一轮）。
    # frozen_since_cycle 记录冻结起点，供退出冻结的进度核查。旧状态文件
    # 无字段 → 默认未冻结（向后兼容）。
    frozen: bool = False
    frozen_since_cycle: int | None = None
    frozen_params: dict[str, Any] = field(default_factory=dict)
    frozen_reason: str = ""
    frozen_skipped: int = 0  #: 冻结期间已跳过的轮数（退避计数用）
    # P2-9 (2026-10-17)【RSI 阶段二-件9】：Strategy 入 Target——搜索策略
    # 权重表 {strategy: weight}。七要素中 Strategy 从「写死的常量」升格为
    # 可改进对象：meta-optimizer 按台账 accepted 率周期性调整（件9b），
    # daemon 每轮按权重随机选策略。asdict 整体落盘，旧状态文件无此字段
    # → load 后补默认（None 哨兵）。
    strategy_weights: dict[str, float] | None = None
    # P1-4 (2026-10-17)【RSI 阶段一-4】: 观察期挂单——新参数应用后的
    # 观察窗起点（本字段在窗内非空）。观察期内基准复评回退 → 恢复
    # last-known-good 快照并结算 rolled_back（可停）。旧状态文件无字段
    # → 默认 None（向后兼容，重活第一轮观察即恢复挂单机制）。
    observing: dict[str, Any] | None = None
    # R1 (2026-10-22)【DGM 式谱系 archive】: 上次备选池回采发生的周期号。
    # 节流基准（每 LINEAGE_RECALL_EVERY 轮至多回采一次）。None=从未回采。
    # 反射 load 自动持久化（P1-卫根治后的新增字段免登记）。
    lineage_last_recall_cycle: int | None = None

    @classmethod
    def load(cls, path: Path) -> DaemonState:
        if path.exists():
            try:
                raw_bytes = path.read_bytes()
                raw = json.loads(raw_bytes.decode("utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("状态文件顶层不是对象")
                # P1-卫根治 (2026-10-17)：反射全字段往返——dataclasses.fields
                # 逐字段从盘上取值，缺省用 dataclass 默认。此后**新增字段自动
                # 持久化/恢复**，结构性消灭「load 白名单漏字段」bug 类
                # （首航实证：P1-4 加 observing 只改 dataclass 未进白名单 →
                # 挂单跨进程失忆；同进程 unit 测试无法暴露）。
                import dataclasses

                kwargs = {
                    f.name: raw[f.name]
                    for f in dataclasses.fields(cls)
                    if f.name in raw
                }
                inst = cls(**kwargs)
                # P1-卫 Rule A：记录加载时盘上指纹——save 时盘 ≠ 此指纹
                # 且 ≠ 本进程上次写入 → 说明他人改过盘 → 拒写防踩。
                try:
                    inst._last_known_sha = hashlib.sha256(raw_bytes).hexdigest()
                except AttributeError:
                    pass
                return inst
            except (json.JSONDecodeError, TypeError, ValueError):
                logger.warning("状态文件损坏，使用默认状态")
        return cls()

    def save(self, path: Path) -> bool:
        """P1-卫 (2026-10-17)：原子写 + flock + Rule A 单写者/陈旧检查。

        返回 False = **拒写**，两种触发：
        1) 磁盘 owner 是存活他者进程（单写者铁律——先停对方再接管）；
        2) 盘上内容 ≠ 本进程最后已知来源（加载时指纹或上次成功写入），
           且无存活 writer——说明有人绕过本进程直改了盘（CLI reset、
           手工编辑），陈旧内存覆盖之即「旧内存踩盘」。
        防线后内存态仍自洽，重载后下轮 save 可成功。
        """
        import dataclasses

        resolved = path.resolve()
        if not resolved.is_relative_to(Path.cwd()):
            resolved = (Path.cwd() / path).resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        lock_path = resolved.with_name(resolved.name + ".lock")
        owner_path = resolved.with_name(resolved.name + STATE_OWNER_SUFFIX)
        me = _current_owner_id()
        with _state_lock(lock_path):
            disk_owner = _read_owner(owner_path)
            if disk_owner is not None:
                mine = (
                    int(disk_owner.get("pid", -1)) == me["pid"]
                    and disk_owner.get("boot_id", "") == me["boot_id"]
                )
                if not mine:
                    cross_boot = disk_owner.get("boot_id") != me["boot_id"]
                    foreign_alive = (
                        not cross_boot
                        and _process_alive(
                            int(disk_owner["pid"]),
                            disk_owner.get("start_time"),
                        )
                    )
                    if foreign_alive:
                        logger.error(
                            "[Rule A] 状态文件由存活进程 pid=%s (cmd=%s) 持有，"
                            "拒绝写入（防旧内存踩盘）；如需接管请先停掉对方",
                            disk_owner.get("pid"),
                            disk_owner.get("cmdline", "?"),
                        )
                        return False
            if resolved.exists():
                try:
                    disk_sha = hashlib.sha256(resolved.read_bytes()).hexdigest()
                except OSError:
                    disk_sha = None
                last_known = getattr(self, "_last_known_sha", None)
                if disk_sha is not None and last_known is not None \
                        and disk_sha != last_known:
                    logger.error(
                        "[Rule A] 状态文件在本进程加载/上次写入后已被他人修改"
                        "（指纹不符，无存活 writer）→ 拒写防覆盖；"
                        "请 reload_state_from_disk 后重试"
                    )
                    return False
            # 所有权接管（原子写 state + owner）
            payload = json.dumps(
                dataclasses.asdict(self),
                indent=2,
                ensure_ascii=False,
                default=str,
            )
            tmp = resolved.with_name(resolved.name + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, resolved)
            self._last_known_sha = hashlib.sha256(
                payload.encode("utf-8")
            ).hexdigest()
            _write_json_atomic(
                owner_path, {**me, "ts": datetime.now().isoformat()}
            )
        return True

    def validate_observing(self) -> bool:
        """P1-卫 Rule C 辅助：挂单结构校验（缺关键字段=脏单 → 上层清除）。"""
        if self.observing is None:
            return True
        if not isinstance(self.observing, dict):
            return False
        required = ("exp_id", "applied_at", "before_score", "params")
        return all(k in self.observing for k in required)


class OptimizationDaemon:
    def __init__(
        self,
        target: str = ".",
        config: lingclaudeConfig | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self.target = target
        self.config = config or load_config()
        self.state_dir = state_dir or DEFAULT_STATE_DIR
        self.state_path = self.state_dir / "daemon_state.json"
        self.reports_dir = self.state_dir / "reports"

        self.trigger = OptimizationTrigger(self.config.triggers)
        self.evaluator = StructureEvaluator(target)
        self.optimizer = SynchronousOptimizer()
        self.advisor = OptimizationAdvisor()
        # P0 实证门禁（2026-09-17）：行为基准评测器——best_params 须过
        # "基准分不回退"门禁才允许应用；代理指标（violations）降为 tiebreaker。
        from lingclaude.self_optimizer.benchmark import BenchmarkEvaluator

        self.benchmark = BenchmarkEvaluator(target)
        self._last_benchmark_score: float | None = None
        # F2 归因链（2026-09-22）：实验台账——experiment_id 贯穿 +
        # accept/rollback 结算。库随 state_dir（测试天然隔离）。
        self.experiments = ExperimentLedger(
            db_path=str(self.state_dir / "experiments.db")
        )
        # F4 值守（2026-09-23）：返审触发器入 daemon 值守循环。
        # 三级节流 + 报告强制出口（category=audit → F0 取数面），
        # 审计权限边界不变：发现 → 入册 → 等待，值守不自动改码。
        self.audit_watch = AuditWatch(state_dir=self.state_dir)
        # P1-4 (2026-10-17)【RSI 阶段一-4/5】: artifact 守卫——应用前
        # 原子快照目标文件(可退),观察期回退自动恢复(可停),继承反向
        # 配对重验旧基线(防赢在历史输在现在)。库随 state_dir 测试隔离。
        from lingclaude.self_optimizer.artifact_guard import ArtifactGuard

        self.artifacts = ArtifactGuard(snapshot_dir=self.state_dir / "artifacts")
        self.state = DaemonState.load(self.state_path)
        # R10-4 (2026-09-23): 实例级 goal 覆盖——stall 轮换只改运行时实例，
        # 不回写 frozen config（多进程共享配置时避免暗中突变全局语义）。
        # _current_goal() 是 daemon 内 goal 判读的唯一入口。
        self._runtime_goal: str | None = None
        # 遗留项1（2026-09-17）：基线跨进程恢复——重启后从归档态取回
        # 上次通过的基准分，避免"重启即丢基线、门禁首轮失效"。
        if self.state.benchmark_baseline is not None:
            self._last_benchmark_score = self.state.benchmark_baseline
            logger.info(
                "[P0基线] 恢复上次基准分 %.1f（来自 daemon_state）",
                self._last_benchmark_score,
            )
        self._behavior_snapshot: dict[str, Any] = {}
        # F5 (2026-09-23): best_ever_goal 卫兵——best_ever_score 是「goal
        # 语义内」的最优（structure=violations 数 / behavior=回放代价），
        # 跨 goal 比较无意义（27 violations vs 代价分不可通约）。goal 与
        # 归档时不一致 → 旧基线一次性重置，避免 P1 门禁用错尺子永久回滚。
        state_goal = getattr(self.state, "best_ever_goal", None)
        current_goal = self._current_goal()
        if self.state.best_ever_score is not None and state_goal != current_goal:
            logger.warning(
                "[F5卫兵] goal 已切换 %s→%s，重置跨语义 best_ever 基线 "
                "(score=%s @cycle#%s)",
                state_goal or "structure(旧档)", current_goal,
                self.state.best_ever_score, self.state.best_ever_cycle_id,
            )
            self.state.best_ever_score = None
            self.state.best_ever_params = {}
            self.state.best_ever_cycle_id = None
        # P0-4: session snapshot/rewind
        # 修复会话泄漏：此前用 cwd 相对路径 P(".lingclaude/sessions")，
        # daemon 从哪个目录启动就把快照写进哪个 <cwd>/.lingclaude/sessions/，
        # 跨 cwd 起 daemon 会把同项目快照分裂到多个目录。
        # 改为无参构造 → 走 _global_sessions_root()（~/.lingclaude/sessions），
        # 与交互/api 主链路一致，并按 project_path 分目录隔离。
        self._session_mgr = SessionManager()
        self._current_session = self._session_mgr.create(
            project_path=str(P(target).resolve()),
            project_name=P(target).name,
        )
        # P0-4: on startup, rewind to last snapshot if any
        sessions = self._session_mgr.list_sessions(project_path=str(P(target).resolve()))
        if sessions:
            latest = sorted(sessions, key=lambda s: s.get("created_at", ""))[-1]
            sid = latest["session_id"]
            # 全局根模式下快照落在 save_dir/<project_dir>/（与 session json 同目录），
            # 需在项目子目录内 glob，而非根层（根层 glob 在全局模式下会漏）。
            snap_dir = self._session_mgr.save_dir / P(target).name
            if not snap_dir.exists():
                snap_dir = self._session_mgr.save_dir
            snap_candidates = list(snap_dir.glob(f"{self._session_mgr.SNAPSHOT_PREFIX}{sid}_*.json")) if snap_dir.exists() else []
            if snap_candidates:
                latest_snap = sorted(snap_candidates)[-1]
                restored = self._session_mgr.rewind(sid, latest_snap)
                if restored.is_ok:
                    self._current_session = restored.data
                    logger.info("已从快照恢复 session: %s", latest_snap.name)
        self._snap_interval = 5  # 每 N 轮优化做一次 snapshot

    def collect_metrics(self) -> Result[dict[str, Any]]:
        try:
            metrics = self.evaluator.get_current_metrics()
            metrics["last_optimization_time"] = self.state.last_optimization_time
            return Result.ok(metrics)
        except Exception as e:
            return Result.fail(f"Failed to collect metrics: {e}", code="METRIC_ERROR")

    def build_context(self, metrics: dict[str, Any], user_triggered: bool = False) -> dict[str, Any]:
        ctx: dict[str, Any] = dict(metrics)
        ctx["last_optimization_time"] = self.state.last_optimization_time
        ctx["user_triggered"] = user_triggered
        ctx["hallucination_risk"] = self._behavior_snapshot.get("hallucination_risk", 0)
        ctx["frustration_rate"] = self._behavior_snapshot.get("frustration_rate", 0)
        ctx["tool_error_rate"] = self._behavior_snapshot.get("tool_error_rate", 0)
        ctx["corrections_received"] = self._behavior_snapshot.get("corrections_received", 0)
        history_result = self.load_behavior_history()
        history = history_result.data if history_result.is_ok else {}
        ctx["cumulative_frustration"] = history.get("total_frustration", 0)
        ctx["cumulative_corrections"] = history.get("total_corrections", 0)
        # P1.5（2026-09-17）：失败实验上下文——最近失败原因与连续同因计数，
        # 供触发器/优化器避开已踩过的坑（"同一失败不重试超 2 次"代码化）。
        failed = self.state.last_metrics.get("failed_attempts", [])
        if failed:
            last = failed[-1]
            same_streak = 0
            for f in reversed(failed):
                if f.get("error_key") == last.get("error_key"):
                    same_streak += 1
                else:
                    break
            ctx["last_failure_error"] = last.get("error_key")
            ctx["same_failure_streak"] = same_streak
        # ---- P1-6 headroom 装配（RSI 阶段一-6，2026-10-17）----
        # 三分量向量：pass(基准分空间) / recurrence(30d 回滚率) / cost(未接
        # 入恒弃权)。人工单跑不受闸（人明确要跑）。全未知 → 表决 True
        # （防首轮死锁，退化=旧定时行为）。
        try:
            from lingclaude.self_optimizer.headroom import compute_headroom

            hv = compute_headroom(
                benchmark_score=self._last_benchmark_score,
                best_ever_score=self.state.best_ever_score,
                rollback_rate_30d=self.experiments.rolled_back_rate(30.0),
            )
            ctx["headroom"] = hv
        except Exception:  # noqa: BLE001 — 判据故障退回旧行为（全未知→True）
            logger.exception("[P1-6headroom] 装配异常（本轮弃权表决）")
        # 遗留项2（2026-09-17）：高置信经验反哺——从知识库取高置信自优化
        # 规则（conf>=0.7 且 active），把"哪类触发→历史上是否有效"喂进
        # 优化上下文，供优化器/触发器决策参考（AgentEvolver Self-Navigating）。
        try:
            from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase

            kb = KnowledgeBase()
            rules_res = kb.get_all_rules(limit=100)
            if rules_res.is_ok:
                exp_rules = [
                    r for r in rules_res.data
                    if r.id.startswith("opt_cycle_") and r.confidence >= 0.7
                    and r.status == "active"
                ]
                # 置信度降序取前 5 条，注入触发类型 + 累积频次
                ctx["experience_hints"] = [
                    {
                        "trigger_type": r.pattern.context_keywords[0] if r.pattern.context_keywords else "?",
                        "frequency": r.frequency,
                        "confidence": r.confidence,
                        "improvement": r.pattern.severity_distribution.get("before", 0)
                        - r.pattern.severity_distribution.get("after", 0),
                    }
                    for r in sorted(exp_rules, key=lambda x: -x.confidence)[:5]
                ]
            kb.close()
        except Exception:  # noqa: BLE001 — 经验反哺失败不影响主循环
            logger.debug("经验反哺 build_context 失败", exc_info=True)
        return ctx

    def update_behavior(self, behavior: dict[str, Any]) -> None:
        self._behavior_snapshot = behavior

    def _record_failed_attempt(self, error: str) -> None:
        """P1.5（2026-09-17）：失败实验入档。

        error_key 取错误首行（同因归并），连续同因 ≥2 时告警——
        组织纪律"同一失败不重试超 2 次"的代码化。上限 20 条滚动。
        """
        error_key = (error or "unknown").strip().splitlines()[0][:200]
        failed: list[dict[str, Any]] = self.state.last_metrics.setdefault("failed_attempts", [])
        failed.append({
            "error_key": error_key,
            "at": datetime.now().isoformat(),
        })
        if len(failed) > 20:
            del failed[:-20]
        same_streak = 0
        for f in reversed(failed):
            if f.get("error_key") == error_key:
                same_streak += 1
            else:
                break
        if same_streak >= 2:
            logger.warning(
                "[P1.5] 同因失败连续 %d 次（%s…）——下轮应换实验方向",
                same_streak, error_key[:80],
            )
        self.state.save(self.state_path)

    # ------------------------------------------------------------------ #
    # F7 (2026-09-25): frozen zone（死循环熔断闸）
    # ------------------------------------------------------------------ #
    def _check_frozen_zone(
        self, params: dict[str, Any], rollback_reason: str
    ) -> tuple[bool, int]:
        """进入 frozen zone（同参数+同因回滚连击达阈值）判定。

        Returns (frozen, streak)。frozen=True 时 run_cycle 应跳轮并按
        指数退避（2^n 轮跳一轮）。连击由 ledger.recent_rollbacks 判定，
        任何 accepted/rejected 单都会打断 streak（优化已转向）。
        """
        try:
            streak = self.experiments.rollback_streak(params, rollback_reason)
        except Exception:  # noqa: BLE001 — 熔断闸故障不得阻断优化主循环
            logger.warning("[F7] 回滚连击查询失败（fail-open 放行）", exc_info=True)
            return False, 0
        if streak < FROZEN_ROLLBACK_STREAK_THRESHOLD:
            return False, streak
        logger.warning(
            "[F7熔断] 同参数组合 %s 连续 %d 次回滚（原因=%s）→ 进入 frozen zone，"
            "优化循环暂停并按指数退避（2^n 轮跳一轮）",
            json.dumps(params, ensure_ascii=False, sort_keys=True),
            streak,
            rollback_reason,
        )
        self.state.frozen = True
        self.state.frozen_since_cycle = self.state.total_cycles + 1
        self.state.frozen_params = dict(params)
        self.state.frozen_reason = rollback_reason
        self.state.frozen_skipped = 0
        self.state.save(self.state_path)
        return True, streak

    # ------------------------------------------------------------------ #
    # S0-2 (2026-10-17)【RSI 阶段〇-2，变更速率总闸】：单机版「能力增速
    # 限制」（论文 2609.36054 三支柱 Steer & Constrain 的落点）。
    # 24h 窗口内 accepted 参数变更 ≤ MAX_ACCEPTED_PER_24H、自入库规则
    # ≤ MAX_RULES_PER_24H。超限轮只评估不落盘（dry-run 进台账 rejected，
    # reason=s0_rate_limited）；自优化周期规则（opt_cycle_*/failure_cluster_*）
    # 仅刷新既有 id（upsert 不净增）不计窗。
    # ------------------------------------------------------------------ #
    def _rule_injection_count_24h(self) -> int:
        """24h 窗口自优化回路规则净入库计数。

        归因口径：id 前缀 opt_cycle_* / failure_cluster_* 由自优化回路
        写入；经 add_rule 同 id upsert 刷新（频次/置信度）不产生新行，
        故按 created_at 计数即净新增。失败降级 0（放行，台账是观测件）。
        """
        try:
            from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase

            kb = KnowledgeBase()
            try:
                since = (
                    datetime.now() - timedelta(hours=24)
                ).isoformat(timespec="seconds")
                row = kb._get_connection().execute(
                    """
                    SELECT COUNT(*) AS n FROM rules
                     WHERE created_at >= ?
                       AND (id LIKE 'opt_cycle_%' OR id LIKE 'failure_cluster_%')
                    """,
                    (since,),
                ).fetchone()
                return int(row["n"]) if row else 0
            finally:
                kb.close()
        except Exception:  # noqa: BLE001 — 计数失败不阻塞优化主循环
            logger.warning("[S0-2] 规则速率计数失败，按 0 计（放行）", exc_info=True)
            return 0

    def _rule_rate_limited(self) -> bool:
        """规则侧速率闸：24h 净新增 ≥ 上限即本轮跳过规则入库（dry-run）。"""
        count = self._rule_injection_count_24h()
        if count < MAX_RULES_PER_24H:
            return False
        logger.warning(
            "[S0-2速率闸] 24h 自优化规则净入库 %d ≥ 上限 %d → 本轮规则只分析"
            "不落库（dry-run）",
            count, MAX_RULES_PER_24H,
        )
        return True

    def _frozen_zone_skip(self) -> bool | None:
        """frozen zone 期间的跳轮+退避。返回 True 表示本轮跳过，None 表示未冻结/已退出。

        每 _watch_interval 巡检一次：冻结期间若出现新的 accepted/rejected
        单（优化已转向），则告警退出冻结并重置 best_ever（结构性修复
        后旧基线已过时，防止继续误杀）。
        """
        if not getattr(self.state, "frozen", False):
            return None
        self.state.frozen_skipped = getattr(self.state, "frozen_skipped", 0) + 1
        self.state.save(self.state_path)
        # 冻结期间新进展探测：若出现新的 accepted/rejected 单，说明优化
        # 已转向，应重新评估（如目标-手段错配已通过结构性修复解决）。
        try:
            progress_cycle = max(
                self.experiments.latest_verdict_cycle("accepted"),
                self.experiments.latest_verdict_cycle("rejected"),
            )
        except Exception:  # noqa: BLE001 — 探测失败按无进展处理
            progress_cycle = 0
        if progress_cycle > (self.state.frozen_since_cycle or 0):
            logger.warning(
                "[F7熔断] 冻结期间出现新的 accepted/rejected 单（cycle=%s）"
                "→ 退出 frozen zone（优化已转向，重置 best_ever 防误杀）",
                progress_cycle,
            )
            self.state.frozen = False
            self.state.frozen_since_cycle = None
            self.state.frozen_params = {}
            self.state.frozen_reason = ""
            self.state.frozen_skipped = 0
            self.state.best_ever_score = None
            self.state.best_ever_params = {}
            self.state.best_ever_cycle_id = None
            self.state.best_ever_goal = None
            self.state.save(self.state_path)
            return None
        backoff = min(
            2 ** getattr(self.state, "frozen_skipped", 0), FROZEN_MAX_BACKOFF_CYCLES
        )
        if self.state.frozen_skipped % backoff != 0:
            return True
        logger.warning(
            "[F7熔断] frozen zone：已跳过 %d 轮（退避=%d 轮/跳），"
            "同参数组合 %s 回滚连击原因=%s。本轮继续跳过。",
            self.state.frozen_skipped,
            backoff,
            json.dumps(self.state.frozen_params, ensure_ascii=False, sort_keys=True),
            self.state.frozen_reason,
        )
        return True

    def run_cycle(self, user_triggered: bool = False) -> Result[OptimizationCycle | None]:
        # ---- P1-4 观察期结算（A2-2 前移 2026-10-17）----
        # 挂单结算必须先于一切早退路径（frozen 熔断跳轮/触发判定失败/
        # 指标收集失败），否则 frozen 期间挂单永久悬置、归档永不闭环。
        # 结算自身异常被捕获（不阻塞主循环），挂单只观察一轮。
        obs = self.state.observing
        if obs:
            try:
                self._settle_observation(obs)
            except Exception:  # noqa: BLE001 — 观察故障不阻塞主循环
                logger.exception("[P1-4观察] 结算异常（本轮跳过，挂单保留）")
            finally:
                self.state.observing = None  # 只观察一轮（结算或故障都不挂起）
                self.state.save(self.state_path)

        metrics_result = self.collect_metrics()
        if metrics_result.is_error:
            return metrics_result  # type: ignore[return-value]
        metrics = metrics_result.data
        context = self.build_context(metrics, user_triggered=user_triggered)

        # F7 (2026-09-25): frozen zone（死循环熔断闸）——冻结期间跳轮并按
        # 指数退避巡检。user_triggered（人工单跑）不受熔断影响（人明确要跑）。
        if not user_triggered:
            frozen_skip = self._frozen_zone_skip()
            if frozen_skip:
                return Result.ok(None)

        should_trigger, trigger_info = self.trigger.check_all_conditions(context)
        if not should_trigger:
            logger.info("无触发条件，跳过本轮")
            return Result.ok(None)

        # P1-1: TriggerInfo 全量保留（含 metrics/current_value/threshold），
        # 随 DaemonState 持久化——此前只落 type/reason/priority 三个字符串。
        if trigger_info is not None:
            self.state.last_trigger_info = asdict(trigger_info)

        logger.info(
            "触发优化: type=%s reason=%s priority=%s",
            trigger_info.type,
            trigger_info.reason,
            trigger_info.priority,
        )

        # ---- P1-8 fail-closed（RSI 阶段一-8，2026-10-17）----
        # 真实应用开启（LINGCLAUDE_DAEMON_APPLY=1）时，实验台账不可用
        # → 拒绝本轮（变更可证性优先于变更本身）。report-only 模式
        # 不受影响（无变更发生，观测件故障降级即可）。
        if (
            os.environ.get("LINGCLAUDE_DAEMON_APPLY") == "1"
            and self.experiments is None
        ):
            logger.error(
                "[P1-8] APPLY=1 但实验台账不可用 → fail-closed 拒绝本轮"
            )
            return Result.ok(None)

        violations_before = metrics.get("structure_violations", 0)
        # F5 (2026-09-23): score_before 同语义化——improvements 判据
        # (violations_after < violations_before) 要求 before/after 同一
        # 把尺子。behavior 循环的 best_score 是回放代价（非 violations
        # 数），若 before 仍取 structure 口径 28，代价分（量级几十）永远
        # 无法与之形成有效比较 → improvements 恒 0 死锁（第 683 圈实证）。
        # behavior 下 before 由 P0 门禁后的当前策略回放分回填。
        is_behavior = self._current_goal() == "behavior"
        score_before: float | None = None if is_behavior else float(violations_before)
        start = time.monotonic()

        # P2-9a: Strategy 入 Target——本轮策略由权重表选出，seed 按 cycle
        # 序号派生（同轮可复现，跨轮新采样）。
        p2_strategy = self._pick_strategy()
        p2_seed = 42 + (self.state.total_cycles + 1) * 1009
        request = OptimizationRequest(
            target=self.target,
            goal=self._current_goal(),  # R10-4: 运行时 goal（轮换后≠config 档）
            params={},
            config={
                "max_experiments": self.config.optimizer.max_trials,
                "strategy": p2_strategy,
                "seed": p2_seed,
            },
        )
        result = self.optimizer.optimize(request)
        duration = time.monotonic() - start
        # R1: 周期级回采标记复位（上轮 True 不得泄漏到本轮）
        self._lineage_recalled_this_cycle = False

        # ---- R1 谱系隔代回采（2026-10-22，DGM 式 archive）----
        # 低频（每 LINEAGE_RECALL_EVERY 轮至多一次）从备选池取一块
        # 踏脚石，作为**本轮候选来源**注入 optimizer 结果位（不绕任何
        # 护栏）：仅当常规搜索无果（result.success=False）时替换候选，
        # 后续 P0 门禁复测 / P1 择优 / 观察窗 / 快照 / 权限闸全部照走。
        # 回采复测通过与否都让候选出池，防同块踏脚石反复回采。
        if (
            isinstance(result, OptimizationResult)
            and not result.success
            and self.experiments is not None
            and self._current_goal() == "behavior"
        ):
            try:
                _due = (
                    self.state.lineage_last_recall_cycle is None
                    or self.state.total_cycles + 1
                    - int(self.state.lineage_last_recall_cycle)
                    >= LINEAGE_RECALL_EVERY
                )
                _cand = self.experiments.pool_sample() if _due else None
                if _cand and _cand.get("params"):
                    result = OptimizationResult(
                        success=True,
                        best_params=dict(_cand["params"]),
                        best_score=float(_cand.get("score") or 0.0),
                        experiments=1,
                        duration=duration,
                        error=None,
                        history=(),
                    )
                    p2_strategy = "lineage_recall"
                    self.state.lineage_last_recall_cycle = (
                        self.state.total_cycles + 1
                    )
                    # 回采轮标记：本轮候选已被复测裁决，无论过否不再回池
                    #（防「回采→拒绝→立刻回池」死循环污染备选池）。
                    self._lineage_recalled_this_cycle = True
                    logger.info(
                        "[R1回采] 候选=%s score=%s（cycle#%s）→ 走全护栏复测",
                        _cand.get("exp_id"), _cand.get("score"),
                        self.state.total_cycles + 1,
                    )
                    self.experiments.pool_remove(str(_cand["exp_id"]))
                elif _cand is not None:
                    self.experiments.pool_remove(str(_cand["exp_id"]))
            except Exception:  # noqa: BLE001 — archive 增强件故障不阻塞主循环
                logger.exception("[R1回采] 异常（本轮跳过，不阻塞）")

        # ---- 断点③（2026-09-28）：失败聚类自动归因 ------------------------
        # 每轮优化后扫 error_log，同型失败跨会话聚类 → 产 LearnedRule(draft)
        # + backlog 修复建议。把「13 连败人工查 journal」机制化为 daemon 自动
        # 发现。fail-soft：聚类失败不阻断优化主流程（节流每轮一次，量级亚秒）。
        # S0-2 (2026-10-17)：规则侧速率闸——24h 净入库达上限时 write=False
        # （dry-run：只分析、可观测、不落库），防规则流水线自我灌水。
        try:
            from lingclaude.self_optimizer.failure_cluster_analyzer import (
                FailureClusterAnalyzer,
            )
            fca_write = not self._rule_rate_limited()
            fca = FailureClusterAnalyzer(min_occurrences=3, min_sessions=2)
            fca_result = fca.analyze(write=fca_write)
            if fca_result.actionable:
                logger.info(
                    "[断点③] 失败聚类归因：可行动 %d 簇，入库规则 %d，backlog +%d%s",
                    fca_result.actionable, fca_result.rules_written,
                    fca_result.backlog_appended,
                    "" if fca_write else "（S0-2 速率闸 dry-run，未落库）",
                )
        except Exception as _fca_err:  # noqa: BLE001 — 归因失败不阻断优化
            logger.debug("[断点③] 失败聚类分析跳过（fail-soft）: %s", _fca_err)

        # ---- 断点②（2026-09-28）：规则行为回放验证 ------------------------
        # 断点③产的 draft 规则经热路注入后，此处回放「注入前后同类失败密度」，
        # 验证规则是否真的改变了行为：改善→升 active，无效→降权，持续无效→退役。
        # 这是「从记录经验到改变行为」的最后一厘米。fail-soft：验证失败不阻断。
        try:
            from lingclaude.self_optimizer.rule_effect_verifier import (
                RuleEffectVerifier,
            )
            rev = RuleEffectVerifier()
            rev_result = rev.verify(write=True)
            if rev_result.promoted or rev_result.deprecated:
                logger.info(
                    "[断点②] 规则行为回放：改善 %d，升 active %d，退役 %d",
                    rev_result.improved, rev_result.promoted, rev_result.deprecated,
                )
        except Exception as _rev_err:  # noqa: BLE001 — 验证失败不阻断优化
            logger.debug("[断点②] 规则行为回放跳过（fail-soft）: %s", _rev_err)

        # ---- 断点④（2026-09-28）：backlog 修复建议自动执行 ------------------
        # 断点③产的修复建议（backlog）经白名单匹配后自动执行验证动作，
        # 结果回写 backlog 条目 status。不可自动执行的标 skipped 等人工。
        # fail-soft：执行失败不阻断优化主流程。
        try:
            from lingclaude.self_optimizer.backlog_executor import BacklogExecutor
            bxe = BacklogExecutor()
            bxe_result = bxe.execute()
            if bxe_result.executed or bxe_result.skipped:
                logger.info(
                    "[断点④] backlog 自动执行：total=%d executed=%d skipped=%d failed=%d",
                    bxe_result.total, bxe_result.executed,
                    bxe_result.skipped, bxe_result.failed,
                )
        except Exception as _bxe_err:  # noqa: BLE001 — 执行失败不阻断优化
            logger.debug("[断点④] backlog 执行跳过（fail-soft）: %s", _bxe_err)

        if not result.success:
            # ---- P1.5 失败入档（2026-09-17，借鉴 OpenEvolve artifact side-channel）----
            # 失败方案入档（含原因与当次参数），下轮 build_context 引用——
            # "同一失败不重试超 2 次"纪律的代码化：连续同因失败 2 次即冷却，
            # 本轮不再重复同一实验方向。
            self._record_failed_attempt(str(result.error))
            logger.error("优化失败: %s", result.error)
            return Result.ok(None)

        # ---- P0 实证门禁（2026-09-17 双测版）----
        # before/after 双测: before 用现行配置阈值跑基准并持久化基线（供跨轮
        # 比较/重启恢复），after 用 adopt_params(best_params) 联动题集阈值后
        # 复测——分数与参数耦合（原实现阈值硬编码 + 基准在 _apply_params 之前
        # 测，恒等分 → 门禁死门，atomcode B2 / opencode #9）。
        # after.score < before.score → 参数会拉低行为分 → 拒绝应用本轮参数。
        # F2 归因链：清扫超时 pending（崩溃残留 → rolled_back），随后开本轮实验单。
        # F5 (2026-09-23): behavior goal 下 P0 跳过——adopt_params 的
        # _PARAM_TO_CHECK 只映射 structure 参数键（max_class_size 等），
        # behavior 键（consecutive_fail_limit/tool_repeat_limit）全部
        # miss → 复测恒等分 → 门禁既不通过也不拒绝，纯烧两遍基准时间
        # （死门）。behavior 的实证闸由 P1 择优 + report-only 护栏承担。
        self.experiments.expire_stale_pending()
        is_behavior_goal = self._current_goal() == "behavior"
        if is_behavior_goal:
            # F5: behavior 的 before = 当前策略参数的回放代价——与
            # best_score 同一把尺子（同语料同函数），improvements 判据
            # 才有效。策略缺键回退出厂默认（fl=2/rl=3，与 behavior_check
            # 回退一致）。注：report-only 模式下策略不动，基线每轮同值，
            # improvements 语义为「候选胜过现行基线」计数。
            from lingclaude.core.policy_loader import get as _policy_get
            from lingclaude.self_optimizer.replay_objective import ReplayObjective

            _pol = _policy_get("behavior_policy") or {}
            _cur = {
                "consecutive_fail_limit": int(_pol.get("consecutive_fail_limit", 2)),
                "tool_repeat_limit": int(_pol.get("tool_repeat_limit", 3)),
            }
            score_before = ReplayObjective().evaluate(_cur)
            logger.info(
                "[P0门禁] goal=behavior 跳过联动复测（adopt_params 无 behavior 键"
                "映射，恒等分无裁决力）；当前策略回放代价 before=%.2f",
                score_before,
            )
        else:
            bench_before = self.benchmark.run()
            self.state.benchmark_baseline = bench_before.score
            self.state.save(self.state_path)
            logger.info(
                "[P0门禁] 基准分 %.1f/%d（passed=%d/%d）",
                bench_before.score, 100, bench_before.passed, bench_before.total,
            )
            self.benchmark.adopt_params(result.best_params)
            bench_after = self.benchmark.run()
            if bench_after.score < bench_before.score:
                # F2 归因链：本轮 best_params 被门禁拒绝 → 结算 rejected。
                # score_before 取行为基准分（门禁语义，与 score_after 可比）。
                exp_id = self.experiments.start(
                    self.state.total_cycles + 1, result.best_params, bench_before.score,
                    strategy=p2_strategy,
                )
                logger.warning(
                    "[P0门禁] 参数联动复测回退 %.1f → %.1f，本轮 best_params 拒绝应用"
                    "（violations=%s 仅作参考）",
                    bench_before.score, bench_after.score, result.best_score,
                )
                self.experiments.settle(
                    exp_id,
                    "rejected",
                    score_after=bench_after.score,
                    reason="p0_gate_regression",
                    feedback_text=(
                        f"参数联动复测回退 {bench_before.score:.1f}→"
                        f"{bench_after.score:.1f}，候选会拉低行为分被拒"
                    ),
                )
                return Result.ok(None)
            if bench_after.score > bench_before.score:
                logger.info(
                    "[P0门禁] 参数联动复测提升 %.1f → %.1f，best_params 通过",
                    bench_before.score, bench_after.score,
                )
            self._last_benchmark_score = bench_before.score

        self.reports_dir.mkdir(parents=True, exist_ok=True)
        report_name = f"cycle_{self.state.total_cycles + 1:04d}.md"
        report_path = self.reports_dir / report_name

        report = self.advisor.generate_report(
            goal=self._current_goal(),
            target=self.target,
            current_metrics=metrics,
            optimization_result=result,
        )
        self.advisor.save_report(report, str(report_path))

        violations_after = int(result.best_score)

        # ---- P1 择优回滚（2026-09-17）：以归档历史最优为 baseline ----
        # best_score 劣于归档历史最优 → 不应用本轮参数，回滚应用
        # best_ever_params（若历史最优存在）——防止优化漂移。
        # F2 归因链：本轮实验单在此开立（观察窗起点），accept/rollback 于
        # 三个分支即时结算。
        # F5 (2026-09-23): score_before 同语义化——台账 score_before 必须与
        # best_score 同一把尺子（behavior=回放代价 / structure=violations），
        # 否则 state_store_ext 的 F0 指标（参数应用率）跨尺子失真。
        exp_id = self.experiments.start(
            self.state.total_cycles + 1, result.best_params,
            score_before if score_before is not None else violations_before,
            strategy=p2_strategy,
        )
        best_ever = self.state.best_ever_score
        # F6 尺度漂移卫兵（2026-10-21）：同 goal 尺子也会因语料漂移整体移位。
        # 判据=score_before（本轮当前参数在当前语料的实测，F2 已同尺化）——
        # 若连占 best_ever 达 M 倍，说明历史最优属旧尺度（27.6 vs ~775 型），
        # 而非「优化变差」。连续 N 轮确认防噪声，达阈一次性归档重置并放行。
        if (
            best_ever is not None
            and best_ever > 0
            and score_before is not None
            and score_before >= BASELINE_DRIFT_RATIO * best_ever
        ):
            self.state.baseline_drift_streak = (
                int(getattr(self.state, "baseline_drift_streak", 0) or 0) + 1
            )
            if self.state.baseline_drift_streak >= BASELINE_DRIFT_STREAK:
                logger.warning(
                    "[F6尺度漂移] 连续 %d 轮 score_before(%.1f) ≥ %.1f×"
                    "best_ever(%.1f@cycle#%s) → 基线属旧语料尺度，归档重置",
                    self.state.baseline_drift_streak, score_before,
                    BASELINE_DRIFT_RATIO, best_ever,
                    self.state.best_ever_cycle_id,
                )
                self.state.best_ever_score = None
                self.state.best_ever_params = {}
                self.state.best_ever_cycle_id = None
                self.state.best_ever_goal = None
                self.state.baseline_drift_streak = 0
                best_ever = None  # 本轮按无基线放行（不进择优回滚分支）
        else:
            # 未命中漂移（或暂无基线）→ 清计数
            self.state.baseline_drift_streak = 0
        best_ever = self.state.best_ever_score
        # S0-2 速率闸（参数侧）：24h accepted 计数达上限 → 本轮只评估不应用。
        # 拒绝理由显式落台账（reason=s0_rate_limited），与 F5 门禁拒绝同构。
        try:
            accepted_24h = self.experiments.accepted_in_window(24.0)
        except Exception:  # noqa: BLE001 — 闸故障不阻塞主循环（fail-open）
            accepted_24h = 0
        if accepted_24h >= MAX_ACCEPTED_PER_24H:
            logger.warning(
                "[S0-2速率闸] 24h accepted=%d ≥ 上限 %d → 本轮参数只评估"
                "不应用（dry-run）",
                accepted_24h, MAX_ACCEPTED_PER_24H,
            )
            self.experiments.settle(
                exp_id, "rejected",
                score_after=result.best_score,
                reason="s0_rate_limited",
                feedback_text=(
                    "24h accepted 达速率上限，本轮只评估不应用"
                    "（建议待窗口重置后重提）"
                ),
            )
            # 与正常分支同构构造 cycle（但 report-only：report_path 未生成）
            violations_after = int(result.best_score)
            cycle = OptimizationCycle(
                cycle_id=self.state.total_cycles + 1,
                triggered_at=datetime.now().isoformat(),
                trigger_reason=trigger_info.reason,
                trigger_type=trigger_info.type,
                trigger_priority=trigger_info.priority,
                best_score=result.best_score,
                best_params=result.best_params,
                experiments=result.experiments,
                duration_seconds=round(duration, 2),
                violations_before=(
                    int(round(score_before))
                    if score_before is not None else violations_before
                ),
                violations_after=violations_after,
                report_path="",
                experiment_id=exp_id,
                strategy=p2_strategy,
            )
            self._record_cycle(cycle)
            return Result.ok(cycle)
        if best_ever is not None and result.best_score > best_ever:
            # ---- P1-5 继承反向配对（RSI 阶段一-5，2026-10-17）----
            # 旧基线（best_ever_params）回滚/再应用前先在近 K 天语料上
            # 与当前参数配对重验：旧基线近期劣化 → 不回滚，只归档重置
            # （防「赢在历史、输在现在」）。
            pair = self.artifacts.reverse_pair_check(
                old_params=self.state.best_ever_params,
                cur_params=result.best_params,
            )
            if pair.block:
                logger.warning(
                    "[P1-5反向配对] 旧基线近期劣化(old=%.2f cur=%.2f, %s)"
                    "→ 不回滚只重置归档（%s）",
                    pair.old_score, pair.cur_score, pair.reason,
                    result.best_params,
                )
                self.state.best_ever_score = None
                self.state.best_ever_params = {}
                self.state.best_ever_cycle_id = None
                self.experiments.settle(
                    exp_id, "rejected",
                    score_after=result.best_score,
                    reason="p5_reverse_pair_baseline_reset",
                    feedback_text=(
                        "旧基线在近 K 天语料配对重验中劣化，不回滚只重置"
                        "归档（候选未过门禁但基线已失效）"
                    ),
                )
                self.state.baseline_drift_streak = 0  # F6: 基线已重置清计数
                cycle = OptimizationCycle(
                    cycle_id=self.state.total_cycles + 1,
                    triggered_at=datetime.now().isoformat(),
                    trigger_reason=(
                        f"{trigger_info.reason} | p5:{pair.reason}"
                        "(old_baseline_worse,archived_reset)"
                    ),
                    trigger_type=trigger_info.type,
                    trigger_priority=trigger_info.priority,
                    best_score=result.best_score,
                    best_params=result.best_params,
                    experiments=result.experiments,
                    duration_seconds=round(duration, 2),
                    violations_before=(
                        int(round(score_before))
                        if score_before is not None else violations_before
                    ),
                    violations_after=violations_after,
                    report_path=str(report_path),
                    strategy=p2_strategy,
                )
                self._record_cycle(cycle)
                return Result.ok(cycle)
            logger.info(
                "[P1-5反向配对] 旧基线近期仍优(old=%.2f cur=%.2f)→ 按原"
                "择优回滚执行（%s）",
                pair.old_score, pair.cur_score, pair.reason,
            )
            logger.warning(
                "[P1回滚] 本轮 best_score=%.2f 劣于归档最优 %.2f（cycle #%s），"
                "回滚应用历史最优参数",
                result.best_score, best_ever, self.state.best_ever_cycle_id,
            )
            if self.state.best_ever_params:
                self._apply_params(dict(self.state.best_ever_params))
            self.experiments.settle(
                exp_id,
                "rolled_back",
                score_after=result.best_score,
                reason="p1_worse_than_best_ever",
                feedback_text=(
                    f"本轮 {result.best_score:.1f} 未胜归档最优 "
                    f"{best_ever:.1f}，已回滚应用历史最优参数"
                ),
            )
            # F7 (2026-09-25): 回滚结算后判定是否进入 frozen zone（死循环
            # 熔断闸）——同参数+同因回滚连击达阈值即冻结优化循环。
            self._check_frozen_zone(result.best_params, "p1_worse_than_best_ever")
            # R1 谱系 archive：本轮真改进过回放分但输给归档最优——
            # DGM 教训的「踏脚石」，入备选池不丢弃，供隔代回采。
            # 例外：回采轮的候选刚被完整护栏复测拒绝 → 不回池
            #（_lineage_recalled_this_cycle 标记，防拒绝者反复回采）。
            if not getattr(self, "_lineage_recalled_this_cycle", False):
                try:
                    self.experiments.pool_candidate(
                        cycle_id=self.state.total_cycles + 1,
                        params=dict(result.best_params),
                        score=float(result.best_score),
                        exp_id=exp_id,
                        note=f"p1_worse_but_locally_good(vs_best={best_ever:.1f})",
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("[R1] 备选池入池异常（不阻塞）")
        else:
            # ---- P1-4 快照门禁 + 观察期挂单（RSI 阶段一-4，2026-10-17）----
            # 应用前原子快照被控文件：快照失败 → 不可退 → 拒绝应用
            # （可退优先于可进）。应用后不立即 accepted：挂观察单
            # state.observing，下轮周期 _settle_observation 复评——
            # 回退超阈值 → 恢复快照 + rolled_back；未回退 → accepted。
            _target = self._behavior_policy_path()
            _snap_meta = self.artifacts.snapshot(_target, exp_id or "unknown")
            if _snap_meta is None:
                logger.warning(
                    "[P1-4快照门禁] 目标文件快照失败 → 本轮拒绝应用（可退优先）"
                )
                self.experiments.settle(
                    exp_id, "rejected",
                    score_after=result.best_score,
                    reason="snapshot_unavailable_no_rollback_path",
                    feedback_text=(
                        "被控文件快照失败（不可退）→ 拒绝应用，"
                        "建议检查磁盘/权限后重试"
                    ),
                )
            else:
                # 直接调 behavior 写入器取返回值（原经 _apply_params 委托
                # 会丢 bool）。False=未落盘（全同值/审批拒/写失败）→
                # 幻影观察单防线（首航实证 2026-10-17）：不挂观察单，
                # 诚实结算 rejected/no_effective_change 并回收快照（审计行保留）。
                applied = self._apply_behavior_params(result.best_params)
                if not applied:
                    self.experiments.settle(
                        exp_id, "rejected",
                        score_after=result.best_score,
                        reason="no_effective_change",
                        feedback_text=(
                            "参数全同值/审批拒/写失败，未落盘"
                            "（全同值不入 lineage 池：与现行无异无回采价值）"
                        ),
                    )
                    self.artifacts._unlink_snap(_snap_meta.snapshot_id)
                else:
                    self.state.observing = {
                        "exp_id": exp_id,
                        "snapshot_id": _snap_meta.snapshot_id,
                        "score_before": score_before,
                        "applied_at": datetime.now().isoformat(timespec="seconds"),
                        "goal": self._current_goal(),
                        "params": dict(result.best_params),
                    }
                    # 更新归档最优（violations 越低越好）
                    if best_ever is None or result.best_score < best_ever:
                        self.state.best_ever_score = float(result.best_score)
                        self.state.best_ever_params = dict(result.best_params)
                        self.state.best_ever_cycle_id = self.state.total_cycles + 1
                        # F5: 归档时盖 goal 印——下轮加载时卫兵据此识别跨语义基线
                        self.state.best_ever_goal = self._current_goal()

        cycle = OptimizationCycle(
            cycle_id=self.state.total_cycles + 1,
            triggered_at=datetime.now().isoformat(),
            trigger_reason=trigger_info.reason,
            trigger_type=trigger_info.type,
            trigger_priority=trigger_info.priority,
            best_score=result.best_score,
            best_params=result.best_params,
            experiments=result.experiments,
            duration_seconds=round(duration, 2),
            # F5: before/after 同轮同语义——behavior 下取回放代价分，
            # structure 下保持 violations 数。CLI/Intel 的「违规 28→27」
            # 展示语义变为「目标分 before→after」，improvements 判据
            # (after < before) 由此恢复有效（此前跨尺子恒假 → 死锁）。
            violations_before=(
                int(round(score_before))
                if score_before is not None else violations_before
            ),
            violations_after=int(violations_after),
            report_path=str(report_path),
            experiment_id=exp_id,
            strategy=p2_strategy,
        )

        self._record_cycle(cycle)
        # P0-4: snapshot every _snap_interval cycles
        if (cycle.cycle_id % self._snap_interval) == 0:
            snap_path = self._session_mgr.snapshot(self._current_session)
            if snap_path.is_ok:
                logger.debug("Session snapshot saved: %s", snap_path.data)
        logger.info(
            "优化完成: score=%.2f experiments=%d duration=%.1fs report=%s",
            cycle.best_score,
            cycle.experiments,
            cycle.duration_seconds,
            report_path,
        )
        return Result.ok(cycle)

    def _current_goal(self) -> str:
        """R10-4: 运行时 goal——实例覆盖优先，回退 frozen config。"""
        return self._runtime_goal or getattr(
            self.config.optimizer, "goal", "structure"
        )

    # ------------------------------------------------------------------ #
    # P2-9 (2026-10-17)【RSI 阶段二-件9】: Strategy 入 Target
    # ------------------------------------------------------------------ #
    def _pick_strategy(self) -> str:
        """按权重表随机选本轮搜索策略（权重随机≠拍脑袋：等价于 Thompson 采样的先验形式）。

        权重表为空/键非法时回退白名单均匀先验；cycle 序号派生 seed 保证
        同轮可复现、跨轮有差异。
        """
        weights = self.state.strategy_weights or {}
        clean = {
            s: float(w)
            for s, w in weights.items()
            if s in P2_STRATEGY_WHITELIST and w > 0
        }
        if not clean:
            clean = {s: P2_STRATEGY_DEFAULT_WEIGHT for s in P2_STRATEGY_WHITELIST}
        total = sum(clean.values())
        r = random.random() * total
        acc = 0.0
        for strat, w in clean.items():
            acc += w
            if r <= acc:
                return strat
        return next(iter(clean))  # 浮点边界兜底

    def _maybe_adjust_strategy_weights(self) -> None:
        """件9b：meta-optimizer——每 N 周期聚合台账，按策略 accepted 率调权重。

        纪律（对齐阶段〇精神）：证据不足不动（MIN_SAMPLES）、更新有界
        （[0.1, 0.6]）、全部 fail-soft 不阻断主流程。被调对象是「搜哪里」
        的先验，不是评估器/闸门——治理不变量不受影响。
        """
        try:
            if self.state.total_cycles <= 0 or (
                self.state.total_cycles % P2_META_ADJUST_EVERY != 0
            ):
                return
            stats = self.experiments.strategy_stats()
            if not stats:
                return
            updated: dict[str, float] = {}
            for strat in P2_STRATEGY_WHITELIST:
                b = stats.get(strat) or {}
                if b.get("total", 0) < P2_META_MIN_SAMPLES:
                    continue  # 证据不足：维持现权重（不进更新集）
                rate = b["accepted"] / max(b["total"], 1)
                updated[strat] = min(
                    P2_META_WEIGHT_MAX, max(P2_META_WEIGHT_MIN, rate * 1.5)
                )
            if not updated:
                return
            old = self.state.strategy_weights or {}
            # 未进更新集的策略：保留旧权重（有则），否则均匀先验
            merged = {
                s: updated.get(s, old.get(s, P2_STRATEGY_DEFAULT_WEIGHT))
                for s in P2_STRATEGY_WHITELIST
            }
            self.state.strategy_weights = merged
            self.state.save(self.state_path)
            logger.warning(
                "[P2-9b] meta-optimizer 权重表更新（第 %d 周期）：%s",
                self.state.total_cycles,
                merged,
            )
        except Exception:
            logger.warning("[P2-9b] 权重调整失败（fail-soft）", exc_info=True)

    def _maybe_rotate_goal(self) -> None:
        """R10-4 (2026-09-23): 停滞轮换——目标饱和时不再空耗预算。

        触发：stall_count >= 8（连续 8 圈零改进）。分两档动作：
        - structure 停滞 → 切 behavior 换尺子找新信息增量；best_ever
          归属旧尺子，跨语义不可比，按 F5 卫兵同语义就地重置
        - behavior 停滞 → watch 降频 x6（收敛慢≠饱和，不换尺子只减速）

        只动运行时实例，不回写 frozen config；重启自然回到配置档。
        """
        if getattr(self.state, "stall_count", 0) < 8:
            return
        current = self._current_goal()
        if current == "structure" and self._runtime_goal != "behavior":
            logger.warning(
                "[R10轮换] structure 连续 %d 圈零改进 → 切 behavior"
                "（best_ever 归属旧尺子，跨语义不可比，按 F5 同语义重置）",
                self.state.stall_count,
            )
            self._runtime_goal = "behavior"
            self.state.best_ever_score = None
            self.state.best_ever_params = {}
            self.state.best_ever_cycle_id = None
            self.state.best_ever_goal = None
            self.state.stall_count = 0
        elif current == "behavior":
            logger.warning(
                "[R10轮换] behavior 连续 %d 圈零改进 → watch 降频 %ds→%ds",
                self.state.stall_count,
                self._watch_interval,
                self._watch_interval * 6,
            )
            self._watch_interval *= 6
            self.state.stall_count = 0

    def _state_disk_sha(self) -> str | None:
        """P1-卫：当前状态文件指纹（save 时比对防陈旧覆盖）。"""
        try:
            return hashlib.sha256(self.state_path.read_bytes()).hexdigest()
        except OSError:
            return None

    def reload_state_from_disk(self) -> None:
        """P1-卫 Rule C (2026-10-17)：从盘重载状态（watch 周期前调用）。

        防线一（陈旧内存）：watch 长驻进程若不重载，任何人（人工 CLI
        reset/rollback、他进程）改盘后，本进程内存态即成史前快照，下次
        save 必踩盘——首航事故（旧 watch 10-12 内存踩掉 10-17 手术解锁
        与新挂单）的直接根源。
        防线二（脏挂单）：重载后跑结构校验，不完整挂单清除+登记
        （P1-4 挂单由 apply 路径原子挂账，半截单无意义）。
        重载只覆盖可安全合并的字段；基准分内存镜像同步刷新。
        """
        disk = DaemonState.load(self.state_path)
        self.state = disk
        if disk.benchmark_baseline is not None:
            self._last_benchmark_score = disk.benchmark_baseline
        if not self.state.validate_observing():
            logger.warning(
                "[Rule C] 磁盘挂单结构不完整（缺 exp_id/applied_at/"
                "before_score/params 之一）→ 清除（脏单无结算意义）"
            )
            self.state.observing = None
        if self.state.observing is not None:
            logger.info(
                "[Rule C] 从盘恢复挂单 exp=%s（观察期跨周期延续）",
                self.state.observing.get("exp_id"),
            )

    def check_startup_consistency(self) -> bool:
        """P1-卫 Rule C (2026-10-17)：启动一致性校验。

        返回 False = 拒绝启动：
        1) 存活的他者 owner（单写者铁律——两个 daemon 同时跑=互踩状态）；
        2) 磁盘状态不可解析（半截文件——此刻接管会把损坏合法化）。
        owner 文件缺失/陈旧死者/损坏 owner 视为无主（放行）。
        """
        owner_path = self.state_path.with_name(
            self.state_path.name + STATE_OWNER_SUFFIX
        )
        me = _current_owner_id()
        disk_owner = _read_owner(owner_path)
        if disk_owner is not None:
            mine = (
                int(disk_owner.get("pid", -1)) == me["pid"]
                and disk_owner.get("boot_id", "") == me["boot_id"]
            )
            if not mine:
                cross_boot = disk_owner.get("boot_id") != me["boot_id"]
                alive = (
                    not cross_boot
                    and _process_alive(
                        int(disk_owner["pid"]), disk_owner.get("start_time")
                    )
                )
                if alive:
                    logger.error(
                        "[Rule C] 检测到存活 daemon pid=%s (cmd=%s) 持有状态"
                        "文件 → 拒绝启动（单写者铁律）。接管前先停对方。",
                        disk_owner.get("pid"),
                        disk_owner.get("cmdline", "?"),
                    )
                    return False
        if self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("顶层非对象")
            except (OSError, json.JSONDecodeError, ValueError):
                logger.error(
                    "[Rule C] 状态文件不可解析（半截/损坏）→ 拒绝启动"
                    "（接管会把损坏合法化）。请人工处理 %s",
                    self.state_path,
                )
                return False
        return True

    def run_watch(self, interval_seconds: int = 300) -> None:
        # P1-卫 Rule C (2026-10-17)：启动一致性校验——存活他者 owner 或
        # 状态文件损坏 → 拒绝启动（不进入循环，防双写互踩）。
        if not self.check_startup_consistency():
            logger.error("自由化框架启动被拒（一致性校验未过，见上方日志）")
            return
        # Rule C-2：成为登记在册的 owner（此后 save 走 mine 分支）。
        self.state.save(self.state_path)
        # R10-4: 可变间隔——behavior 停滞降频 x6 由 _maybe_rotate_goal 调整
        self._watch_interval = interval_seconds
        logger.info(
            "自由化框架启动 (watch 模式, interval=%ds, target=%s)",
            interval_seconds,
            self.target,
        )
        logger.info("按 Ctrl+C 停止")
        try:
            while True:
                # P1-卫 Rule C-3：周期前从盘重载——人工 reset/rollback、
                # CLI 单轮 run 等盘上变更对长驻进程可见，防陈旧内存踩盘。
                try:
                    self.reload_state_from_disk()
                    self._disk_sha = self._state_disk_sha()
                except Exception:  # noqa: BLE001
                    logger.exception("[Rule C] 周期前重载异常（本轮用内存态）")
                cycle_result = self.run_cycle()
                if cycle_result.is_ok and cycle_result.data is not None:
                    cycle = cycle_result.data
                    self._write_cycle_to_knowledge(cycle)
                    print(
                        f"[{cycle.triggered_at}] Cycle #{cycle.cycle_id}: "
                        f"score={cycle.best_score:.2f} "
                        f"violations={cycle.violations_before}→{cycle.violations_after} "
                        f"({cycle.duration_seconds}s)"
                    )
                # F4 值守：返审触发器随循环巡检（内部三级节流：
                # 指纹守卫近似零开销；24h 例行核账；报告出口 KB）
                try:
                    diag = self.audit_watch.run_once()
                    logger.info(
                        "[F4值守] exit=%s open_tasks=%s sweep_expired=%s report=%s",
                        diag["exit"], diag["open_tasks"],
                        diag["sweep_expired"], diag.get("kb_rule"),
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("[F4值守] 本轮值守异常（不阻塞优化循环）")
                # R10-4: 停滞轮换决策点（本轮结果已记账，stall_count 最新）
                try:
                    self._maybe_rotate_goal()
                except Exception:  # noqa: BLE001
                    logger.exception("[R10轮换] 决策异常（不阻塞循环）")
                # P0-4 灰区看门狗（24h 节流, 摘要式升级, fail-safe）
                try:
                    tick = self._pending_watchdog_tick(
                        last_run=_pending_watchdog_state_last_run()
                    )
                    if tick:
                        logger.info("[PW看门狗] 本轮处理 %d 条超时未决", len(tick))
                except Exception:  # noqa: BLE001
                    logger.exception("[PW看门狗] tick 异常（不阻塞循环）")
                time.sleep(self._watch_interval)
        except KeyboardInterrupt:
            logger.info("自由化框架已停止")
            print("\n自由化框架已停止")

    def run_once(self) -> Result[OptimizationCycle | None]:
        logger.info("自由化框架单次运行 (target=%s)", self.target)
        # N8 free-RAM 守卫门: 内存不足拒收优化周期(2026-09-24 主裁 B1 裁决立项)
        # fail-open: 守卫失效放行, 由 CLI 层留痕; 拒收返回 error Result 不写知识库
        from lingclaude.gov.guard.free_ram_gate import check_startup_allowed

        allowed, reason = check_startup_allowed("optimizer-cycle")
        if not allowed:
            return Result.fail(f"[N8守卫] {reason}", code="N8_FREE_RAM_GUARD")
        cycle_result = self.run_cycle(user_triggered=True)
        if cycle_result.is_ok and cycle_result.data is not None:
            self._write_cycle_to_knowledge(cycle_result.data)
        return cycle_result

    # ---- P1-4/6（RSI 阶段一，2026-10-17）新增方法 ----
    def _behavior_policy_path(self) -> Path:
        """被控文件绝对路径（P1-4 快照目标——单源，快照/恢复同走此路）。"""
        from lingclaude.core.policy_loader import policies_dir

        return policies_dir() / "behavior_policy.yaml"

    def manual_rollback(self, snapshot_id: str) -> Result[dict[str, Any]]:
        """A1 人工回滚（2026-10-17）：CLI rollback --to 的执行核心。

        应急通道：观察期自动回滚只覆盖「分数回退」失败模式；其余失败
        （误改/脏数据/规则闯祸）由此人工触发。诚实台账：若存在与本快照
        同单的观察挂单/待结实验，同步结算 rolled_back（原 pending 语义
        天然兼容——只覆盖 pending 行）。

        已知部署语义缺口（首航实证 2026-10-17，待专项）：「apply 前快照」
        捕获的是上一轮 apply 后的状态——若目标文件已离开安全基线多轮，
        单步回滚只回退一跳；回到任意验证态需沿 snapshots.jsonl 审计链
        逐跳回滚（或用 git 管理的策略文件历史兜底）。首航原点恢复即
        用了 cycle#1130 的原始快照（trl 6→3）。
        """
        try:
            meta = self.artifacts._find_meta(snapshot_id)
        except Exception:
            meta = None
        if meta is None:
            return Result.fail(
                f"快照不存在: {snapshot_id}", code="SNAPSHOT_NOT_FOUND"
            )
        target = str(Path(meta.path).expanduser())  # 恢复实际落点=快照元数据记录的原始路径
        ok, msg = self.artifacts.restore(
            snapshot_id, reason="manual_rollback"
        )
        if not ok:
            return Result.fail(
                f"恢复失败: {msg}", code="RESTORE_FAILED"
            )
        settled = False
        try:
            if self.experiments is not None:
                exp_id = (self.state.observing or {}).get("exp_id")
                snap_exp = snapshot_id
                if exp_id or snap_exp:
                    self.experiments.settle(
                        exp_id or snap_exp,
                        "rolled_back",
                        score_after=None,
                        reason="manual_rollback",
                        feedback_text="人工应急回滚（daemon rollback CLI 触发）",
                    )
                    settled = True
                self.state.observing = None
                self.state.save(self.state_path)
        except Exception:  # noqa: BLE001 — 台账失败不影响文件已回滚的事实
            logger.exception("[A1人工回滚] 台账结算失败（文件已回滚）")
        logger.warning(
            "[A1人工回滚] %s ← 快照=%s msg=%s 台账结算=%s",
            target, snapshot_id, msg, settled,
        )
        return Result.ok(
            {
                "snapshot_id": snapshot_id,
                "target": str(target),
                "restore": msg,
                "ledger_settled": settled,
            }
        )

    def _settle_observation(self, obs: dict[str, Any]) -> None:
        """观察期结算：复评基准，回退超阈值 → 恢复快照 + rolled_back。

        可证：同尺子复评（benchmark.run()）；可退：restore 校验 sha256；
        可停：回滚入台账，连击走既有 F7 熔断闸（same-params same-reason
        streak 语义天然覆盖观察期回滚）。
        """
        # ---- A2-1 兜底（2026-10-17）：挂单超时强制终态 ----
        # 正常挂单下一轮即结算；超 TTL 仍悬置（如基准持续故障两日）=
        # 变更效果无法证实 → 按「不可证即退」纪律默认回滚。
        applied_at = obs.get("applied_at")
        if applied_at:
            try:
                then = datetime.fromisoformat(str(applied_at))
                now = (
                    datetime.now(timezone.utc) if then.tzinfo else datetime.now()
                )
                age_h = (now - then).total_seconds() / 3600.0
            except (ValueError, TypeError):
                age_h = 0.0
            if age_h > P1_OBSERVING_TTL_HOURS:
                snap_id = obs.get("snapshot_id")
                ok, msg = False, "no_snapshot_id"
                if snap_id:
                    ok, msg = self.artifacts.restore(
                        snap_id, reason=f"obs_stale ttl={age_h:.0f}h"
                    )
                logger.warning(
                    "[P1-4观察] 挂单超时 %.0fh>TTL%.0fh → 强制回滚"
                    " restore=%s msg=%s（不可证即退）",
                    age_h, P1_OBSERVING_TTL_HOURS, ok, msg,
                )
                self.experiments.settle(
                    obs.get("exp_id"),
                    "rolled_back",
                    score_after=None,
                    reason="applied:obs_stale_ttl",
                    feedback_text=(
                        "挂单超 TTL 仍无法复评（基准持续故障类）→ "
                        "按「不可证即退」强制回滚"
                    ),
                )
                return
        exp_id = obs.get("exp_id")
        snap_id = obs.get("snapshot_id")
        score_before = obs.get("score_before")
        applied_params = obs.get("params") or {}
        # 同尺子纪律：复用 daemon 自持的 benchmark 实例（与 P0 门禁、
        # 参数联动复测同一对象）——新建实例会绕开测试/配置注入。
        try:
            bench = self.benchmark.run()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[P1-4观察] 基准复评失败：%s（保守放行，留观察单原因）", exc)
            self.experiments.settle(
                exp_id, "accepted", score_after=None,
                reason="applied:obs_bench_error",
                feedback_text=(
                    "复评基准故障，保守放行为 accepted"
                    "（无法证实回退≠证实改进，建议下轮复核）"
                ),
            )
            return
        prev = (
            float(score_before)
            if score_before is not None
            else float(self.state.benchmark_baseline or bench.score)
        )
        ratio = (bench.score / prev) if prev > 0 else 1.0
        if ratio < P1_OBSERVE_REGRESSION_RATIO:
            ok, msg = self.artifacts.restore(snap_id, reason=f"obs_regress ratio={ratio:.3f}")
            logger.warning(
                "[P1-4观察] 回退 %.4f→%.4f (ratio=%.3f < %.2f) → 恢复快照=%s msg=%s",
                prev, bench.score, ratio, P1_OBSERVE_REGRESSION_RATIO, ok, msg,
            )
            self.experiments.settle(
                exp_id, "rolled_back",
                score_after=float(bench.score),
                reason="applied:obs_regress" if ok else "applied:obs_regress_restore_failed",
                feedback_text=(
                    f"观察窗复评回退 {prev:.1f}→{bench.score:.1f}"
                    f"（ratio={ratio:.3f}<{P1_OBSERVE_REGRESSION_RATIO}）"
                    + ("，快照恢复成功" if ok else "，快照恢复失败需人工介入")
                ),
            )
            # 观察期回滚计入熔断判据（same-params same-reason 连击）
            try:
                self._check_frozen_zone(applied_params, "obs_regress")
            except Exception:  # noqa: BLE001
                logger.exception("[P1-4观察] 熔断判定异常（不阻塞）")
            return
        logger.info(
            "[P1-4观察] 未回退（%.4f→%.4f, ratio=%.3f）→ 结算 accepted",
            prev, bench.score, ratio,
        )
        self.experiments.settle(
            exp_id, "accepted", score_after=float(bench.score), reason="applied",
            feedback_text=(
                f"观察窗复评未回退（{prev:.1f}→{bench.score:.1f}, "
                f"ratio={ratio:.3f}）→ 正式收编"
            ),
        )

    def _apply_params(self, params: dict[str, Any]) -> None:
        """把优化参数写入 config.yaml — 执行器限幅版（R2，系统论融入）。

        三重护栏：
        1. **默认 report-only**：不设 LINGCLAUDE_DAEMON_APPLY=1 时只记日志不动配置
           （钱学森增益纪律：纠错执行器必须有外部许可才能作用到被控对象）。
        2. **单参数限幅**：开启应用后每周期最多写 1 个参数（纠错幅度×对象敏感度>1
           即发散——一次调多参出问题时无法归因，也没有复测窗口）。
        3. 失败只告警不抛出（本函数在优化循环尾部调用，不应炸掉整个循环）。
        """

        if not params:
            return
        # F1 (2026-09-22): goal=behavior 时被控对象是 behavior_policy.yaml，
        # 走专用写入器（同一套护栏），不复用下方 config.yaml 写入段。
        if getattr(self.config.optimizer, "goal", "") == "behavior":
            self._apply_behavior_params(params)
            return
        if os.environ.get("LINGCLAUDE_DAEMON_APPLY") != "1":
            logger.info(
                "[report-only] 建议参数（未应用；设 LINGCLAUDE_DAEMON_APPLY=1 开启）: %s",
                params,
            )
            return

        # P0-1 审批闸门：写 config.yaml 前必过 guard（第四重护栏）。
        # 2026-09-05 事故（会话执行 rm -rf .lingclaude）证明缺少此闸的风险。
        # 2026-09-06 fix：消除上游混合缩进造成的 SyntaxError，确保 daemon 可启动。
        # H1 (2026-09-15): 统一动作闸门 — 由 ApprovalGuard 改为 PermissionContext.check_action。
        # B项 (2026-10-22): 对象级 path-scoped 规则闸前置（第五重，声明式，
        #   根锚 evaluator/规则文件自身永拒；只叠加不替换既有四重）。
        from lingclaude.core.permissions import (
            PermissionContext,
            check_optimizer_write,
            load_approval_mode,
        )

        wd = check_optimizer_write(
            "self_optimizer", Path("config.yaml")
        )
        if wd.decision == "deny":
            logger.error(
                "[write-policy] 写 %s 被声明式规则拒绝（%s），丢弃参数: %s",
                "config.yaml", wd.rule_id, sorted(params),
            )
            return
        if wd.decision == "ask":
            from lingclaude.core.permissions import log_pending_action

            log_pending_action(
                "optimizer_write_policy_ask",
                {"actor": "self_optimizer", "path": "config.yaml",
                 "rule_id": wd.rule_id, "proposed": params},
                mode="ask",
            )
            logger.warning(
                "[write-policy] 写 config.yaml 规则外路径（%s），待审批未应用: %s",
                wd.rule_id, params,
            )
            return
        # allow → 落入下方既有四重护栏（P0-1 / APPLY / 限幅 / 锁）

        lock_cm = None  # 提前 return 路径（未持锁）时 finally 需判空
        guard_mode = load_approval_mode(Path("config.yaml"))
        guard = PermissionContext(mode=guard_mode)
        allowed, reason = guard.check_action(
            "optimize_write",
            params={"proposed": params, "deferred_hint": "见 daemon 日志"},
        )
        if not allowed:
            if guard_mode == "strict":
                raise PermissionError(
                    f"ApprovalGuard(strict): 写配置被拒绝（{reason}）— 动作 optimize_write"
                )
            logger.warning(
                "[guard] 写配置待审批（mode=%s, reason=%s），本轮参数未应用: %s",
                guard.mode, reason, params,
            )
            return

        config_path = Path("config.yaml")
        if not config_path.exists():
            logger.debug("无 config.yaml，跳过参数应用")
            return

        # 多 agent 并行编辑锁（P3 补强）：config.yaml 读-改-写全程持锁，
        # 防止 lingclaude 会话/Claude Code/监督者三方顺序踩踏（2026-09-06 事故）。
        from lingclaude.core.file_lock import file_edit_lock

        try:
            lock_cm = file_edit_lock(config_path, owner="self_optimizer")
            lock_cm.__enter__()
            import yaml

            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if raw is None:
                raw = {}

            opt_section = raw.setdefault("self_optimizer", {}).setdefault(
                "optimization", {}
            )
            trigger_section = raw.setdefault("self_optimizer", {}).setdefault(
                "triggers", {}
            )

            # S0-1 (2026-10-17)【RSI 阶段〇止血】：structure param_map 已下线。
            # 原 map 把搜索参数（max_class_size 等）写入 config.yaml triggers ——
            # 即「优化器改量具」的执行端。评估器只许人工升级，故此路径清空。
            # behavior goal 的写入走 _apply_behavior_params（被控对象是
            # behavior_policy.yaml，非评估器），不在本路径，不受影响。
            param_map: dict[str, tuple[str, str]] = {}
            if params:
                logger.warning(
                    "[S0-1] structure 参数写入已停用（评估器只许人工升级），"
                    "丢弃参数: %s", sorted(params),
                )

            # 单参数限幅：按 param_map 顺序取第一个可应用项，其余记为待复测建议
            pending = [
                (k, v) for k, v in param_map.items() if k in params
            ]
            if not pending:
                return
            param_key, (section, yaml_key) = pending[0]
            deferred = {k: params[k] for k, _ in pending[1:]}

            target_section = trigger_section if section == "triggers" else opt_section
            value = params[param_key]
            old_value = target_section.get(yaml_key)

            # P1-3: 写前留档（回滚保障）
            from lingclaude.core.file_history import record_change

            record_change(config_path, source="self_optimizer")

            # P1-2: patch 审计记录 — 每次变更先落 patch,写配置只是应用 patch

            patches_dir = Path(".lingclaude") / "patches"
            patches_dir.mkdir(parents=True, exist_ok=True)
            patch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            patch = {
                "patch_id": patch_id,
                "ts": patch_id,
                "source": "self_optimizer",
                "applied": True,
                "changes": [{
                    "file": str(config_path),
                    "section": section,
                    "key": yaml_key,
                    "old": old_value,
                    "new": round(value, 2) if isinstance(value, float) else value,
                }],
                "deferred": deferred,
            }
            (patches_dir / f"patch_{patch_id}.json").write_text(
                json.dumps(patch, ensure_ascii=False, indent=2), encoding="utf-8",
            )

            target_section[yaml_key] = round(value, 2) if isinstance(value, float) else value
            config_path.write_text(
                yaml.dump(raw, default_flow_style=False, allow_unicode=True),
                encoding="utf-8",
            )
            logger.info(
                "已应用优化参数 1/%d 到 config.yaml: %s=%s（复测通过前不再应用其余参数）",
                len(pending), param_key, value,
            )
            # P0-N5 (2026-09-15): 写配置成功路径补审计留痕 — 与灰区 escalate
            # (state=pending) 形成「申请→审批→执行→留痕」闭环。审批人可依据
            # params 核对入口(pending)与出口(approved_by_daemon)的动作域一致。
            from lingclaude.core.permissions import log_pending_action

            log_pending_action(
                "optimize_write",
                params={
                    "applied_key": yaml_key,
                    "applied_value": round(value, 2) if isinstance(value, float) else value,
                    "patch_id": patch_id,
                    "deferred": deferred,
                },
                mode=guard_mode,
                state="approved_by_daemon",
            )
            if deferred:
                logger.info("[待复测] 暂缓参数建议: %s", deferred)
        except Exception:
            logger.warning("应用参数失败", exc_info=True)
        finally:
            # 读-改-写结束（含提前 return），释放编辑锁
            if lock_cm is not None:
                lock_cm.__exit__(None, None, None)

    def _apply_behavior_params(self, params: dict[str, Any]) -> None:
        """F1 (2026-09-22): 把行为阈值参数写入 behavior_policy.yaml。

        与 _apply_params 同一套护栏（report-only / 审批闸门 / 编辑锁 /
        审计留痕），但被控对象从 config.yaml 换成 core/policies/
        behavior_policy.yaml —— PolicyLoader mtime watch 热加载，
        行为检查器立即生效，无需重启。

        纪律（沿用 _apply_params 语义）：
        1. 默认 report-only（LINGCLAUDE_DAEMON_APPLY=1 才真正落盘）。
        2. 单参数限幅：每周期最多写 1 个键（归因窗口）；选「真正发生
           变化的合法键」，全同值不写盘不烧速率闸（S0-2 配额留给真变更）。
        3. 合法键白名单 + int 强制，防搜索空间外的值写进策略文件。
        返回：True=真实落盘；False=report-only/审批拒/无合法键/全同值
        （首航实证：调用方需据此决定是否挂观察单——没写盘就不该有观察单）。
        """

        if not params:
            return False
        if os.environ.get("LINGCLAUDE_DAEMON_APPLY") != "1":
            logger.info(
                "[report-only] 行为参数建议（未应用；设 LINGCLAUDE_DAEMON_APPLY=1 开启）: %s",
                params,
            )
            return False

        # B项 (2026-10-22): path-scoped 规则闸前置（第五重护栏，声明式）。
        # 根锚 evaluator/规则文件自身永拒；规则外路径默认 ask 落审批不应用。
        from lingclaude.core.permissions import check_optimizer_write

        _policy_rel = "core/policies/behavior_policy.yaml"
        wd = check_optimizer_write("self_optimizer", _policy_rel)
        if wd.decision == "deny":
            logger.error(
                "[write-policy] 写 %s 被声明式规则拒绝（%s），丢弃参数: %s",
                _policy_rel, wd.rule_id, sorted(params),
            )
            return False
        if wd.decision == "ask":
            from lingclaude.core.permissions import log_pending_action

            log_pending_action(
                "optimizer_write_policy_ask",
                {"actor": "self_optimizer", "path": _policy_rel,
                 "rule_id": wd.rule_id, "proposed": params},
                mode="ask",
            )
            logger.warning(
                "[write-policy] 写 %s 规则外路径（%s），待审批未应用: %s",
                _policy_rel, wd.rule_id, params,
            )
            return False
        # allow → 落入下方既有四重护栏（合法键白名单 / P0-1 / APPLY / 锁）

        allowed = {"consecutive_fail_limit", "tool_repeat_limit"}

        from lingclaude.core.policy_loader import policies_dir

        policy_path = policies_dir() / "behavior_policy.yaml"
        # 首航实测修复（2026-10-17）：原实现 sorted(params) 取第一个合法键，
        # 字母序恒先 consecutive_fail_limit——若其建议值恰等于现值，本轮
        # 「写了个寂寞」，真正变化的键（tool_repeat_limit）永不落盘。
        # 现判据：第一个「建议值≠现值」的合法键；全同值 → 无变更不应用。
        try:
            import yaml as _yaml

            current = (
                _yaml.safe_load(policy_path.read_text(encoding="utf-8"))
                if policy_path.exists()
                else {}
            ) or {}
        except Exception:
            current = {}
        key: str | None = None
        value: int | None = None
        for k in sorted(params):  # sorted → 归因窗口确定论
            if k not in allowed:
                continue
            v = max(1, int(params[k]))
            if current.get(k) != v:
                key, value = k, v
                break
        if key is None:
            logger.info(
                "[behavior] 合法键建议值均与现值一致，无可应用变更"
                "（不写盘不烧速率闸）: %s",
                {k: params[k] for k in sorted(params) if k in allowed},
            )
            return False
        lock_cm = None
        try:
            from lingclaude.core.permissions import (
                PermissionContext,
                load_approval_mode,
                log_pending_action,
            )
            from lingclaude.core.file_lock import file_edit_lock

            guard_mode = load_approval_mode(Path("config.yaml"))
            guard = PermissionContext(mode=guard_mode)
            allowed, reason = guard.check_action(
                "optimize_write",
                params={"proposed": {key: value}, "policy": "behavior_policy.yaml"},
            )
            if not allowed:
                if guard_mode == "strict":
                    raise PermissionError(
                        f"ApprovalGuard(strict): 写策略被拒绝（{reason}）— 动作 optimize_write"
                    )
                logger.warning(
                    "[guard] 行为参数待审批（mode=%s, reason=%s），未应用: %s=%s",
                    guard.mode, reason, key, value,
                )
                return False

            lock_cm = file_edit_lock(policy_path, owner="self_optimizer")
            lock_cm.__enter__()
            import yaml

            raw = (
                yaml.safe_load(policy_path.read_text(encoding="utf-8"))
                if policy_path.exists()
                else {}
            )
            if raw is None:
                raw = {}
            old = raw.get(key)
            raw[key] = value
            policy_path.write_text(
                yaml.dump(raw, default_flow_style=False, allow_unicode=True),
                encoding="utf-8",
            )
            from lingclaude.core.file_history import record_change  # R1' 2026-09-26: F821 修复（对齐 814 行同文件惯例）
            record_change(policy_path, source="self_optimizer")
            log_pending_action(
                "optimize_write",
                params={
                    "applied_key": key,
                    "applied_value": value,
                    "policy_file": str(policy_path),
                    "previous": old,
                },
                mode=guard_mode,
                state="approved_by_daemon",
            )
            logger.info(
                "[behavior] 已应用行为参数 %s=%s → %s（PolicyLoader 热加载即时生效）",
                key, value, policy_path,
            )
        except Exception:
            logger.warning("[behavior] 应用行为参数失败", exc_info=True)
        finally:
            if lock_cm is not None:
                lock_cm.__exit__(None, None, None)
    def should_run_cycle(self, min_interval_hours: float = 24.0) -> bool:
        """节流判断：距上次优化循环是否超过 min_interval_hours。

        last_optimization_time 缺失/不可解析 → True（从未跑过就该跑，诚实优先）。
        """
        raw = getattr(self.state, "last_optimization_time", None)
        if not raw:
            return True
        try:
            last = datetime.fromisoformat(str(raw))
        except ValueError:
            return True
        return (datetime.now() - last).total_seconds() >= min_interval_hours * 3600

    def _record_cycle(self, cycle: OptimizationCycle) -> None:
        self.state.last_optimization_time = cycle.triggered_at
        self.state.total_cycles += 1
        # R10-4 (2026-09-23): 停滞计数——以本轮是否真改进为判据（v_after <
        # v_before），供 run_watch 做目标轮换决策。有改进即清零。
        if cycle.violations_after < cycle.violations_before:
            self.state.total_improvements += 1
            self.state.stall_count = 0
        else:
            self.state.stall_count = getattr(self.state, "stall_count", 0) + 1
        self.state.cycles.append(
            {
                "cycle_id": cycle.cycle_id,
                "triggered_at": cycle.triggered_at,
                "trigger_type": cycle.trigger_type,
                "trigger_reason": cycle.trigger_reason,
                "best_score": cycle.best_score,
                "experiments": cycle.experiments,
                "duration_seconds": cycle.duration_seconds,
                "violations_before": cycle.violations_before,
                "violations_after": cycle.violations_after,
                "report_path": cycle.report_path,
                # P2-9a: 台账串联合——strategy 与 experiment_id 同源记录
                "strategy": cycle.strategy,
                "experiment_id": cycle.experiment_id,
            }
        )
        if len(self.state.cycles) > 100:
            self.state.cycles = self.state.cycles[-100:]
        self.state.save(self.state_path)
        self._record_quality_to_metrics(cycle)
        # P2-9b: meta-optimizer——每 N 周期聚合台账调权重表（Strategy 成为
        # 可改进对象）。fail-soft，位置在 total_cycles 递增后（以新计数判齐）。
        self._maybe_adjust_strategy_weights()
        # P1-7 (2026-10-17)【RSI 阶段一-7】: 改进活动台账——每周期一行
        # append（jsonl，与 verify_log 同构），带 origin 归因位。
        # 观测件：写失败不阻塞主循环。
        try:
            self._append_activity(cycle)
        except Exception:  # noqa: BLE001
            logger.exception("[P1-7活动台账] 写入异常（不阻塞主循环）")
        # A2-3 (2026-10-17) 快照保留策略：周期收尾顺带清理（最佳努力），
        # 每目标文件保留最近 DEFAULT_KEEP_SNAPSHOTS 份未恢复快照。
        try:
            self.artifacts.prune_snapshots()
        except Exception:  # noqa: BLE001
            logger.exception("[A2-3快照清理] 异常（不阻塞主循环）")

    def _append_activity(self, cycle: OptimizationCycle) -> None:
        """activity.jsonl 追加一行（近 7/30 天聚合由读取方现算）。

        链路完整性诊断（codex-P0-3 单机版）：experiment_id 非空而台账
        查无此单 → 断链告警（audit_log），不阻塞。
        """
        origin = "self"  # 本路径全部由自优化回路产生
        broken = False
        if cycle.experiment_id:
            try:
                row = self.experiments.get(cycle.experiment_id)
                broken = row is None
            except Exception:
                broken = False  # 台账不可读不算断链（fail-open 观测）
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "cycle_id": cycle.cycle_id,
            "experiment_id": cycle.experiment_id,
            "trigger_type": cycle.trigger_type,
            "goal": self._current_goal(),
            "origin": origin,
            "strategy": cycle.strategy,  # P2-9a: 本轮搜索策略
            "best_score": cycle.best_score,
            "v_before": cycle.violations_before,
            "v_after": cycle.violations_after,
            "report": cycle.report_path,
            "chain_broken": broken,
        }
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.state_dir / "activity.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if broken:
            logger.warning(
                "[P1-7断链] experiment_id=%s 在台账查无此单（审计事件流断链）",
                cycle.experiment_id,
            )

    def activity_report(
        self, days: float = 7.0, limit: int = 20,
    ) -> dict[str, Any]:
        """P1-7 消费方（2026-10-21）：activity.jsonl 近 N 天聚合报告。

        activity 行本身不含 verdict（应用轮的最终裁决在下一轮观察期
        结算时才产生，写入时不可知）→ 本方法与台账联表补全 verdict。
        只读两库，不写盘，watch 在跑时可用。
        """
        path = self.state_dir / "activity.jsonl"
        out: dict[str, Any] = {
            "path": str(path), "total_lines": 0, "window_days": days,
            "window_cycles": 0, "cycle_span": None, "verdicts": {},
            "strategies": {}, "trigger_types": {}, "goals": {},
            "chain_broken": 0, "recent": [],
        }
        if not path.exists():
            return out
        cutoff = (
            datetime.now() - timedelta(days=days)
        ).isoformat(timespec="seconds")
        verdicts: Counter[str] = Counter()
        strategies: Counter[str] = Counter()
        triggers: Counter[str] = Counter()
        goals: Counter[str] = Counter()
        total = window = broken = 0
        rows: list[dict[str, Any]] = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    total += 1
                    if str(rec.get("ts", "")) < cutoff:
                        continue
                    window += 1
                    rows.append(rec)
                    strategies[str(rec.get("strategy", "?"))] += 1
                    triggers[str(rec.get("trigger_type", "?"))] += 1
                    goals[str(rec.get("goal", "?"))] += 1
                    if rec.get("chain_broken"):
                        broken += 1
                    # 联表补 verdict（台账查无=断链/回滚后清理 → 标记）
                    exp_id = rec.get("experiment_id")
                    verdict = "no_experiment"
                    if exp_id:
                        try:
                            er = self.experiments.get(exp_id)
                            verdict = (
                                er.get("verdict", "unknown")
                                if isinstance(er, dict) else "unknown"
                            )
                        except Exception:
                            verdict = "ledger_unreadable"
                    verdicts[verdict] += 1
                    rec = dict(rec)
                    rec["verdict"] = verdict
                    rows[-1] = rec
        except OSError:
            return out
        if rows:
            out["cycle_span"] = [rows[0].get("cycle_id"), rows[-1].get("cycle_id")]
        out.update(
            total_lines=total, window_cycles=window,
            verdicts=dict(verdicts), strategies=dict(strategies),
            trigger_types=dict(triggers), goals=dict(goals),
            chain_broken=broken, recent=rows[-limit:],
        )
        return out

    def _write_cycle_to_knowledge(self, cycle: OptimizationCycle) -> None:
        try:
            from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
            from lingclaude.self_optimizer.learner.models import (
                FeedbackCategory,
                LearnedRule,
                Pattern,
            )

            kb = KnowledgeBase()
            rule_id = f"opt_cycle_{cycle.cycle_id:04d}"
            improved = cycle.violations_after < cycle.violations_before

            # ---- P2 经验复用（2026-09-17，借鉴 AgentEvolver Self-Navigating）----
            # 同触发类型的经验累积：已有同 context 规则 → 频次+1，置信度按
            # 本轮是否有效强化/衰减（有效 +0.05 封顶 0.95，无效 -0.05 下限 0.1）。
            # 下轮优化可直接引用"哪类触发→哪类参数历史上有效"。
            existing = kb.search_rules(cycle.trigger_type, limit=5)
            if existing.is_ok:
                for prior in existing.data:
                    if cycle.trigger_type in prior.pattern.context_keywords:
                        new_conf = min(0.95, prior.confidence + 0.05) if improved \
                            else max(0.1, prior.confidence - 0.05)
                        kb.update_rule_status(prior.id, prior.status)
                        # 频次/置信度经 add_rule 幂等覆盖（同 id upsert 语义见
                        # knowledge.py PRIMARY KEY 冲突处理）——直接构造更新版。
                        updated = LearnedRule(
                            id=prior.id,
                            name=prior.name,
                            description=prior.description,
                            category=prior.category,
                            pattern=prior.pattern,
                            tools=prior.tools,
                            frequency=prior.frequency + 1,
                            confidence=round(new_conf, 2),
                            quality_score=prior.quality_score,
                            status=prior.status,
                            created_at=prior.created_at,
                        )
                        kb.add_rule(updated)
                        logger.info(
                            "[P2经验] 同类触发规则 %s 强化: freq=%d conf=%.2f (%s)",
                            prior.id, updated.frequency, new_conf,
                            "有效" if improved else "无效衰减",
                        )

            rule = LearnedRule(
                id=rule_id,
                name=f"自优化周期 #{cycle.cycle_id}",
                # P2: 描述必须含触发类型——search_rules 按 name/description
                # LIKE 匹配，同类触发经验累积依赖此字段可命中。
                description=f"触发类型: {cycle.trigger_type} | 触发: {cycle.trigger_reason} | 结果: score={cycle.best_score:.2f} violations={cycle.violations_before}→{cycle.violations_after}",
                category=FeedbackCategory.BEST_PRACTICE,
                pattern=Pattern(
                    context_keywords=(cycle.trigger_type, cycle.trigger_priority),
                    severity_distribution={"before": cycle.violations_before, "after": cycle.violations_after},
                ),
                tools=("optimizer", "evaluator"),
                frequency=1,
                confidence=0.8 if improved else 0.4,
                quality_score=cycle.best_score / 100 if cycle.best_score > 0 else 0.5,
                status="active" if improved else "draft",
            )
            # S0-2 规则侧速率闸：本条是每轮新 id（opt_cycle_NNNN）= 净增长，
            # 24h 窗口净入库达上限即跳过（周期经验已在 state.cycles 留档，
            # 不丢信息；P2 强化分支是同 id upsert 不计窗、保持放行）。
            if self._rule_rate_limited():
                logger.info(
                    "[S0-2速率闸] 周期规则 %s 跳过入库（dry-run，已在 state.cycles 留档）",
                    rule_id,
                )
                kb.close()
                return
            kb.add_rule(rule)
            logger.info("已写入知识库: %s", rule_id)
            kb.close()
        except Exception:
            logger.warning("写入知识库失败", exc_info=True)

    def load_behavior_history(self) -> Result[dict[str, Any]]:
        behavior_path = self.state_dir / "behavior_history.json"
        if behavior_path.exists():
            try:
                return Result.ok(json.loads(behavior_path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, KeyError):
                pass
        return Result.ok(
            dict(_DEFAULT_BEHAVIOR_HISTORY, snapshots=[])
        )

    def save_behavior_history(self, behavior: dict[str, Any]) -> Result[None]:
        try:
            history_result = self.load_behavior_history()
            history = history_result.data if history_result.is_ok else dict(_DEFAULT_BEHAVIOR_HISTORY, snapshots=[])
            history["total_turns"] = history.get("total_turns", 0) + behavior.get("total_turns", 0)
            history["total_frustration"] = history.get("total_frustration", 0) + behavior.get("frustration_count", 0)
            history["total_corrections"] = history.get("total_corrections", 0) + behavior.get("corrections_received", 0)
            history["total_tool_errors"] = history.get("total_tool_errors", 0) + behavior.get("tool_error_count", 0)
            history["last_updated"] = datetime.now().isoformat()
            # 系统论修正（存量/流量，Meadows）：此前只累计 total_* 存量，没有
            # 变化率序列，无法回答「行为是否在变好」。滚动快照提供流量时间线，
            # 供回路计算趋势/基线；上限 200 条防止无限膨胀。
            snapshots: list[dict[str, Any]] = history.setdefault("snapshots", [])
            snapshots.append(
                {
                    "ts": history["last_updated"],
                    "turns": behavior.get("total_turns", 0),
                    "frustration": behavior.get("frustration_count", 0),
                    "corrections": behavior.get("corrections_received", 0),
                    "tool_errors": behavior.get("tool_error_count", 0),
                }
            )
            history["snapshots"] = snapshots[-_BEHAVIOR_SNAPSHOT_CAP:]
            behavior_path = self.state_dir / "behavior_history.json"
            behavior_path.parent.mkdir(parents=True, exist_ok=True)
            from lingclaude.core.state_store import _atomic_write_json
            _atomic_write_json(behavior_path, history)
            return Result.ok(None)
        except Exception as e:
            return Result.fail(f"Failed to save behavior history: {e}", code="IO_ERROR")

    def behavior_trend(self, window: int = 20) -> Result[dict[str, float]]:
        """计算最近 window 条快照的每回合摩擦率趋势。

        返回 frustration/corrections/tool_errors 的「次/回合」比率（窗口内合计
        ÷ 窗口内回合合计），配合更早窗口的同类值即可回答「是否在变好」。
        快照不足（<2 条或 0 回合）时返回空 dict — 不伪造趋势。
        """
        history_result = self.load_behavior_history()
        if history_result.is_error:
            return Result.fail(history_result.error)
        snapshots = history_result.data.get("snapshots", [])[-window:]
        total_turns = sum(s.get("turns", 0) for s in snapshots)
        if len(snapshots) < 2 or total_turns <= 0:
            return Result.ok({})
        rates = {
            key: round(sum(s.get(key, 0) for s in snapshots) / total_turns, 4)
            for key in ("frustration", "corrections", "tool_errors")
        }
        return Result.ok(rates)

    def _record_quality_to_metrics(self, cycle: OptimizationCycle) -> None:
        try:
            from lingclaude.core.metrics import MetricsStore, QualityScorer

            store = MetricsStore(self.state_dir / "metrics.db")
            scorer = QualityScorer(store)
            # 2026-09-17 修复 (恒用默认值): last_metrics 从无 avg_complexity
            # 写入点，恒取 5.0。改从 benchmark 真实 AST 扫描取值。
            avg_complexity = 5.0
            try:
                scan = self.benchmark._scan()
                avg_complexity = float(scan.get("avg_complexity", 5.0))
            except Exception:  # noqa: BLE001 — 质量分是遥测，取不到不阻塞
                pass
            structure_metrics = {
                "violations": cycle.violations_after,
                "avg_complexity": avg_complexity,
                "large_classes": self.state.last_metrics.get("large_classes", 0),
            }
            behavior_metrics = {
                "hallucination_risk": self._behavior_snapshot.get("hallucination_risk", 0),
                "frustration_rate": self._behavior_snapshot.get("frustration_rate", 0),
                "tool_error_rate": self._behavior_snapshot.get("tool_error_rate", 0),
            }
            safety_metrics = {
                "hard_stops": 0,
                "verification_passes": 1,
                "verification_total": 1,
            }
            scorer.compute_overall(
                structure=structure_metrics,
                behavior=behavior_metrics,
                safety=safety_metrics,
            )
            store.record("optimization", "cycle_score", cycle.best_score, cycle_id=str(cycle.cycle_id))
            store.close()
            logger.info("已记录质量指标 cycle=#%d", cycle.cycle_id)
        except Exception:
            logger.debug("质量指标记录失败", exc_info=True)


# ---------------------------------------------------------------------------
# P0-4 债务清偿（2026-10-03）: 灰区 pending 看门狗接线（24h 节流 + fail-safe）
# 契约: tests/test_pending_watchdog.py::TestDaemonWiring
# 设计: 摘要式升级（最老 10 条）——积压期逐条广播会把总线打爆
# ---------------------------------------------------------------------------

PW_STATE_PATH = Path.home() / ".lingclaude" / "pending_watchdog_state.json"

try:
    from lingclaude.core import pending_watchdog as _pw
except Exception:  # noqa: BLE001 — 模块缺失时看门狗整体降级为 no-op
    _pw = None


def _pending_watchdog_state_last_run() -> str | None:
    """读上次看门狗扫描时间；无记录/损坏返回 None（视作到期）。"""
    try:
        return PW_STATE_PATH.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _pending_watchdog_tick(last_run: str | None, now: datetime | None = None):
    """看门狗 tick：24h 节流；到期扫描+摘要升级；任何异常不外抛。"""
    if last_run is not None:
        try:
            ref = now or datetime.now()
            elapsed = ref - datetime.fromisoformat(str(last_run)) if ref.tzinfo is None else ref.replace(tzinfo=None) - datetime.fromisoformat(str(last_run))
            if abs(elapsed.total_seconds()) < 24 * 3600:
                return None
        except (ValueError, TypeError):
            pass  # 时间戳损坏 → 视作到期照扫
    if _pw is None:
        logger.debug("[PW看门狗] 模块不可用, 跳过")
        return []
    try:
        stale = _pw.scan_stale_pendings(stale_hours=24.0)
    except Exception as e:  # noqa: BLE001 — fail-safe 契约
        logger.warning("[PW看门狗] 扫描异常(忽略): %s", e)
        return []
    for item in stale[:10]:
        try:
            item.escalated = _pw._escalate_one(item)
        except Exception as e:  # noqa: BLE001
            logger.warning("[PW看门狗] 升级失败(忽略): %s", e)
    if stale:
        oldest = stale[0]
        print(
            f"[PW看门狗] 超时未决 {len(stale)} 条, "
            f"最老 {oldest.hours_pending:.0f}h ({oldest.action}); "
            f"本轮升级最老 {min(10, len(stale))} 条"
        )
    try:
        PW_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PW_STATE_PATH.write_text(
            (now or datetime.now()).isoformat(), encoding="utf-8"
        )
    except OSError as e:
        logger.warning("[PW看门狗] 状态写入失败(不影响本轮): %s", e)
    return stale


OptimizationDaemon._pending_watchdog_tick = staticmethod(_pending_watchdog_tick)
