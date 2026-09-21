# lingclaude/core/approval_matrix.py
"""P0-2（2026-09-21，全 15 家精读 §3.2）：codex 式审批矩阵 + 批准资产化。

对齐 openai/codex 的审批模型（codex-rs/core/src/exec_policy.rs + protocol）：
- **两维正交**：
  - 沙箱模式（能力边界）：ReadOnly / WorkspaceWrite / DangerFullAccess —— 决定"能不能写"
  - 审批策略（打扰边界）：Untrusted / OnFailure / Granular / Never —— 决定"要不要问人"
  - 正交后裁决：能力不够 → 直接拒绝（连问都不问，codex Forbidden 语义）；
    能力够但策略要求问 → 弹窗/落 pending（codex NeedsApproval 语义）；
    能力够且策略不问 → 放行（codex Skip 语义）。
- **granular 细粒度开关**（codex Granular{sandbox_approval, rules, ...}）：
  各开关为 False 时对应类别请求 **auto-reject**（自动拒绝，而非弹窗）——
  与现有 ask 模式"落 pending 等用户点"不同，是"策略性关闭=静默拒绝"。
- **批准资产化**（codex default.rules 热更）：一次人工"允许"某前缀/工具后，
  把 allow 写回规则文件（approvals.json 的 always_allow），下次同前缀直接放行
  —— 把一次性弹窗沉淀为可复用资产，治"同命令反复弹窗"。

与 permissions.py 的关系（不替换，只扩展）：
- permissions.PermissionContext.check_action 保留（旧语义锚点，H1 单源）；
- 本矩阵是**上层裁决面**：先问能力（sandbox_mode），再问打扰（approval_policy），
  裁决结果映射回 permissions 的 reason 常量复用，不新造字符串。
- 规则资产化写点 = permissions._PERSIST_PATH（approvals.json always_allow），
  与既有 allow_persist 语义对齐（复用同一存储，不造第二套）。

停层声明（铁律 2 细则 5）：
- 内核 = ApprovalMatrix（纯裁决函数，无 I/O，可全离线单测）
- 接缝 = decide() / record_approval() 协议
- 实现 = 单实现（approvals.json 持久化），预留更多 backend
边界纪律：矩阵裁决只读规则不写盘；写盘（资产化）走 record_approval 显式调用。
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from lingclaude.core.permissions import (
    READ_ONLY_TOOLS,
    SIDE_EFFECT_TOOLS,
    REASON_AUTO,
    REASON_DENIED,
    REASON_NEED_APPROVAL,
    REASON_PENDING,
    REASON_READ_ONLY,
    REASON_EMPTY,
)

logger = logging.getLogger(__name__)

RULES_PATH = Path("lingclaude/data/approvals.json")


class SandboxMode(str, Enum):
    """能力边界（codex SandboxMode）：决定"能不能写"。"""
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    DANGER_FULL = "danger_full_access"

    def can_write(self, tool_name: str) -> bool:
        """该工具在能力层是否被沙箱放行。

        能力边界语义（codex 对齐）：沙箱约束的是「写入动作」，不约束
        命令字符串本身。因此 READ_ONLY 只放行只读工具（其余拒绝）；
        WORKSPACE_WRITE / DANGER_FULL 对写/副作用工具放行。工具名含空格
        （复合命令）按首词归类，与 permissions.READ_ONLY_TOOLS 精确匹配
        语义保持一致——避免 'git commit' 这类复合命令被误判为写工具。
        """
        name = tool_name.lower().split()[0] if tool_name else ""
        if name in READ_ONLY_TOOLS:
            return True
        if self is SandboxMode.DANGER_FULL:
            return True
        if self is SandboxMode.WORKSPACE_WRITE:
            return True
        # READ_ONLY：非只读工具一律拒绝（能力层 Forbidden）
        return False


class ApprovalPolicy(str, Enum):
    """打扰边界（codex AskForApproval）：决定"要不要问人"。"""
    UNTRUSTED = "untrusted"        # 非白名单全问（默认，最打扰）
    ON_FAILURE = "on_failure"      # 只在失败/高危时问
    GRANULAR = "granular"         # 按细粒度开关分类裁决（关=auto-reject）
    NEVER = "never"               # 不问，能力够就放（最不打扰）


@dataclass(frozen=True)
class GranularFlags:
    """granular 策略的细粒度开关（codex Granular{...}）。

    任一开关为 False 时，对应类别的请求 auto-reject（不弹窗、静默拒绝）。
    """
    sandbox_approval: bool = True   # 沙箱内写动作是否允许弹窗（False=拒绝）
    rules: bool = True              # 前缀规则匹配类是否允许弹窗（False=拒绝）
    request_permissions: bool = True  # 模型主动申请权限是否弹窗（False=拒绝）

    @classmethod
    def default(cls) -> "GranularFlags":
        return cls()


@dataclass
class ApprovalVerdict:
    """矩阵裁决结果：复用 permissions reason 常量 + 裁决维度说明。"""
    allowed: bool
    reason: str
    dimension: str  # "capability" / "policy" / "asset"
    detail: str = ""

    @property
    def is_denied_by_capability(self) -> bool:
        return not self.allowed and self.dimension == "capability"

    @property
    def is_rejected_by_policy(self) -> bool:
        return not self.allowed and self.dimension == "policy"


class ApprovalMatrix:
    """codex 式审批矩阵：能力（沙箱）× 打扰（策略）正交裁决 + 批准资产化。

    用法：
        m = ApprovalMatrix(sandbox=SandboxMode.WORKSPACE_WRITE,
                           policy=ApprovalPolicy.GRANULAR,
                           granular=GranularFlags(sandbox_approval=False))
        v = m.decide("bash")
        # bash 是写工具、granular 关 sandbox_approval → auto-reject（静默拒绝）
        assert v.is_rejected_by_policy

    裁决优先级（codex 对齐）：
      0. 空动作 → fail-closed 拒绝
      1. 硬拒绝名单（DENY_ACTIONS）→ 拒绝（能力+策略都拒）
      2. 能力层：沙箱不允许 → 拒绝（Forbidden，连问都不问）
      3. 资产层：命中已批准 allow 前缀 → 放行（asset 命中，不弹窗）
      4. 打扰层：按策略裁决（never=放、untrusted/on_failure/granular=问或拒）
      5. 只读工具 → 恒放行（能力+打扰都放）
    """

    def __init__(
        self,
        sandbox: SandboxMode = SandboxMode.WORKSPACE_WRITE,
        policy: ApprovalPolicy = ApprovalPolicy.ON_FAILURE,
        granular: GranularFlags | None = None,
        always_allow: tuple[str, ...] = (),
    ) -> None:
        self.sandbox = sandbox
        self.policy = policy
        self.granular = granular or GranularFlags.default()
        self._always_allow = frozenset(a.lower() for a in always_allow)
        self._lock = threading.Lock()

    # ── 裁决 ──

    def decide(self, tool_name: str, *, on_failure: bool = False) -> ApprovalVerdict:
        """裁决单个工具调用。on_failure 标记当前是否处于"失败/高危"语境
        （on_failure 策略下只有 on_failure=True 才真正弹窗，否则静默放行）。"""
        from lingclaude.core.permissions import DENY_ACTIONS

        name = (tool_name or "").strip()
        lowered = name.lower()

        # 0. 空动作 fail-closed
        if not lowered:
            return ApprovalVerdict(False, REASON_EMPTY, "policy", "empty action")

        # 1. 硬拒绝名单
        if lowered in DENY_ACTIONS:
            return ApprovalVerdict(False, REASON_DENIED, "capability", f"deny list: {lowered}")

        # 2. 能力层：沙箱不允许 → 直接拒绝（不进入打扰层）
        if not self.sandbox.can_write(tool_name):
            return ApprovalVerdict(
                False, REASON_DENIED, "capability",
                f"sandbox={self.sandbox.value} 不允许 {lowered}",
            )

        # 3. 资产层：命中已批准 allow 前缀 → 放行（codex default.rules 热更效果）
        if self._match_asset(lowered):
            return ApprovalVerdict(True, REASON_AUTO, "asset", f"asset hit: {lowered}")

        # 5. 只读工具恒放（能力已放 + 打扰不问）
        if lowered in READ_ONLY_TOOLS:
            return ApprovalVerdict(True, REASON_READ_ONLY, "capability", "read-only")

        # 4. 打扰层：按策略裁决
        return self._decide_by_policy(lowered, on_failure)

    def _decide_by_policy(self, lowered: str, on_failure: bool) -> ApprovalVerdict:
        if self.policy is ApprovalPolicy.NEVER:
            return ApprovalVerdict(True, REASON_AUTO, "policy", "policy=never")

        if self.policy is ApprovalPolicy.ON_FAILURE:
            if on_failure:
                return ApprovalVerdict(False, REASON_NEED_APPROVAL, "policy", "on-failure 需审批")
            return ApprovalVerdict(True, REASON_AUTO, "policy", "on-failure 非高危静默放")

        if self.policy is ApprovalPolicy.GRANULAR:
            if not self.granular.sandbox_approval:
                return ApprovalVerdict(
                    False, REASON_DENIED, "policy",
                    "granular.sandbox_approval=False → auto-reject",
                )
            if not self.granular.rules:
                return ApprovalVerdict(
                    False, REASON_DENIED, "policy",
                    "granular.rules=False → auto-reject",
                )
            return ApprovalVerdict(False, REASON_PENDING, "policy", "granular 开 → 落 pending")

        # UNTRUSTED：非白名单全问
        return ApprovalVerdict(False, REASON_NEED_APPROVAL, "policy", "untrusted 需审批")

    def _match_asset(self, lowered: str) -> bool:
        """前缀匹配已批准资产（codex allow 前缀语义：'git commit' 批准后 'git commit -m x' 也放）。"""
        for prefix in self._always_allow:
            if lowered == prefix or lowered.startswith(prefix):
                return True
        return False

    # ── 批准资产化（codex default.rules 写回热更） ──

    def record_approval(self, prefix: str, *, persist: bool = True,
                        rules_path: Path | None = None) -> Path | None:
        """把一次人工批准沉淀为可复用资产：写入 always_allow（会话内立即生效，
        并可选持久化到 approvals.json 跨会话热更）。

        codex 对齐：append_amendment_and_update —— 批准后写回规则文件，
        下次同前缀直接 Allow，不再弹窗。
        """
        prefix = (prefix or "").strip().lower()
        if not prefix:
            return None
        with self._lock:
            self._always_allow = self._always_allow | {prefix}
            if persist:
                path = rules_path or RULES_PATH
                try:
                    self._persist_allow(prefix, path)
                    return path
                except OSError as e:
                    logger.warning("approval 资产化写盘失败（会话内仍生效）: %s", e)
                    return path
            return None

    @staticmethod
    def _persist_allow(prefix: str, path: Path) -> None:
        data: dict[str, Any] = {"mode": "ask", "always_allow": []}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {"mode": "ask", "always_allow": []}
        allow = list(dict.fromkeys(data.get("always_allow", [])))  # 去重保序
        if prefix not in allow:
            allow.append(prefix)
        data["always_allow"] = allow
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load_assets(cls, path: Path | None = None) -> tuple[str, ...]:
        """从 approvals.json 读回 always_allow（跨会话热更的读侧）。"""
        p = path or RULES_PATH
        if not p.exists():
            return ()
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return tuple(data.get("always_allow", []))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("审批资产读取失败: %s", e)
            return ()
