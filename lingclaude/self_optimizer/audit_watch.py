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
        enable_f5_hooks: bool = True,
    ) -> None:
        self.state_path = Path(state_dir) / "audit_watch_state.json"
        self.state = AuditWatchState.load(self.state_path)
        self.kb_path = kb_path  # None → KnowledgeBase 默认（项目 KB）
        self.min_sweep_hours = min_sweep_hours
        self.enable_f5_hooks = enable_f5_hooks  # False：测试环境禁真实钩子
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
        - write_kb 时报告强制出口（层级3，失败仅记日志不阻塞值守）；
        - F5 挂钩（2026-09-23）：sweep_due 时顺跑 corrections 回填 +
          规则 conf 治理，并对其落账 verify_ledger（脚本失败仅记日志）。
        """
        exit_code = self.trigger.run_audit()

        sweep_expired: list[str] = []
        f5: dict = {}
        if self._sweep_due():
            sweep_expired = self.trigger.sweep_debts()
            self.state.last_full_sweep = datetime.now().isoformat()
            self.state.dump(self.state_path)
            if self.enable_f5_hooks:
                f5 = self._run_f5_hooks()

        diag: dict = {
            "exit": exit_code,
            "open_tasks": len(self.trigger.open_tasks()),
            "sweep_expired": sweep_expired,
        }
        if f5:
            diag["f5"] = f5
        if write_kb:
            diag["kb_rule"] = self._write_report_rule(diag)
        return diag

    # ---- F5 挂钩：corrections 回填 + conf 治理 + 证据落账 ----

    def _run_f5_hooks(self) -> dict:
        """sweep_due 时顺跑 F5 数据钩子，结果落 verify_ledger。

        - corrections_backfill：hard_interrupt → corrections 落账（幂等）；
        - rule_conf_governor：flywheel/refined 规则 conf 三档归位（幂等）；
        - 证据口径：以「脚本 exit=0 + 输出行」为 evidence，claim 写摘要。
          任一脚本失败则不落 evidence 档、只记日志（fail-open 不阻塞值守）。

        F5 触发条件②（verify_ledger 自动落账）由此从"人工"变"值守自动"，
        条件③（F0 基线 ≥1 周）继续按墙钟积累。
        """
        import subprocess

        scripts = Path(__file__).resolve().parent.parent.parent / "scripts"
        result: dict = {"backfill": None, "governor": None, "verified": False}
        outputs: list[str] = []
        all_ok = True
        for name in ("corrections_backfill.py", "rule_conf_governor.py"):
            try:
                proc = self._run_subprocess(str(scripts / name), timeout=300)
                ok = proc.returncode == 0
                all_ok = all_ok and ok
                result["backfill" if name.startswith("corrections") else "governor"] = (
                    "ok" if ok else f"exit={proc.returncode}"
                )
                outputs.append(f"$ {name}\n{proc.stdout.strip()[:500]}")
                if not ok:
                    logger.warning("F5 hook %s 失败: %s", name, proc.stderr[:300])
            except Exception as e:  # noqa: BLE001 fail-open
                all_ok = False
                result["backfill" if name.startswith("corrections") else "governor"] = f"error={e}"
                logger.warning("F5 hook %s 异常: %s", name, e)
        if all_ok and outputs:
            try:
                from lingclaude.core import verify_ledger as _vl

                _vl.record_verify(
                    kind=_vl.KIND_TEST_PASSED,
                    claim="F5 hooks: corrections_backfill + rule_conf_governor 均成功",
                    evidence="\n".join(outputs)[:2000],
                    trace_id=f"f5_watch_{datetime.now().strftime('%Y%m%d%H%M')}",
                    via="audit_watch",
                )
                result["verified"] = True
            except Exception as e:  # noqa: BLE001 fail-open
                logger.warning("F5 verify 落账失败: %s", e)
        return result

    @staticmethod
    def _run_subprocess(script_path: str, timeout: int):
        """钩子脚本执行器（独立方法便于测试打桩，不碰真实 subprocess）。"""
        import subprocess

        return subprocess.run(
            [sys.executable, script_path],
            capture_output=True, text=True, timeout=timeout,
        )

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
