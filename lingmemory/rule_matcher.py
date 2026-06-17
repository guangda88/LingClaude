# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵码 V1.0 插片：编码错误模式匹配

从离线提取的12种错误模式中提取规则匹配逻辑。
"""
from typing import Optional


_RULE_PATTERNS = [
    ("permission denied", "文件/目录权限不足时，先检查owner和权限位，不要直接chmod 777", "debugging"),
    ("address already in use", "端口被占用时，先查谁在用（ss/lsof），不要盲目换端口", "debugging"),
    ("modulenotfound", "ModuleNotFoundError时先检查virtualenv是否激活、pip install路径是否正确", "debugging"),
    ("syntaxerror", "Python语法/缩进错误时，检查tab和space混用、缺少冒号、括号不匹配", "debugging"),
    ("indentationerror", "Python语法/缩进错误时，检查tab和space混用、缺少冒号、括号不匹配", "debugging"),
    ("keyerror", "KeyError时用dict.get(key, default)替代直接访问，防御性编程", "debugging"),
    ("attributeerror", "AttributeError时检查对象是否None、类型是否正确、方法名是否拼写正确", "debugging"),
    ("typeerror", "TypeError时检查参数类型、参数数量、可变/不可变类型混用", "debugging"),
    ("filenotfound", "FileNotFoundError时先检查路径是否用绝对路径、文件名大小写、~是否展开", "debugging"),
    ("connection refused", "Connection refused时检查服务是否启动、端口是否正确、防火墙是否放行", "debugging"),
    ("database is locked", "SQLite database locked时启用WAL模式+busy_timeout，不要用长事务", "debugging"),
    ("timeout", "超时错误时检查网络连通性、服务响应时间、是否需要增加timeout参数", "debugging"),
    ("unicodedecode", "Unicode错误时统一用utf-8编码，open()加encoding='utf-8'参数", "debugging"),
]


class RuleMatcher:
    """错误模式匹配 — 插片"""

    @staticmethod
    def match(error_text: str) -> Optional[dict]:
        if not error_text:
            return None
        text_lower = error_text.lower()
        for keyword, rule, category in _RULE_PATTERNS:
            if keyword in text_lower:
                return {"rule": rule, "category": category, "confidence": 0.5}
        return None
