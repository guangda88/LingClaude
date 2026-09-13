"""L10-A 后置审计层 (Post-Output Declaration Audit)

防"声明≠执行"风险: 模型输出后提取执行性声明 (如"已通知灵安"),
异步调灵极优 L10-B verify_claim 验证声明是否有对应 Datalog 事件,
不匹配则告警到 LingBus, 不阻断输出.

与 T1 FactChecker 的分工:
- T1 (fact_checker.py): 验证"内容真实" (陈述性 claim, 查灵知 KG)
- L10-A (本模块): 验证"行为真实" (执行性声明, 查 L6 Datalog 事件)

接口契约 (基于 L10 thread 1c9d463fa3 共识):
- 复用灵极优 CLAIM_PATTERNS (15 个模式), 不自建第二套
- 匹配必须精确, 非语义相似度 (灵安要求)
- 异步告警, 不阻断 (atomcode + 灵克共识)
- verify_claim 二值返回 verified=true/false (灵极优 v1.1-final)

依赖:
- 灵极优 lingyuan.l10_trust_anchor.verify_claim (commit 278aedd, 2026-07-21)
- 灵极优 lingyuan.l10_trust_anchor.CLAIM_PATTERNS (15 模式)
- 灵安 lingan.security_gate.LingAn (Layer A 6 层 rules, v0.2.1 接入)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 灵极优 L10-B 信任锚点导入 (旁路, 与 T1 FactChecker 导入灵知同模式)
_LINGMINOPT_PATH = os.environ.get(
    "LINGMINOPT_PATH", str(Path(__file__).parent.parent.parent.parent / "lingminopt")
)
if _LINGMINOPT_PATH not in sys.path:
    sys.path.insert(0, _LINGMINOPT_PATH)

try:
    from lingyuan.l10_trust_anchor import CLAIM_PATTERNS as _LMO_CLAIM_PATTERNS
    from lingyuan.l10_trust_anchor import verify_claim as _lmo_verify_claim
    _L10B_AVAIL = True
    logger.info("灵极优 L10-B verify_claim 可用, 已加载 (15 模式)")
except ImportError:
    _L10B_AVAIL = False
    _LMO_CLAIM_PATTERNS = []
    _lmo_verify_claim = None  # type: ignore[assignment]
    logger.info("灵极优 L10-B 不可用, L10-A 将使用 mock verifier")

# 灵安 LingAn security_gate 导入 (v0.2.1 接入 Layer A)
# 用 importlib 绝对路径加载, 避免 lingmemory/security_gate.py 旧骨架覆盖
import importlib.util as _importlib_util

_LINGAN_MODULE_PATH = os.environ.get(
    "LINGAN_SECURITY_GATE_PATH",
    str(Path(__file__).parent.parent.parent.parent / "lingan" / "security_gate.py"),
)
try:
    _spec = _importlib_util.spec_from_file_location("lingan_security_gate", _LINGAN_MODULE_PATH)
    _lingan_mod = _importlib_util.module_from_spec(_spec)
    _spec.loader.exec_module(_lingan_mod)
    _LingAn = _lingan_mod.LingAn
    _GateLayer = _lingan_mod.GateLayer
    _LINGAN_AVAIL = True
    _LINGAN_SINGLETON = _LingAn()
    logger.info("灵安 LingAn security_gate 可用, L10-A v0.2.1 已接入 Layer A")
except (ImportError, FileNotFoundError, Exception) as e:
    _LINGAN_AVAIL = False
    _LINGAN_SINGLETON = None  # type: ignore[assignment]
    _LingAn = None  # type: ignore[assignment]
    _GateLayer = None  # type: ignore[assignment]
    logger.info("灵安 LingAn 不可用, L10-A 跳过 Layer A 门禁 (graceful degrade): %s", e)


# === 数据结构 ===

@dataclass
class Declaration:
    """从模型输出中提取的执行性声明 (我做了 X)"""
    text: str                       # 原始声明文本
    pattern_id: str                 # 命中的模式 ID (notified/sent/created/...)
    action: str                     # 动作 (通知/发送/创建/...)
    target: str                     # 目标对象 (灵安/thread xxx/...)
    span: tuple[int, int] = (0, 0)  # 在原文中的位置


@dataclass
class DeclarationAuditResult:
    """单条声明的审计结果"""
    declaration: Declaration
    verified: bool = False          # verify_claim 是否通过
    matched_pattern: Optional[str] = None  # 灵极优命中的模式 ID
    fallback: Optional[str] = None  # 未验证原因: no_match/ambiguous_pattern/outside_window/unknown_member/lingan_rejected
    source_db: Optional[str] = None
    event: Optional[dict] = None    # 匹配到的 Datalog 事件
    error: str = ""                 # 调用异常
    lingan_gate_id: Optional[str] = None  # 灵安 LingAn.check 返回的 gate_id
    lingan_approved: bool = True    # 灵安 LingAn.evaluate 是否通过 (False=被拒)


@dataclass
class AuditResult:
    """整体审计结果 (audit_declarations 返回)"""
    passed: bool = False            # 所有声明都 verified=True 时为 True
    total: int = 0                  # 提取到的声明总数
    verified_count: int = 0         # 通过验证的声明数
    failed_count: int = 0           # 未通过验证的声明数
    results: list[DeclarationAuditResult] = field(default_factory=list)
    warning: str = ""
    alerted: bool = False           # 是否已向 LingBus 告警


# === 声明提取器 (继承 T1 ClaimExtractor, 复用灵极优 15 模式) ===

class DeclarationExtractor:
    """执行性声明提取器 - 复用灵极优 CLAIM_PATTERNS, 不自建第二套模式库.

    与 T1 ClaimExtractor 的区别:
    - T1 ClaimExtractor 提取陈述性 claim (X 是 Y), 查灵知 KG
    - 本类提取执行性声明 (我做了 X), 查 L6 Datalog

    模式来源: 灵极优 lingyuan.l10_trust_anchor.CLAIM_PATTERNS (15 个)
    灵极优不可用时回退到内置副本 (与灵极优保持 1:1 同步, 15 个).
    """

    @classmethod
    def _load_policy_patterns(cls) -> list[dict[str, Any]]:
        """从策略文件加载模式（YAML），失败回退空列表。

        P1-1: 走 PolicyLoader 统一加载，改 claim_patterns.yaml 后
        下次 L10-A 生效，进程不重启。
        """
        from lingclaude.core.policy_loader import get as policy_get

        data = policy_get("claim_patterns")
        patterns = data.get("patterns", [])
        return [p for p in patterns if isinstance(p, dict) and "regex" in p]

    # 内置副本 (与灵极优 CLAIM_PATTERNS 1:1 同步, 灵极优不可用时回退)

    _FALLBACK_PATTERNS: list[dict[str, Any]] = [
        {"pattern_id": "notified", "regex": r"已(?:通知|告知|通告)\s*(.+?)(?:,|。|$)",
         "event_type": "claim.notified", "action": "通知"},
        {"pattern_id": "sent", "regex": r"已(?:发送|投递|回复)\s*(.+?)(?:,|。|$)",
         "event_type": "claim.sent", "action": "发送"},
        {"pattern_id": "created", "regex": r"已(?:创建|建立|发起)\s*(.+?)(?:,|。|$)",
         "event_type": "claim.created", "action": "创建"},
        {"pattern_id": "modified", "regex": r"已(?:修改|更新|编辑|修正)\s*(.+?)(?:,|。|$)",
         "event_type": "claim.modified", "action": "修改"},
        {"pattern_id": "verified", "regex": r"已(?:确认|核实|验证|校对)\s*(.+?)(?:,|。|$)",
         "event_type": "claim.verified", "action": "确认"},
        {"pattern_id": "queried", "regex": r"已(?:查询|检索|读取|拉取)\s*(.+?)(?:,|。|$)",
         "event_type": "claim.queried", "action": "查询"},
        {"pattern_id": "executed", "regex": r"已(?:执行|调用|运行|部署)\s*(.+?)(?:,|。|$)",
         "event_type": "claim.executed", "action": "执行"},
        {"pattern_id": "emitted", "regex": r"已(?:写入|记录|emit)\s*(.+?)(?:,|。|$)",
         "event_type": "claim.emitted", "action": "写入"},
        {"pattern_id": "completed", "regex": r"已(?:完成|落地|通过)\s*(.+?)(?:,|。|$)",
         "event_type": "claim.completed", "action": "完成"},
        {"pattern_id": "deleted", "regex": r"已(?:删除|移除)\s*(.+?)(?:,|。|$)",
         "event_type": "claim.deleted", "action": "删除"},
        {"pattern_id": "checked", "regex": r"已(?:检查|审查|审计)\s*(.+?)(?:,|。|$)",
         "event_type": "claim.checked", "action": "检查"},
        {"pattern_id": "downloaded", "regex": r"已(?:下载|同步)\s*(.+?)(?:,|。|$)",
         "event_type": "claim.downloaded", "action": "下载"},
        {"pattern_id": "uploaded", "regex": r"已(?:上传|推送)\s*(.+?)(?:,|。|$)",
         "event_type": "claim.uploaded", "action": "上传"},
        {"pattern_id": "generated", "regex": r"已(?:生成|产出)\s*(.+?)(?:,|。|$)",
         "event_type": "claim.generated", "action": "生成"},
        {"pattern_id": "installed", "regex": r"已(?:安装|配置|设置)\s*(.+?)(?:,|。|$)",
         "event_type": "claim.installed", "action": "安装"},
    ]

    def __init__(self, patterns: list[dict[str, Any]] | None = None):
        """初始化.

        Args:
            patterns: 自定义模式库 (测试用). None 则优先用灵极优 CLAIM_PATTERNS,
                      灵极优不可用则用内置 _FALLBACK_PATTERNS.
        """
        if patterns is not None:
            self._patterns = patterns
        elif _L10B_AVAIL and _LMO_CLAIM_PATTERNS:
            self._patterns = _LMO_CLAIM_PATTERNS
        else:
            # 灵元：策略是 data —— 优先读策略文件 YAML，代码内副本仅作最终兜底
            yaml_patterns = self._load_policy_patterns()
            if yaml_patterns:
                self._patterns = yaml_patterns
                logger.info("灵极优不可用, L10-A 使用策略文件模式 (%d 个, claim_patterns.yaml)", len(yaml_patterns))
            else:
                self._patterns = self._FALLBACK_PATTERNS
                logger.warning("灵极优与策略文件均不可用, L10-A 使用内置模式副本 (15 个)")

        # 预编译正则
        self._compiled = [
            (p["pattern_id"], re.compile(p["regex"]), p.get("action", ""), p.get("event_type", ""))
            for p in self._patterns
        ]

    @property
    def pattern_count(self) -> int:
        return len(self._compiled)

    def extract(self, text: str) -> list[Declaration]:
        """从文本提取执行性声明.

        Args:
            text: 模型输出文本.

        Returns:
            去重后的 Declaration 列表 (按出现顺序).
        """
        declarations: list[Declaration] = []
        seen: set[str] = set()

        for pattern_id, compiled_re, action, _event_type in self._compiled:
            for m in compiled_re.finditer(text):
                raw_text = m.group(0)
                target = m.group(1).strip() if m.groups() else ""

                # 去重 (同一声明文本只保留首次)
                if raw_text in seen:
                    continue
                seen.add(raw_text)

                declarations.append(Declaration(
                    text=raw_text,
                    pattern_id=pattern_id,
                    action=action,
                    target=target,
                    span=(m.start(), m.end()),
                ))

        return declarations


# === 后置审计入口 ===

def _default_verifier(claim_text: str, claim_member: str,
                      db_instance: Optional[str] = None,
                      time_window_hours: float = 24.0) -> dict[str, Any]:
    """默认 verifier: 调灵极优 verify_claim.

    灵极优不可用时返回 mock (verified=False, fallback=no_match),
    这样审计会告警但不报错, 符合"不阻断"原则.
    """
    if not _L10B_AVAIL or _lmo_verify_claim is None:
        return {
            "verified": False,
            "event": None,
            "matched_pattern": None,
            "extracted": {"action": "", "target": ""},
            "source_db": db_instance,
            "fallback": "no_match",
        }
    return _lmo_verify_claim(
        claim_text=claim_text,
        claim_member=claim_member,
        db_instance=db_instance,
        time_window_hours=time_window_hours,
    )


def _lingan_check(claim_member: str, pattern_id: str) -> tuple[Optional[str], bool]:
    """灵安 Layer A 门禁校验 (v0.2.1 接入).

    对 claim.{pattern_id} 事件走 LingAn.check + evaluate.
    灵安不可用时 graceful degrade (返回 approved=True, gate_id=None).
    """
    if not _LINGAN_AVAIL or _LINGAN_SINGLETON is None:
        return (None, True)
    gate_id = None
    try:
        gate_id = _LINGAN_SINGLETON.check(
            gate_layer=_GateLayer.DATA,
            actor=claim_member,
            action="emit",
            target=f"claim.{pattern_id}",
        )
        _LINGAN_SINGLETON.evaluate(gate_id)
        return (gate_id, True)
    except PermissionError as e:
        logger.warning("LingAn 拒绝声明 [actor=%s pattern=%s]: %s", claim_member, pattern_id, e)
        return (gate_id, False)
    except Exception as e:
        logger.warning("LingAn 校验异常 [actor=%s pattern=%s]: %s", claim_member, pattern_id, e)
        return (None, True)


def audit_declarations(
    output: str,
    claim_member: str,
    verifier: Callable[..., dict[str, Any]] | None = None,
    extractor: DeclarationExtractor | None = None,
    db_instance: Optional[str] = None,
    time_window_hours: float = 24.0,
    alert_fn: Callable[[list[DeclarationAuditResult]], None] | None = None,
    lingan_gate: bool = True,
) -> AuditResult:
    """输出后审计: 提取声明 -> LingAn 门禁 -> verify_claim -> 不匹配告警, 不阻断."""
    if not claim_member:
        raise ValueError("claim_member 必填 (声明所属成员名)")

    verifier = verifier or _default_verifier
    extractor = extractor or DeclarationExtractor()
    alert_fn = alert_fn or _default_alert

    declarations = extractor.extract(output)

    if not declarations:
        return AuditResult(passed=True, total=0, verified_count=0,
                          failed_count=0, results=[], warning="", alerted=False)

    results: list[DeclarationAuditResult] = []
    for decl in declarations:
        gate_id, approved = (None, True)
        if lingan_gate:
            gate_id, approved = _lingan_check(claim_member, decl.pattern_id)

        if not approved:
            results.append(DeclarationAuditResult(
                declaration=decl, verified=False, fallback="lingan_rejected",
                source_db=db_instance, lingan_gate_id=gate_id, lingan_approved=False,
            ))
            continue

        try:
            vr = verifier(claim_text=decl.text, claim_member=claim_member,
                         db_instance=db_instance, time_window_hours=time_window_hours)
            results.append(DeclarationAuditResult(
                declaration=decl, verified=bool(vr.get("verified", False)),
                matched_pattern=vr.get("matched_pattern"), fallback=vr.get("fallback"),
                source_db=vr.get("source_db"), event=vr.get("event"),
                lingan_gate_id=gate_id, lingan_approved=True,
            ))
        except Exception as e:
            logger.warning("verify_claim 调用异常 (声明=%s): %s", decl.text[:40], e)
            results.append(DeclarationAuditResult(
                declaration=decl, verified=False, error=str(e),
                lingan_gate_id=gate_id, lingan_approved=True,
            ))

    # 3. 汇总
    verified_count = sum(1 for r in results if r.verified)
    failed_count = len(results) - verified_count
    failed_results = [r for r in results if not r.verified]

    # 4. 不匹配则告警 (不阻断)
    alerted = False
    if failed_results:
        try:
            alert_fn(failed_results)
            alerted = True
        except Exception as e:
            logger.error("L10-A 告警失败: %s", e)

    return AuditResult(
        passed=(failed_count == 0),
        total=len(results),
        verified_count=verified_count,
        failed_count=failed_count,
        results=results,
        warning=f"{failed_count}/{len(results)} 声明未通过 Datalog 验证" if failed_results else "",
        alerted=alerted,
    )


# === 告警通道 ===

def _default_alert(failed_results: list[DeclarationAuditResult]) -> None:
    """默认告警: 异步发 LingBus (fire-and-forget, 不阻塞调用方).

    告警失败不影响主流程 (L10-A 是"尽力而为"审计, 非强制约束).
    实际 LingBus 发送通过 mcp_ling-term-mcp_open_thread,
    但本函数在 lingclaude 进程内, 无法直接调 MCP,
    因此改为写告警日志 + 可选的 hook 回调.

    生产环境应通过 L10-D 操作门禁或灵信 hook 接入 LingBus.
    """
    for r in failed_results:
        logger.warning(
            "L10-A 声明未验证 [member=%s pattern=%s target=%s fallback=%s]: %s",
            "unknown",  # claim_member 在 alert_fn 签名中不可用, 由调用方日志补全
            r.declaration.pattern_id,
            r.declaration.target[:40],
            r.fallback,
            r.declaration.text[:80],
        )


def alert_to_lingbus(
    failed_results: list[DeclarationAuditResult],
    claim_member: str,
    thread_id: Optional[str] = None,
) -> Callable[[list[DeclarationAuditResult]], None]:
    """构造 LingBus 告警回调 (供 audit_declarations 的 alert_fn 参数).

    用法:
        alert_fn = alert_to_lingbus(failed, "lingclaude", thread_id="xxx")
        audit_declarations(output, "lingclaude", alert_fn=alert_fn)

    注意: 实际 LingBus 发送需通过 MCP 工具调用,
    本函数返回的闭包会记录告警意图到日志,
    由灵克主进程在合适时机 poll 并发送.

    Args:
        failed_results: 占位 (实际由闭包接收).
        claim_member: 声明所属成员.
        thread_id: 关联的 LingBus thread_id (可选).
    """
    def _alert(failed: list[DeclarationAuditResult]) -> None:
        for r in failed:
            logger.warning(
                "L10-A LingBus 告警待发 [member=%s thread=%s pattern=%s target=%s fallback=%s]: %s",
                claim_member,
                thread_id or "-",
                r.declaration.pattern_id,
                r.declaration.target[:40],
                r.fallback,
                r.declaration.text[:80],
            )
    return _alert


__all__ = [
    "Declaration",
    "DeclarationAuditResult",
    "AuditResult",
    "DeclarationExtractor",
    "audit_declarations",
    "alert_to_lingbus",
]
