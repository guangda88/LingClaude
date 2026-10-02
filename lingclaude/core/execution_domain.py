"""最小执行域 capability（P2③，2026-10-02）——聚合显式化，不建第二套规则。

需求出处：external_review P2 finding「SeamRegistry 强化 {read_paths,
write_paths, net_allowlist, side_effect_class} 执行域对象，按成员/子agent
强制校验」。

设计裁定（三条红线）：
  1. 路径维规则源 = core/sandbox_rules.py（directory_rules），本模块不持有
     任何路径白名单数据——resolve_domain() 只调用只读函数聚合；
  2. 副作用维规则源 = core/policies/tool_auth_policy.yaml 四档裁决
     （tool_auth_hook.check_tool_call），本模块只回填裁决元数据；
  3. 网络维为本 finding 的真增量：sandbox_policy.yaml ``net_allowlist``
     段（缺省缺段 = 不激活 = 只观察零行为变化），按 scope 前缀选规则，
     域名精确或点前缀子域匹配（""非空条目非法）。

失败语义沿 sandbox_rules 三条 fail-safe：段缺失不激活、首个 scope 命中
生效、规则层不是信任根只是选择器（网络钳制点在 M 期 httpx AsyncClient
工厂点接线，本模块只提供查询面）。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_POLICY_NAME = "sandbox_policy"
_NET_KEY = "net_allowlist"


# ── 数据结构 ──


@dataclass
class ExecutionDomain:
    """聚合后的执行域视图（只读快照，不含任何规则原文）。"""

    scope: str                          # 命中的 scope（或 "default"）
    write_paths: list[str] = field(default_factory=list)   # 来自 sandbox_rules
    net_allowlist: tuple[str, ...] = ()                    # 本域允许的域名单
    net_rule_active: bool = False       # net_allowlist 段是否激活
    side_effect_meta: dict = field(default_factory=dict)   # 四档裁决元数据


# ── 网络维（真增量） ──


def net_rules_configured(data: dict | None = None) -> bool:
    """net_allowlist 段是否存在且非空（缺段 = 网络规则层不激活）。"""
    try:
        d = data if data is not None else _load_policy()
    except Exception:  # noqa: BLE001 — 策略读取故障 = 不激活
        return False
    if not isinstance(d, dict):
        return False
    sec = d.get(_NET_KEY)
    if not isinstance(sec, dict):
        return False
    # rules 或 default 任一存在即激活（default-only = 全域一条规则，合法形态）
    return bool(sec.get("rules") or sec.get("default"))


def resolve_net_allowlist(cwd: str | os.PathLike | None = None) -> tuple[str, ...]:
    """按 cwd 解析网络白名单。规则未激活返回 ()（调用方零行为变化）。

    匹配语义与 directory_rules 同构：cwd realpath 对 scope 做目录边界
    前缀判定，首个命中生效，无命中取 default 段；default 缺失 = 不限制
    （显式留空口，与「未配置不激活」一致）。
    """
    try:
        data = _load_policy()
    except Exception:  # noqa: BLE001
        return ()
    if not net_rules_configured(data):
        return ()
    sec = data.get(_NET_KEY) or {}
    cwd_real = _realpath(str(cwd or os.getcwd()))
    chosen: list[str] = []
    for entry in sec.get("rules") or []:
        if not isinstance(entry, dict):
            continue
        scope = _realpath(str(entry.get("scope", "")))
        if not scope:
            continue
        if cwd_real == scope or cwd_real.startswith(scope.rstrip("/") + "/"):
            chosen = [str(h) for h in (entry.get("allow") or [])]
            break
    if not chosen:
        default = sec.get("default") or {}
        chosen = [str(h) for h in (default.get("allow") or [])]
    return tuple(h for h in chosen if h)


def net_allowed(host: str, cwd: str | os.PathLike | None = None) -> bool | None:
    """域名是否被当前域放行。True/False=有裁决；None=规则未激活（不限制）。

    匹配：精确相等，或 ``example.com`` 放行其全部子域（``a.example.com``）。
    端口/协议不参与匹配（host 先剥 scheme/port/userinfo）。
    """
    allow = resolve_net_allowlist(cwd)
    if not allow:
        return None
    h = _norm_host(host)
    if not h:
        return False
    for entry in allow:
        e = _norm_host(entry)
        if not e:
            continue
        if h == e or h.endswith("." + e):
            return True
    return False


def _norm_host(host: str) -> str:
    h = str(host or "").strip().lower()
    if "://" in h:
        h = h.split("://", 1)[1]
    h = h.split("/", 1)[0].split("@", 1)[-1]
    # host:port 剥端口（仅单冒号；裸 IPv6 多冒号不动）
    if h.count(":") == 1:
        h = h.rsplit(":", 1)[0]
    if h and h[0] == ".":
        h = h[1:]
    return h


# ── 聚合入口 ──


def resolve_domain(
    cwd: str | os.PathLike | None = None,
    with_side_effect_meta: bool = False,
) -> ExecutionDomain:
    """聚合三规则源为显式执行域对象（全部只读引用，fail-soft）。

    with_side_effect_meta=True 时回填四档裁决元数据（数据源
    tool_auth_policy.yaml 的段结构，读取失败静默留空——元数据增强，
    绝不因它阻塞域解析）。
    """
    from lingclaude.core.sandbox_rules import rules_configured, resolve_writable_dirs

    domain = ExecutionDomain(scope="default")
    try:
        if rules_configured():
            domain.write_paths = resolve_writable_dirs(cwd)
    except Exception:  # noqa: BLE001 — 路径维读取失败不拖垮整个域视图
        logger.warning("resolve_domain: write_paths 聚合失败", exc_info=True)
    try:
        allow = resolve_net_allowlist(cwd)
        domain.net_allowlist = allow
        domain.net_rule_active = net_rules_configured()
        # scope 回显：命中规则或 default
        domain.scope = _hit_scope(cwd)
    except Exception:  # noqa: BLE001
        logger.warning("resolve_domain: net_allowlist 聚合失败", exc_info=True)
    if with_side_effect_meta:
        try:
            data = _load_policy()
            meta = data.get("side_effect_meta") or {}
            if isinstance(meta, dict):
                domain.side_effect_meta = dict(meta)
        except Exception:  # noqa: BLE001
            pass
    return domain


def _hit_scope(cwd: str | os.PathLike | None) -> str:
    """回显命中的 scope 名（仅展示用；匹配失败回落 "default"）。"""
    try:
        data = _load_policy()
        sec = data.get(_NET_KEY) or {}
        cwd_real = _realpath(str(cwd or os.getcwd()))
        for entry in sec.get("rules") or []:
            if not isinstance(entry, dict):
                continue
            scope = _realpath(str(entry.get("scope", "")))
            if scope and (cwd_real == scope
                          or cwd_real.startswith(scope.rstrip("/") + "/")):
                return str(entry.get("scope", scope))
    except Exception:  # noqa: BLE001
        pass
    return "default"


# ------------------------------------------------------------------ 内部

def _load_policy() -> dict:
    from lingclaude.core.policy_loader import get as policy_get

    data = policy_get(_POLICY_NAME) or {}
    return data if isinstance(data, dict) else {}


def _realpath(p: str) -> str:
    try:
        return str(Path(p).resolve())
    except (OSError, ValueError):
        return p
