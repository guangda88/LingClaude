"""统一输出脱敏层（A1: 补输出侧安全短板）。

背景（2026-09-13 安全审计）：
- 输入端防线（sensitive_path_gate / bash 黑名单 / bwrap 沙箱）只防「读/执行」，
  输出侧（模型回复 → 会话落盘）无任何 scrub —— key 一旦进入对话即明文沉淀。
- session.py 已有 _redact_message（仅 session 文件落盘用），但 checkpoint /
  conversation / journal 走原始 to_dict()，仍会明文落盘。

本模块统一所有输出侧的密钥形态 scrub，供各落盘路径复用，避免规则漂移。

规则设计（fail-closed）：
- 覆盖常见密钥形态前缀：sk- / sk-ant- / tp- / cpk- / glm- / AIza / AKIA /
  nvapi- / ghp_ / github_pat_ / xoxb- / Bearer <token> 等
- 仅脱敏「看起来像密钥」的形态（前缀 + 足够长度），不误伤普通文本
- 对短 token（< 8 位）不脱敏，避免把 "sk-1" 之类正常词打码
"""

from __future__ import annotations

import re

# 密钥形态正则（与 session.py _SENSITIVE_PATTERNS 对齐并扩展）
# 注意：刻意不匹配单 token 词（如 "token" 本身），只匹配「key 赋值/前缀形态」。
_SENSITIVE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # key/token/secret/password 等赋值形态（含引号、含=、含:）
    re.compile(
        r"(?i)(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|secret|"
        r"password|auth[_-]?header)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=\-]{12,}['\"]?"
    ),
    # 常见密钥前缀（OpenAI/DeepSeek/Anthropic/智谱/火山/谷歌/AWS/GitHub/Slack 等）
    re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{12,}|sk-ant-[A-Za-z0-9_\-]{12,})"),
    re.compile(r"\btp-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bcpk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bglm-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bAIza[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bAKIA[A-Z0-9]{16}"),
    re.compile(r"\bnvapi-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{12,}"),
    # Bearer / Basic 认证头
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{16,}"),
    # 通用长 token（40+ 位随机串，排除纯 hex 的文件 hash 误伤——保留常见形态）
    re.compile(r"\b[0-9a-f]{64}\b"),  # 64 位 hex（GitHub 类 / API key 类）
)


def redact(text: str) -> str:
    """对文本中的密钥形态做统一脱敏（幂等，安全）。

    - 所有命中位置替换为 [REDACTED]
    - 不修改非密钥文本
    """
    if not text:
        return text
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def redact_if_needed(text: str) -> tuple[str, bool]:
    """脱敏并返回是否发生了替换（供日志/审计用）。"""
    out = redact(text)
    return out, out != text


def contains_sensitive(text: str) -> bool:
    """是否包含密钥形态（快速判定，供写入前检查）。"""
    if not text:
        return False
    return any(p.search(text) is not None for p in _SENSITIVE_PATTERNS)
