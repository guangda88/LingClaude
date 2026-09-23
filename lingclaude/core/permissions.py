from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import os as _os

logger = logging.getLogger(__name__)

READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "read", "grep", "glob", "ls", "find", "head", "tail",
    "cat", "view", "search", "list", "stat", "wc",
    # 2026-09-18 补齐新命名体系只读工具（精确匹配语义，缺名即灰区误伤）：
    # read_file/list_directory/web_search 是工具注册表真实名；todo_write 为
    # todo_tools.py 注册名（非 "todowrite"）；code_review/recall 为无副作用工具。
    "read_file", "list_directory", "web_search", "todo_write",
    "code_review", "recall",
})

# R5 阶段2:resume 时需二次确认的副作用工具（与 READ_ONLY_TOOLS 互斥，显式列出防误降级）。
SIDE_EFFECT_TOOLS: frozenset[str] = frozenset({
    "write", "edit", "bash", "rm", "mv", "cp",
    "curl", "wget", "ssh", "git", "python", "apply_patch",
    "request_user_input", "plan_mode",
})

# ── H1 (2026-09-15): 动作闸门常量单源（guard 引用别名；只读真源=READ_ONLY_TOOLS）。
VALID_MODES: tuple[str, ...] = ("auto", "ask", "strict")
# ask 模式下待审批动作的落盘位置 (相对 CWD)
PENDING_LOG_PATH: Path = Path(".lingclaude") / "guard_pending.jsonl"

# 硬拒绝动作 — 任何模式都不放行
DENY_ACTIONS: frozenset[str] = frozenset({
    "rm_rf_root", "sudo", "format_disk", "shutdown",
})

# reason 常量 — 管线按字符串匹配分流, 不要散落裸字符串
REASON_DENIED = "denied_by_rule"
REASON_EMPTY = "empty_action"
REASON_READ_ONLY = "read_only_auto"
REASON_AUTO = "auto_mode_pass"
REASON_NEED_APPROVAL = "requires_approval"
REASON_PENDING = "pending approval"

@dataclass(frozen=True)
class PermissionContext:
    deny_names: frozenset[str] = field(default_factory=frozenset)
    deny_prefixes: tuple[str, ...] = ()
    auto_approve_names: frozenset[str] = field(default_factory=lambda: READ_ONLY_TOOLS)
    # T1-2: permission mode — auto(非deny全放行含写) / ask(写需审批,默认) / strict(非auto_approve需审批)
    mode: str = "ask"

    @classmethod
    def from_config(
        cls,
        deny_tools: list[str] | None = None,
        deny_prefixes: list[str] | None = None,
        auto_approve: list[str] | None = None,
        mode: str = "ask",
    ) -> "PermissionContext":
        auto = READ_ONLY_TOOLS
        if mode == "auto":
            # auto 模式：除 deny 外全部放行 — auto_approve 集合无意义，放空交由 requires_approval 处理
            auto = frozenset()
        elif auto_approve is not None:
            auto = frozenset(name.lower() for name in auto_approve) | READ_ONLY_TOOLS
        return cls(
            deny_names=frozenset(name.lower() for name in (deny_tools or [])),
            deny_prefixes=tuple(prefix.lower() for prefix in (deny_prefixes or [])),
            auto_approve_names=auto,
            mode=mode,
        )

    def blocks(self, tool_name: str) -> bool:
        lowered = tool_name.lower()
        return lowered in self.deny_names or any(lowered.startswith(prefix) for prefix in self.deny_prefixes)

    def blocks_with_rule(self, tool_name: str) -> tuple[bool, str]:
        """5a：返回 (是否拦截, rule_id)。用于让调用方拿到结构化 rule_id 而非 bool。

        rule_id 是熔断 key 喂给 R5b（_ToolLoopDetector 按 rule_id 熔断）。
        """
        lowered = tool_name.lower()
        if lowered in self.deny_names:
            return True, "config.deny_tools.exact"
        for prefix in self.deny_prefixes:
            if lowered.startswith(prefix):
                return True, "config.deny_prefixes.prefix"
        return False, "no_match"

    def is_auto_approved(self, tool_name: str) -> bool:
        lowered = tool_name.lower()
        if self.blocks(lowered):
            return False
        if self.mode == "auto":
            # auto 模式：非 deny 工具全部放行
            return True
        return lowered in self.auto_approve_names

    def requires_approval(self, tool_name: str) -> bool:
        if self.blocks(tool_name):
            return True
        return not self.is_auto_approved(tool_name)

    # ── H1 (2026-09-15): 动作级统一闸门 — ApprovalGuard.check 的语义收敛入口。
    # 语义与 guard.check 完全一致：空/deny 硬拒、只读放行、auto 全放、
    # strict 需审批（不落盘）、ask 落盘 pending 后返回 (False, REASON_PENDING)。
    def check_action(self, action: str, params: dict | None = None) -> tuple[bool, str]:
        """判定单个动作是否放行，语义与 ApprovalGuard.check 对齐（H1 单源）。

        返回 (allowed, reason):
          (True,  REASON_READ_ONLY)      只读动作, 任意模式放行
          (True,  REASON_AUTO)           auto 模式放行
          (False, REASON_PENDING)        ask 模式写动作: 已落盘待审批(params 一并记录)
          (False, REASON_NEED_APPROVAL)  strict 模式写动作: 需审批, 不落盘
          (False, REASON_DENIED)         命中硬拒绝名单
          (False, REASON_EMPTY)          空 action, fail-closed
        """
        if not action or not action.strip():
            return (False, REASON_EMPTY)

        lowered = action.strip().lower()

        if lowered in DENY_ACTIONS:
            return (False, REASON_DENIED)

        if lowered in READ_ONLY_TOOLS:
            return (True, REASON_READ_ONLY)

        # P0-2 接线（2026-09-22）：approval_matrix 上层裁决面。
        # 门禁双通道：env LINGCLAUDE_APPROVAL_MATRIX=1 启用（默认关，零行为分叉）；
        # 沙箱/策略档位可由 LINGCLAUDE_SANDBOX_MODE / LINGCLAUDE_APPROVAL_POLICY 覆盖。
        # 裁决语义：asset/只读/auto 放行 → 采信；capability/policy 拒绝 → 静默拒；
        # 需审批 → 落回下方 ask/strict 原语义（pending 落盘链路不绕开）。
        matrix = _get_approval_matrix()
        if matrix is not None:
            v = matrix.decide(lowered)
            if v.allowed:
                return (True, v.reason)
            if v.reason in (REASON_NEED_APPROVAL, REASON_PENDING):
                pass  # 需审批 → 走原 ask/strict 语义（pending 落盘链路不绕开）
            else:
                return (False, v.reason)

        if self.mode == "auto":
            return (True, REASON_AUTO)

        if self.mode == "strict":
            return (False, REASON_NEED_APPROVAL)

        # ask: 记录到待审批日志, 返回 pending approval
        log_pending_action(lowered, params, mode=self.mode)
        return (False, REASON_PENDING)

    def filter_tools(self, tools: tuple[object, ...], name_getter: object = None) -> tuple[object, ...]:
        result = []
        for tool in tools:
            name = name_getter(tool) if name_getter else getattr(tool, "name", str(tool))
            if not self.blocks(name):
                result.append(tool)
        return tuple(result)

    def with_allow(self, tool_name: str) -> "PermissionContext":
        """T0-3: 返回新的 PermissionContext，将 tool_name 加入 auto_approve。

        同时从 deny_names 移除 — 显式审批决策覆盖静态/历史拒绝（否则 deny 后永远无法翻案）。
        """
        lowered = tool_name.lower()
        return PermissionContext(
            mode=self.mode,
            deny_names=self.deny_names - {lowered},
            deny_prefixes=self.deny_prefixes,
            auto_approve_names=self.auto_approve_names | {lowered},
        )

    def with_deny(self, tool_name: str) -> "PermissionContext":
        """T0-3: 返回新的 PermissionContext，将 tool_name 加入 deny"""
        new_deny = self.deny_names | {tool_name.lower()}
        return PermissionContext(
            mode=self.mode,
            deny_names=new_deny,
            deny_prefixes=self.deny_prefixes,
            auto_approve_names=self.auto_approve_names - {tool_name.lower()},
        )


class PermissionStore:
    """T0-3: 可变的权限上下文 — 支持审批回路回灌"""

    def __init__(self, initial: PermissionContext | None = None):
        self._ctx = initial or PermissionContext()
        self._history: list[tuple[str, str]] = []  # (tool_name, decision)
        self._explicit_allows: set[str] = set()  # 显式放行过的工具（区别于 read-only 默认放行）

    @property
    def context(self) -> PermissionContext:
        return self._ctx

    def record_approval(self, tool_name: str, decision: str) -> None:
        """记录审批决策并更新权限上下文"""
        self._history.append((tool_name, decision))
        if decision in ("allow", "always_allow", "allow_persist"):
            self._ctx = self._ctx.with_allow(tool_name)
            self._explicit_allows.add(tool_name.lower())
        elif decision == "deny":
            self._ctx = self._ctx.with_deny(tool_name)
            self._explicit_allows.discard(tool_name.lower())

    def explicitly_allowed(self, tool_name: str) -> bool:
        """是否有过显式放行决策（敏感路径门等 fail-closed 机制的逃生门）。"""
        return tool_name.lower() in self._explicit_allows

    def blocks(self, tool_name: str) -> bool:
        return self._ctx.blocks(tool_name)

    def requires_approval(self, tool_name: str) -> bool:
        return self._ctx.requires_approval(tool_name)

    def history(self) -> list[tuple[str, str]]:
        return list(self._history)

    def context_with_allow(self, tool_name: str) -> None:
        """仅放开权限上下文（不记入显式会话审批）— 持久化放行预载专用。

        工具级持久放行不应绕过 sensitive_path_gate 的 fail-closed：
        敏感路径要求本会话级显式审批，否则 allow_persist 一次 = 凭据路径永久失守。
        """
        self._ctx = self._ctx.with_allow(tool_name)


# ── T0-3: 会话级审批回路 — webUI /permission 决策 → 工具执行实时生效 ──
# api.py /permission 写入；CodingRuntime.execute_tool / sensitive_path_gate 读取。
# 进程内单例按 session_id 隔离；allow_persist 额外落 JSON 跨会话生效。

_STORES: dict[str, PermissionStore] = {}
_STORES_LOCK = threading.Lock()
_PERSIST_PATH = Path(__file__).resolve().parent.parent / "data" / "approvals.json"
_PERSISTED_TOOLS: set[str] = set()
# T1-2: 全局 permission mode — auto/ask/strict（webUI 可读可设，落同一 JSON）
_GLOBAL_MODE: str = "ask"

# P0-2 接线（2026-09-22）：approval_matrix 裁决面单例（env 门禁，默认关）。
_APPROVAL_MATRIX_SINGLETON: Any | None = None


def _get_approval_matrix() -> Any | None:
    """P0-2 接线（2026-09-22）：approval_matrix 裁决面获取（env 门禁，默认关）。

    env LINGCLAUDE_APPROVAL_MATRIX=1 时构建单例矩阵（档位可由
    LINGCLAUDE_SANDBOX_MODE / LINGCLAUDE_APPROVAL_POLICY 覆盖，资产读
    approvals.json always_allow）；未启用/构建失败 → None（调用方走原语义）。
    """
    if _os.environ.get("LINGCLAUDE_APPROVAL_MATRIX", "") not in ("1", "true", "TRUE"):
        return None
    global _APPROVAL_MATRIX_SINGLETON
    if _APPROVAL_MATRIX_SINGLETON is not None:
        return _APPROVAL_MATRIX_SINGLETON
    try:
        from lingclaude.core.approval_matrix import (
            ApprovalMatrix, ApprovalPolicy, SandboxMode,
        )
        sandbox = SandboxMode(
            _os.environ.get("LINGCLAUDE_SANDBOX_MODE", SandboxMode.WORKSPACE_WRITE.value)
        )
        policy = ApprovalPolicy(
            _os.environ.get("LINGCLAUDE_APPROVAL_POLICY", ApprovalPolicy.ON_FAILURE.value)
        )
        matrix = ApprovalMatrix(
            sandbox=sandbox, policy=policy,
            always_allow=ApprovalMatrix.load_assets(),
        )
        _APPROVAL_MATRIX_SINGLETON = matrix
        return matrix
    except Exception:  # noqa: BLE001 — 矩阵不可用 → 原语义（fail-soft）
        logger.debug("approval_matrix 构建失败，走原 check_action 语义", exc_info=True)
        return None
# 2026-09-22: 跨进程热更 —— 盘上 approvals.json 被外部进程（API server / 手改）
# 写入后，本进程经 _maybe_reload_mode 在下一次 get_permission_mode 时自动捡起。
# mtime 节流：每秒 toolbar 快照调用也只 stat 一次，值变才解析 JSON（µs 级开销）。
_LAST_PERSIST_MTIME: float = 0.0


def get_permission_mode() -> str:
    """T1-2 深化: 读取当前全局 permission mode（含跨进程热更探测）。"""
    _maybe_reload_mode()
    return _GLOBAL_MODE


def _maybe_reload_mode() -> None:
    """跨进程热更：盘上 approvals.json 的 mode 变化则重载进 _GLOBAL_MODE。

    与 _load_persisted 同一解析语义（非法值忽略，fail-closed 不提权）；
    set_permission_mode 落盘后同步 _LAST_PERSIST_MTIME 基线，不把自己的
    写入误判为外部变更。stat/解析失败静默保留内存值——热更是增强路径，
    不反噬权限门控。
    """
    global _LAST_PERSIST_MTIME, _GLOBAL_MODE
    try:
        mtime = _PERSIST_PATH.stat().st_mtime
    except OSError:
        return
    if mtime == _LAST_PERSIST_MTIME:
        return
    _LAST_PERSIST_MTIME = mtime
    try:
        data = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
        mode = data.get("mode")
        if mode in VALID_MODES and mode != _GLOBAL_MODE:
            _GLOBAL_MODE = mode
            logger.info("permission mode 跨进程热更: %s", mode)
    except Exception:  # noqa: BLE001 — 解析损坏时保留内存值
        pass


def set_permission_mode(mode: str) -> bool:
    """T1-2 深化: 设置全局 permission mode（auto/ask/strict），持久化落盘。

    Returns:
        True 设置成功；False 参数非法（保持原值）。
    """
    global _GLOBAL_MODE
    normalized = mode.lower()
    if normalized not in ("auto", "ask", "strict"):
        logger.warning("无效 permission mode: %s（允许: auto/ask/strict）", mode)
        return False
    with _STORES_LOCK:
        _GLOBAL_MODE = normalized
        _save_persisted()
        # 同步 mtime 基线：本次落盘是本进程所为，非外部变更（免得多解析一次）
        try:
            _LAST_PERSIST_MTIME = _PERSIST_PATH.stat().st_mtime
        except OSError:
            pass
    logger.info("permission mode 已切换: %s", normalized)
    return True


def _load_persisted() -> None:
    global _GLOBAL_MODE  # 2026-09-15 修复: 缺此声明，赋值成局部变量，mode 永远停在默认 ask
    mode_from_persist: str | None = None
    try:
        if _PERSIST_PATH.exists():
            data = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
            _PERSISTED_TOOLS.update(str(t).lower() for t in data.get("always_allow", []))
            if data.get("mode") in ("auto", "ask", "strict"):
                _GLOBAL_MODE = data["mode"]
                mode_from_persist = data["mode"]
    except Exception as e:  # noqa: BLE001 — 持久化文件损坏不应阻塞启动
        logger.warning("approvals.json 读取失败: %s", e)
    if mode_from_persist is None and _GLOBAL_MODE == "ask":
        # 2026-09-17 修复: 仅当 approvals.json 未显式设置 mode 时才读 config.yaml
        # （原条件 `if _GLOBAL_MODE == "ask"` 会把 approvals.json 显式写的
        #  "mode": "ask" 也覆盖掉 — 注释声称 approvals.json 优先级更高，
        #  实现却相反）。兜底读 config 的调用点延后至模块尾部（见文件末尾）。

        _GLOBAL_MODE = load_approval_mode(
            Path(_os.environ.get("LINGCLAUDE_CONFIG") or "config.yaml")
        )


def _save_persisted() -> None:
    try:
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PERSIST_PATH.write_text(
            json.dumps({
                "mode": _GLOBAL_MODE,
                "always_allow": sorted(_PERSISTED_TOOLS),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001 — 持久化失败不影响内存决策
        logger.warning("approvals.json 写入失败: %s", e)


def log_pending_action(
    action: str,
    params: dict | None = None,
    mode: str | None = None,
    state: str = "pending",
) -> None:
    """把审批动作追加到 .lingclaude/guard_pending.jsonl（H1 单源）。

    原 guard._log_pending 迁移至此 — 灰区 escalate 与 PermissionContext.check_action
    共用同一落盘点。落盘失败不影响判定结果（只对日志 fail-open）。

    mode 参数：记录实际判定用的模式（默认取全局 _GLOBAL_MODE；
    daemon 等独立判定场景应显式传 ctx.mode，保证落盘可考）。

    state 参数（P0-N5, 2026-09-15）：
      - "pending"            灰区 escalate 的待审批记录（默认）
      - "approved_by_daemon" daemon 写配置成功后补录的已执行留痕
    与灰区形成「申请→审批→执行→留痕」闭环：同一条 guard_pending.jsonl 里，
    state=pending 是入口（申请），state=approved_by_daemon 是出口（执行），
    审批人可依据 params 字段核对两端的动作域一致性。
    """
    try:
        PENDING_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "mode": mode or _GLOBAL_MODE,
            "state": state,
        }
        # 2026-09-05 事故复盘: pending 只记 action 名导致被拦参数不可考 —
        # params 摘要必须随记录落盘, 审批人才有裁决依据。
        if params:
            record["params"] = params
        with PENDING_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


_CONFIG_MODE_PATH = ("guard", "approval_mode")


def load_approval_mode(config_path: Path | str | None = None) -> str:
    """从 config.yaml 读取 guard.approval_mode, 缺省按调用场景区分。

    权限语义 fail-closed（2026-09-17）：
      - config 存在但 guard.approval_mode 缺省 → "auto"（合法的部署选择）；
      - config 缺失/不可读/解析失败 → "ask"（fail-closed，异常状态不提权）；
      - 值非法（不在 VALID_MODES）→ "ask"（防配置手坏导致意外提权）。
    """
    if config_path is None:
        return "auto"
    p = Path(config_path)
    if not p.exists():
        return "ask"  # fail-closed: 配置缺失不等于授权 auto
    try:
        import yaml

        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        mode = raw
        for key in _CONFIG_MODE_PATH:
            mode = (mode or {}).get(key) if isinstance(mode, dict) else None
        if mode is None:  # guard 段缺失 → 合法默认 auto
            return "auto"
        normalized = str(mode).strip().lower()
        return normalized if normalized in VALID_MODES else "ask"  # 非法值 fail-closed
    except Exception:  # noqa: BLE001 — 解析损坏时回退 ask（不提权）
        return "ask"


def get_permission_store(session_id: str = "default") -> PermissionStore:
    """获取（或创建）会话级 PermissionStore 单例。

    新 store 创建时自动应用 allow_persist 的跨会话放行（仅作用于 blocks /
    is_auto_approved — 不标记 explicitly_allowed，敏感路径门要求本会话显式审批）。
    """
    with _STORES_LOCK:
        store = _STORES.get(session_id)
        if store is None:
            store = PermissionStore()
            for tool in _PERSISTED_TOOLS:
                store.context_with_allow(tool)
            _STORES[session_id] = store
        return store


# ── P0 通电第三支（2026-09-23）：审批前缀沉淀（资产化接线）────────────────
_SEDIMENT_MATRIX: Any = None  # 沉淀专用矩阵实例（与裁决面单例分离，避免档位耦合）


def _sediment_matrix() -> Any:
    """沉淀专用 ApprovalMatrix（惰性构建，不依赖裁决面 env 门禁）。

    资产化（写 approvals.json）与裁决（decide）解耦：裁决门禁关着也可沉淀；
    盘上资产等裁决面下次启用 / 新进程 load_assets 时生效。
    """
    global _SEDIMENT_MATRIX
    if _SEDIMENT_MATRIX is None:
        try:
            from lingclaude.core.approval_matrix import ApprovalMatrix  # 惰性导入，与裁决面同风格
            _SEDIMENT_MATRIX = ApprovalMatrix(always_allow=ApprovalMatrix.load_assets())
        except Exception:  # noqa: BLE001 — 沉淀失败不影响审批主流程
            logger.debug("sediment matrix 构建失败，本次跳过沉淀", exc_info=True)
    return _SEDIMENT_MATRIX


def sediment_approval_prefix(command: str, *, persist: bool = True) -> bool:
    """P0 通电（2026-09-23）：把一次人工批准的命令沉淀为可复用前缀资产。

    只在调用方明确给出 command 时沉淀；禁止以 tool_name 充当前缀
    （"allow bash" 会全量放行该工具，越权面不可接受）。
    返回是否实际沉淀。
    """
    prefix = (command or "").strip()
    if not prefix:
        return False
    m = _sediment_matrix()
    if m is None:
        return False
    try:
        m.record_approval(prefix, persist=persist)
        return True
    except Exception:  # noqa: BLE001 — best-effort，不阻塞审批主流程
        logger.debug("审批前缀沉淀失败", exc_info=True)
        return False


def record_permission_decision(session_id: str, tool_name: str, decision: str,
                               command: str = "") -> None:
    """审批决策入口 — api.py /permission 调用，实时回灌后续工具执行。

    P0 通电（2026-09-23）：decision ∈ {always_allow, allow_persist} 且带
    command 时，同步沉淀命令前缀资产（approval_matrix.record_approval →
    approvals.json，跨会话热更）。无 command 不沉淀（防 tool_name 全量放行）。
    """
    if not tool_name:
        return
    store = get_permission_store(session_id or "default")
    store.record_approval(tool_name, decision)
    if decision in ("always_allow", "allow_persist"):
        sediment_approval_prefix(command)
    if decision == "allow_persist":
        with _STORES_LOCK:
            if tool_name.lower() not in _PERSISTED_TOOLS:
                _PERSISTED_TOOLS.add(tool_name.lower())
                _save_persisted()


def reset_permission_stores() -> None:
    """清空会话 store 与持久化内存态（测试隔离用）。

    同时清 _PERSISTED_TOOLS，防止真实 approvals.json 的 always_allow
    污染测试会话（工具级持久放行不应影响测试断言）。
    """
    with _STORES_LOCK:
        _STORES.clear()
        _PERSISTED_TOOLS.clear()


# 导入期初始化 — 2026-09-17 移至模块尾部：_load_persisted() 兜底分支现
# 直接引用 load_approval_mode（本行之前已定义）。原位置（函数定义后立即
# 调用）使兜底成为死代码，config.yaml 的 guard.approval_mode 从不生效。
_load_persisted()
