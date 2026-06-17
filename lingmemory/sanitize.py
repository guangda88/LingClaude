# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵码 V1.0 插片：脱敏

灵犀安全要求：API key/token/password等敏感信息不得落库。
"""
import re


_SENSITIVE_PATTERNS = [
    (re.compile(r'(?:api[_-]?key|token|password|secret)["\']?\s*[:=]\s*["\']?[\w\-]{8,}', re.IGNORECASE), '***REDACTED***'),
    (re.compile(r'sk-[a-zA-Z0-9]{20,}'), '***REDACTED***'),
    (re.compile(r'Bearer\s+[a-zA-Z0-9\-._~+/]+=*'), 'Bearer ***REDACTED***'),
    (re.compile(r'AKIA[0-9A-Z]{16}'), '***REDACTED***'),
]


class Sanitize:
    """敏感信息脱敏 — 插片"""

    @staticmethod
    def text(text: str) -> str:
        if not text:
            return text
        for pattern, replacement in _SENSITIVE_PATTERNS:
            text = pattern.sub(replacement, text)
        return text
