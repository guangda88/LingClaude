"""sandbox_rules — 目录级细粒度沙箱规则解析（2026-10-02，灵元 C 项）。

需求出处：豆包/趋势评估「按目录、按操作类型做细粒度权限」。lc 现状：
bash 沙箱 default_writable_dirs 全局一刀切（/home/ai 全可写），文件工具
allowed_write_roots 默认不生效——缺「按项目目录收窄写面」的一层。

规则源：core/policies/sandbox_policy.yaml 的 directory_rules 段（PolicyLoader
热更，改 yaml 下条命令/下个写操作生效）：

    directory_rules:
      - scope: "/home/ai/lingclaude"   # cwd 命中该前缀 → 本规则生效
        writable: ["/home/ai/lingclaude", "/tmp"]
      default:                          # 无命中时的缺省（缺省段缺省值=现状等价）
        writable: ["/home/ai"]

语义（fail-safe 三条）：
  1. 段不存在/空/坏结构 → 规则层不激活（调用方回退旧逻辑，零行为变化）
  2. scope 匹配按 realpath 前缀，首个命中生效（列表序即优先级）
  3. 解析出的可写目录仍须过 is_safe_writable_dir 钳制（红线根/可信根不可绕）

消费方：
  - engine/bash.py _sandbox_command：extra_writable_dirs 按 cwd 取规则
  - engine/tool_handlers/file_tools.py _write_allowed：写路径必须落规则 writable 内
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_POLICY_NAME = "sandbox_policy"
_RULES_KEY = "directory_rules"


def rules_configured(rules: dict | None = None) -> bool:
    """directory_rules 段是否存在且非空（不存在=规则层不激活）。"""
    try:
        data = rules if rules is not None else _load_policy()
    except Exception:  # noqa: BLE001 — 策略读取故障 = 不激活
        return False
    if not isinstance(data, dict):
        return False
    sec = data.get(_RULES_KEY)
    return isinstance(sec, dict) and bool(sec)


def resolve_writable_dirs(cwd: str | os.PathLike | None = None) -> list[str]:
    """按 cwd 解析可写目录清单。规则未激活返回 []（调用方回退旧逻辑）。

    匹配：cwd realpath 对每条规则的 scope realpath 做前缀判定（目录边界含
    末尾 "/"），首个命中生效；无命中取 default 段。所有路径过
    is_safe_writable_dir 钳制（红线根/越可信根/不存在一律剔除）。
    """
    try:
        data = _load_policy()
    except Exception:  # noqa: BLE001
        return []
    if not rules_configured(data):
        return []
    sec = data.get(_RULES_KEY) or {}
    cwd_real = _realpath(str(cwd or os.getcwd()))

    chosen: list[str] = []
    for entry in sec.get("rules", []) or []:
        if not isinstance(entry, dict):
            continue
        scope = _realpath(str(entry.get("scope", "")))
        if not scope:
            continue
        if cwd_real == scope or cwd_real.startswith(scope.rstrip("/") + "/"):
            chosen = [str(p) for p in (entry.get("writable") or [])]
            break
    if not chosen:
        default = sec.get("default") or {}
        chosen = [str(p) for p in (default.get("writable") or [])]
    # 共享钳制原语（红线根/可信根/存在性）——规则层不是信任根，只是选择器
    from lingclaude.lacp.sandbox_policy import is_safe_writable_dir

    return [p for p in chosen if is_safe_writable_dir(p)]


def check_write_allowed(path: str, cwd: str | os.PathLike | None = None) -> str | None:
    """文件写校验入口：规则激活且路径越出 writable → 返回拒绝理由；否则 None。

    规则未激活（段缺失）恒 None——向后兼容（零行为变化）。
    """
    if not rules_configured():
        return None
    writable = resolve_writable_dirs(cwd)
    if not writable:
        return None
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError):
        return f"路径无法解析: {path}"
    for root in writable:
        rp = Path(root)
        if resolved == rp or resolved.is_relative_to(rp):
            return None
    return (
        f"路径不在 directory_rules writable 白名单内: {resolved}"
        f"（当前目录规则允许: {writable}；调整见 core/policies/sandbox_policy.yaml）"
    )


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
