"""LC_L1_07: HTML转义

实现 escape_html(text) -> str
- 转义 < > & " '
- 必须先替换 &，避免双重转义
"""
_HTML_ESCAPE_MAP = [
    ('&', '&amp;'),   # 必须最先
    ('<', '&lt;'),
    ('>', '&gt;'),
    ('"', '&quot;'),
    ("'", '&#x27;'),
]


def escape_html(text: str) -> str:
    """对 HTML 特殊字符进行转义，防止 XSS。

    Args:
        text: 原始字符串

    Returns:
        转义后的字符串
    """
    for old, new in _HTML_ESCAPE_MAP:
        text = text.replace(old, new)
    return text


if __name__ == "__main__":
    assert escape_html("<script>alert('xss')</script>") == "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;"
    assert escape_html("Hello & World") == "Hello &amp; World"
    assert escape_html("A\"B'C") == "A&quot;B&#x27;C"
    assert escape_html("") == ""
    assert escape_html("plain text") == "plain text"
    print("LC_L1_07 OK")