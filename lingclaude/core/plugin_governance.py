"""plugin_governance — 插件治理：blocklist + install scope（M3，2026-10-02）。

外部 agent 联审第 3 条（atomcode hooks.json 先例 + cc/codex 共同形态）的 lc 落地。
裁剪（对账附录裁定）：不做 marketplace 分发体系——单机工具无分发需求，
治理面只做「禁运（blocklist）+ 落点约束（install scope）」两件。

三侧接入点（同一规则源 core/policies/plugin_governance.yaml，热更生效）：
  1. 用户 hook 装载（core/hook_registry.load_user_hooks）——文件名 blocklist
  2. 斜杠插件装载（cli/slash_plugin_loader）——文件名 blocklist
  3. 核心 manifest 插件（core/plugin_loader.load_plugin）——entry install scope

fail 纪律（三层，有意区分）：
  - 策略文件缺失 / enabled=false → 全放行（治理是 opt-in 增强，缺省零分叉）
  - check_* 正常返回 → 按裁决执行（blocklist/scope 命中 = 拒绝 + 告警）
  - check_* 自身抛异常 → 消费方（三侧 loader）统一 deny + 告警——
    治理已启用时守卫炸了绝不放行（fail-safe）；未启用时异常同样 deny
    无害（默认就放行，deny 只影响加载，重启即恢复）。
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from pathlib import Path

from lingclaude.core import policy_loader

logger = logging.getLogger(__name__)

_POLICY_NAME = "plugin_governance"


@dataclass(frozen=True)
class GovernanceVerdict:
    """单文件/单插件的治理裁决。"""

    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class _GovPolicy:
    """不可变策略快照（每次调用现取现用，天然热更；量小无缓存必要）。"""

    enabled: bool
    blocked_names: tuple[str, ...]                 # fnmatch 模式（对文件 stem / 插件名）
    blocked_exact: frozenset[str]                  # 无通配精确名单（O(1) 查）
    scope_rules: tuple[tuple[str, tuple[str, ...]], ...]  # (插件名, entry pattern 元组)
    scope_default: str                             # "allow" | "deny"


def load_policy() -> _GovPolicy:
    """从 policy_loader 取策略（热更复用）。缺失/空 → enabled=False 全放行。

    本函数不吞异常——策略基础设施故障向上抛，消费方按 fail-safe deny。
    """
    raw = policy_loader.get(_POLICY_NAME) or {}
    enabled = bool(raw.get("enabled", False))
    blocked = raw.get("blocklist", {}) or {}
    names = tuple(
        p for p in (blocked.get("plugin_names", []) or [])
        if isinstance(p, str) and p.strip()
    )
    exact = frozenset(p for p in names if not any(c in p for c in "*?["))
    globs = tuple(p for p in names if p not in exact)

    scopes_raw = raw.get("install_scopes", {}) or {}
    default = str(scopes_raw.get("default", "allow")).strip().lower()
    if default not in ("allow", "deny"):
        default = "deny"  # 语义外的 default 值按收紧处理
    rules: list[tuple[str, tuple[str, ...]]] = []
    for r in raw.get("scope_rules", []) or []:
        if not isinstance(r, dict):
            continue
        pname = str(r.get("plugin", "")).strip()
        pats = tuple(
            p for p in (r.get("entry_patterns", []) or [])
            if isinstance(p, str) and p.strip()
        )
        if pname:
            rules.append((pname, pats))
    return _GovPolicy(
        enabled=enabled, blocked_names=globs, blocked_exact=exact,
        scope_rules=tuple(rules), scope_default=default,
    )


def deny_on_error(fn, *args) -> GovernanceVerdict:
    """消费方统一包装：治理检查异常 → deny（fail-safe）。"""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 — 守卫异常不放行
        logger.warning("plugin_governance: 检查异常，拒绝装载（fail-safe）: %s", exc)
        return GovernanceVerdict(False, f"治理检查异常: {exc}")


# ── blocklist（对用户落点插件文件名）─────────────────────────────

def check_file_allowed(path: Path) -> GovernanceVerdict:
    """用户落点插件（hook/slash 文件）治理：blocklist 命中 → 拒绝。

    scope 不约束用户落点（用户 HOME 本来就是声明的安装区），只做禁运。
    """
    pol = load_policy()
    if not pol.enabled:
        return GovernanceVerdict(True, "治理未启用")
    stem = path.stem
    if stem in pol.blocked_exact or any(
        fnmatch.fnmatch(stem, pat) for pat in pol.blocked_names
    ):
        return GovernanceVerdict(False, f"blocklist 命中（{stem}）")
    return GovernanceVerdict(True, "")


# ── install scope（对 core manifest 插件 entry 路径）─────────────

def check_manifest_entry(plugin_name: str, entry: str) -> GovernanceVerdict:
    """manifest 插件治理：先 blocklist（插件名），再 scope（entry 路径）。

    scope 语义：有该插件专属 scope_rules → entry 必须命中任一 pattern
    （fnmatch 对 entry 原文，直观可写 "core/plugins/*"）；无专属规则 →
    install_scopes.default。pattern 非法 → 拒绝（fail-safe 收紧）。
    """
    pol = load_policy()
    if not pol.enabled:
        return GovernanceVerdict(True, "治理未启用")

    if plugin_name in pol.blocked_exact or any(
        fnmatch.fnmatch(plugin_name, pat) for pat in pol.blocked_names
    ):
        return GovernanceVerdict(False, f"blocklist 命中（{plugin_name}）")

    rule_pats: tuple[str, ...] | None = None
    for pname, pats in pol.scope_rules:
        if pname == plugin_name:
            rule_pats = pats
            break

    if rule_pats is None:
        if pol.scope_default == "deny":
            return GovernanceVerdict(
                False, f"install_scopes.default=deny 且无 {plugin_name} 专属规则"
            )
        return GovernanceVerdict(True, "")

    for pat in rule_pats:
        if fnmatch.fnmatch(entry, pat):
            return GovernanceVerdict(True, "")
    # 有规则但全不中 → 拒绝（含规则写了但 pattern 全非法的情况——
    # fnmatch 不会抛，坏 pattern 表现为不命中，天然收紧，无需特判）
    return GovernanceVerdict(False, f"entry 越出 install scope: {entry!r}")
