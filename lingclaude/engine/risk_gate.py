"""工具调用风险门控（NanoJev 契约消费层消费点⑩，2026-09-22）。

对齐 NanoJev/Jev 工具调用风险门控（PreToolUse <1ms 放行/询问/拒绝）：工具执行前
对拟调用的命令/参数做**三原语风险分级**——Score 头判风险等级（只读/修改本地/
破坏性/网络/生产敏感），Choice 头判处置决策（放行/询问/拒绝），高频有界判断
下沉 0 LLM token，循环控制流可测量可审计。

设计（对齐 lc 红线：能模式匹配解决的不用模型）：
- 主路径 = 本地风险信号先验（0 模型调用）：
  - 只读（read-only leads）→ 等级 1，处置放行
  - 修改本地（写/装/构建，有副作用）→ 等级 2
  - 破坏性（rm -rf / force / drop / 不可逆删除）→ 等级 3
  - 网络（curl/wget 非查询形态、发数据）→ 等级 4
  - 生产敏感（写生产库/部署/凭据操作）→ 等级 5
- 复用 sensitive_path_gate 现成判定（_split_command_chain / BASH_READONLY_LEADS /
  _is_curl_query_only / SENSITIVE_MARKERS），不重复造轮子。
- Choice 头 = 按风险等级 + 当前权限模式（auto/ask/strict）定处置决策；
  spec_decision 兜底 = 灰区命令（既非只读也非明显破坏）走 boolean 头
  P(破坏性) 反向确认（spec_decision_enabled 时）。
- fail-open = 门控故障 → 不分级（返回中性，交既有 sensitive_path_gate / 权限链
  处理），不反噬工具执行本身。

停层声明（铁律 2 细则 5）：
- 内核 = classify_command_risk(command) -> RiskVerdict（纯判定无 I/O）
- 接缝 = 风险信号正则 + 处置决策协议（等级→allow/ask/deny）
- 实现 = 单实现（本地先验 + 可选 spec_decision 兜底）
边界纪律：风险分级是**叠加提醒/询问信号**（不替换既有 sensitive_path_gate 拦截），
只读放行零打扰、破坏性/生产敏感触发询问或拒绝；主路径 0 模型调用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# 破坏性命令信号（等级 3：不可逆删除/强制覆盖）
_DESTRUCTIVE_RES = (
    re.compile(r"\brm\s+(-[a-z]*[rf][a-z]*\s+)+(-[a-z]*[rf][a-z]*\s+)*(/|~|\.)", re.I),
    re.compile(r"\brm\s+-[a-z]*f.*\s+(-[a-z]*r|\*)\b", re.I),
    re.compile(r"\bgit\s+(push\s+.*--force|clean\s+-[a-z]*f|reset\s+--hard)\b"),
    re.compile(r"\b(drop\s+(database|table)|truncate\s+table)\b", re.I),
    re.compile(r"\b(shred|wipe)\b", re.I),
    re.compile(r">\s*/dev/[sh]da", re.I),
)
# 生产/部署敏感信号（等级 5：写生产库/部署/凭据/网络写）
_PRODUCTION_RES = (
    re.compile(r"\b(migrate\s+.*--?prod|deploy\s+.*prod|kubectl\s+(delete|apply|rollout|exec))\b", re.I),
    re.compile(r"\b(migrate|rollback)\b.*\bprod(uction)?\b", re.I),
    re.compile(r"(ssh|scp)\s+\S+.*(rm|drop|deploy|kill)", re.I),
    re.compile(r"\bpromote\s+.*production\b", re.I),
    re.compile(r"(systemctl\s+(stop|disable)|service\s+\S+\s+stop)\s+\S*(db|prod|mysql|postgres)", re.I),
)
# 网络非查询（等级 4：curl/wget 写/发数据形态）
_NETWORK_RES = (
    re.compile(r"\b(curl|wget)\b.*(-d|--data|-F|--form|-T|--upload|PUT|POST)\b", re.I),
    re.compile(r"\b(curl|wget)\b.*(-o|--output|-O|--output-document)", re.I),
    re.compile(r"\b(ssh|scp|nc|ncat)\b", re.I),
)
# 本地修改（等级 2：写/装/构建，有副作用但可恢复）
_LOCAL_WRITE_RES = (
    re.compile(r"\b(pip3?\s+install|npm\s+(install|i|add)|cargo\s+(install|build)|go\s+install)\b", re.I),
    re.compile(r"\b(mkdir|touch|mv|cp|ln)\b", re.I),
    re.compile(r"\b(write|edit|append|truncate)\b.*\.\w+", re.I),
    re.compile(r">\s*\S+|>>\s*\S+", re.I),  # 重定向写
    re.compile(r"\b(make|pytest|npm\s+run|cargo\s+(test|run|build))\b", re.I),
    re.compile(r"\bchmod\s+\+x|chown\b", re.I),
    re.compile(r"\bsudo\b"),
)
# 破坏性/不可逆关键词补充（rm 非递归但删文件、git reset 丢工作区）
_IRREVERSIBLE_EXTRA = (
    re.compile(r"\brm\s+\S+\.(py|rs|ts|go|js|sh|json|yaml|toml)\b", re.I),
    re.compile(r"\bgit\s+checkout\s+--?\s*\.", re.I),
)


@dataclass
class RiskVerdict:
    """工具调用风险分级判定。"""
    risk_level: int          # 1 只读 / 2 本地修改 / 3 破坏性 / 4 网络 / 5 生产敏感
    risk_label: str         # 人类可读标签
    action: str             # allow / ask / deny（按权限模式 + 等级定）
    reason: str = ""        # 命中信号（供结果面/日志）
    source: str = "local"   # local / noul_fallback / neutral

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "risk_label": self.risk_label,
            "action": self.action,
            "reason": self.reason,
            "source": self.source,
        }


# 等级标签
_LEVEL_LABELS = {1: "read_only", 2: "local_write", 3: "destructive",
                 4: "network", 5: "production_sensitive"}


def classify_command_risk(
    command: str,
    *,
    permission_mode: str = "auto",
    tool_name: str = "bash",
) -> RiskVerdict:
    """对一条拟执行命令做风险分级 + 处置决策（主路径 0 模型调用）。

    permission_mode（auto/ask/strict）调制处置决策：
    - 只读（等级 1）：三模式都放行（零打扰）。
    - 本地修改（等级 2）：auto 放行、ask 询问、strict 询问。
    - 破坏性（等级 3）/网络（4）/生产敏感（5）：auto 询问、ask 询问、
      strict 拒绝（fail-closed）。
    灰区（无法归级）→ spec_decision 兜底（启用时）；未启用/故障 → 按权限
    模式保守处置（fail-open 不武断拦，交既有权限链）。
    """
    cmd = (command or "").strip()
    if not cmd:
        return RiskVerdict(1, _LEVEL_LABELS[1], "allow", "empty command", "neutral")

    # 非 bash 类工具：按工具域粗分（写类=2，读类=1，exec 类按命令分）
    if tool_name not in ("bash", "bash_lingxi", "execute"):
        return _classify_non_bash(tool_name, permission_mode)

    # 逐子命令判定，取最高风险（复合命令任一破坏性即整条破坏性）
    from lingclaude.engine.sensitive_path_gate import _split_command_chain
    subcmds = _split_command_chain(cmd) or [cmd]

    level = 1
    hit_reasons: list[str] = []
    for sub in subcmds:
        sub_level, reason = _risk_of_subcommand(sub)
        if sub_level > level:
            level, hit_reasons = sub_level, [reason]
        elif sub_level == level and sub_level > 1:
            hit_reasons.append(reason)

    reason = "；".join(hit_reasons[:3]) if hit_reasons else "read-only leads"
    # 灰区（等级仍为 1 但命令非纯只读读法）→ spec_decision 兜底
    if level == 1 and _is_gray(cmd, subcmds):
        fb = _gray_risk_gate(cmd, permission_mode)
        if fb is not None:
            return fb
    return _decide(level, permission_mode, reason)


def _risk_of_subcommand(sub: str) -> tuple[int, str]:
    """单条子命令风险等级（取命中信号的最高级）。"""
    sub = sub.strip()
    # 生产敏感（最高，等级 5）
    for rx in _PRODUCTION_RES:
        if rx.search(sub):
            return 5, f"production: {rx.pattern[:30]}"
    # 破坏性（等级 3）
    for rx in _DESTRUCTIVE_RES:
        if rx.search(sub):
            return 3, f"destructive: {rx.pattern[:30]}"
    for rx in _IRREVERSIBLE_EXTRA:
        if rx.search(sub):
            return 3, f"irreversible: {rx.pattern[:30]}"
    # 网络非查询（等级 4）
    for rx in _NETWORK_RES:
        if rx.search(sub):
            return 4, f"network: {rx.pattern[:30]}"
    # 本地修改（等级 2）
    for rx in _LOCAL_WRITE_RES:
        if rx.search(sub):
            return 2, f"local-write: {rx.pattern[:30]}"
    # 只读 leads（等级 1，验证确为只读）
    from lingclaude.engine.sensitive_path_gate import (
        BASH_READONLY_LEADS, BASH_READONLY_GIT_SUBS, _is_curl_query_only)
    tokens = sub.split()
    head = tokens[0] if tokens else ""
    # 敏感路径 marker（生产/凭据信号，读敏感路径=等级 4 需审批）
    from lingclaude.engine.sensitive_path_gate import SENSITIVE_MARKERS
    if any(marker in sub.lower() for marker in SENSITIVE_MARKERS if len(marker) > 4):
        return 4, "sensitive-path"
    if head in BASH_READONLY_LEADS:
        if head == "git":
            if len(tokens) > 1 and tokens[1] in BASH_READONLY_GIT_SUBS:
                return 1, "git read-only subcommand"
            return 2, "git with side-effect subcommand"
        if head in ("curl", "wget"):
            if _is_curl_query_only(tokens):
                return 1, f"{head} query-only"
            return 4, f"{head} non-query form"
        return 1, f"read-only lead: {head}"
    # 未知首 token：无法证明只读 → 保守归本地修改（等级 2）
    return 2, f"unknown command: {head[:20]}"


def _is_gray(cmd: str, subcmds: list[str]) -> bool:
    """是否灰区（首 token 不在只读名单且无任何显式信号命中）→ 需兜底门控。"""
    if not cmd:
        return False
    # 有任一显式破坏/网络/写信号就不是灰区（已判过等级）
    for rx in (_DESTRUCTIVE_RES + _PRODUCTION_RES + _NETWORK_RES + _LOCAL_WRITE_RES + _IRREVERSIBLE_EXTRA):
        if rx.search(cmd):
            return False
    return True


def _gray_risk_gate(cmd: str, permission_mode: str) -> RiskVerdict | None:
    """灰区 spec_decision Noul 兜底：P(破坏性) 门控（未启用/故障 → None 交权限链）。"""
    try:
        from lingclaude.core.policy_loader import get as policy_get
        defaults = (policy_get("fan_out_questions") or {}).get("defaults", {})
        if not defaults.get("spec_decision_enabled"):
            return None
        from lingclaude.model.spec_decision import get_engine
        state = f"命令={cmd[:60]} 模式={permission_mode}"
        verdict = get_engine().boolean(state, "该命令是否具有破坏性副作用？")
        p = float(verdict.get("p_true", 0.0))
        if p >= 0.5:
            return _decide(3, permission_mode, f"gray→destructive P={p:.2f}")
        return _decide(2, permission_mode, f"gray→local-write P={p:.2f}")
    except Exception:  # noqa: BLE001 — 兜底故障 fail-open（不分级，交既有权限链）
        return None


def _decide(level: int, permission_mode: str, reason: str) -> RiskVerdict:
    """等级 + 权限模式 → 处置决策（allow/ask/deny）。"""
    mode = (permission_mode or "auto").lower()
    if level <= 1:
        action = "allow"
    elif level <= 2:
        action = "ask"
    else:  # 等级 3/4/5
        action = "deny" if mode == "strict" else "ask"
    return RiskVerdict(level, _LEVEL_LABELS.get(level, "unknown"), action, reason, "local")


def _classify_non_bash(tool_name: str, permission_mode: str) -> RiskVerdict:
    """非 bash 工具域粗分（写类=2，读类=1，其余按 unknown=2）。"""
    readonly_tools = {"read_file", "grep", "glob", "list_directory", "read_symbol",
                      "list_symbols", "find_references", "bash_ls"}
    if tool_name in readonly_tools:
        return RiskVerdict(1, _LEVEL_LABELS[1], "allow", f"read-only tool: {tool_name}", "local")
    return _decide(2, permission_mode, f"non-bash tool: {tool_name}")
