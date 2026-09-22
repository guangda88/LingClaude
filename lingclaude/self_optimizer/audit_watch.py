"""F4 值守（2026-09-23，诊断文档 §M 执行序）：返审触发器入 daemon 值守循环。

三级节流（§L.4 规格）：
  1) 触发器自身指纹守卫：无 HEAD/关键文件/台账变化 → 不深审（scripts/
     self_audit_trigger.py 内部实现，近似零开销）；
  2) 值守层 24h 例行核账：覆盖无指纹项（如债务到期 sweep_debts）；
  3) 报告强制出口：审计结果落 KB（category=audit），进 F0 取数面。

审计权限边界不变：发现 → 入册 → 等待，值守不自动改码。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 层级2：例行全量核账的最小间隔（小时）。触发驱动的深审不受此限——
# 指纹变化随时审，此间隔只约束"无指纹项"的例行核账频率。
_FULL_SWEEP_MIN_HOURS = 24.0


@dataclass
class AuditWatchState:
    """值守层自身状态（独立小 JSON，不混入 daemon_state）。"""

    last_full_sweep: str | None = None  # ISO 时间

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "AuditWatchState":
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                return cls(last_full_sweep=raw.get("last_full_sweep"))
            except (ValueError, OSError):
                pass
        return cls()


_AUDIT_TRIGGER_MOD = None  # 进程内缓存：exec_module 一次


def _load_audit_trigger():
    """加载 scripts/self_audit_trigger.py。

    scripts/ 非包，且其内部 `from arch_ledger import ...` 依赖
    scripts/ 在 sys.path（直接跑脚本时由解释器自动加）——此处显式
    注册 scripts/ 后再 exec，保证 daemon 进程内可用。
    """
    global _AUDIT_TRIGGER_MOD
    if _AUDIT_TRIGGER_MOD is not None:
        return _AUDIT_TRIGGER_MOD
    root = Path(__file__).resolve().parent.parent.parent
    path = root / "scripts" / "self_audit_trigger.py"
    scripts_dir = str(path.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("lc_self_audit_trigger", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["lc_self_audit_trigger"] = mod
    spec.loader.exec_module(mod)
    _AUDIT_TRIGGER_MOD = mod
    return mod


class AuditWatch:
    """返审值守：薄封装触发器 + 分级节流 + 报告强制出口。"""

    def __init__(
        self,
        state_dir: Path,
        kb_path: Path | None = None,
        min_sweep_hours: float = _FULL_SWEEP_MIN_HOURS,
    ) -> None:
        self.state_path = Path(state_dir) / "audit_watch_state.json"
        self.state = AuditWatchState.load(self.state_path)
        self.kb_path = kb_path  # None → KnowledgeBase 默认（项目 KB）
        self.min_sweep_hours = min_sweep_hours
        self.trigger = _load_audit_trigger()

    # ---- 层级2：例行核账节流 ----

    def _sweep_due(self) -> bool:
        if self.state.last_full_sweep is None:
            return True
        try:
            last = datetime.fromisoformat(self.state.last_full_sweep)
        except ValueError:
            return True
        elapsed = (datetime.now() - last).total_seconds()
        return elapsed >= self.min_sweep_hours * 3600

    # ---- 主入口：daemon 值守循环每轮调用 ----

    def run_once(self, write_kb: bool = True) -> dict:
        """跑一轮值守，返回诊断摘要。

        - run_audit()：层级1指纹守卫在脚本内部，无变化近似零开销；
        - sweep_due 时做例行核账（层级2，覆盖无指纹项）；
        - write_kb 时报告强制出口（层级3，失败仅记日志不阻塞值守）。
        """
        exit_code = self.trigger.run_audit()

        sweep_expired: list[str] = []
        if self._sweep_due():
            sweep_expired = self.trigger.sweep_debts()
            self.state.last_full_sweep = datetime.now().isoformat()
            self.state.dump(self.state_path)

        diag: dict = {
            "exit": exit_code,
            "open_tasks": len(self.trigger.open_tasks()),
            "sweep_expired": sweep_expired,
        }
        if write_kb:
            diag["kb_rule"] = self._write_report_rule(diag)
        return diag

    # ---- 层级3：报告强制出口 ----

    def _write_report_rule(self, diag: dict) -> str | None:
        """审计结果 → KB 规则（category=audit，进 F0 取数面）。

        同日多次值守经 add_rule 幂等 upsert（F3 语义）合并为一行；
        出口尽力而为：失败仅记日志，不阻塞值守循环。
        """
        try:
            from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
            from lingclaude.self_optimizer.learner.models import (
                FeedbackCategory,
                LearnedRule,
                Pattern,
            )

            day = datetime.now().strftime("%Y%m%d")
            rid = f"audit_report_{day}"
            kb = KnowledgeBase(db_path=str(self.kb_path) if self.kb_path else None)
            rule = LearnedRule(
                id=rid,
                name=f"返审值守报告 {day}",
                description=(
                    f"exit={diag['exit']} open_tasks={diag['open_tasks']} "
                    f"sweep_expired={len(diag['sweep_expired'])}"
                ),
                category=FeedbackCategory.AUDIT,
                pattern=Pattern(context_keywords=("audit", "返审", "值守")),
                tools=("audit_watch",),
                frequency=1,
                confidence=0.9,
                quality_score=0.0,
                status="active",
            )
            r = kb.add_rule(rule)
            if not r.success:
                logger.warning("[F4值守] 审计报告出口写 KB 失败: %s", r.error)
                return None
            return rid
        except Exception:  # noqa: BLE001
            logger.exception("[F4值守] 审计报告出口异常（不阻塞值守）")
            return None
