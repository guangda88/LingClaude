"""LC_L1_08: 命令行参数校验

实现 validate_command_args(args, allowed_chars='') -> (bool, str)
- 默认检查 shell 危险字符
- 提供 allowed_chars 时仅允许列表内的字符
"""
# shell 危险字符集合（不含空格/字母数字/常见合法符号）
_DANGEROUS_CHARS = set(";|$`(){}[]<>\"'\n!#~*?&")


def validate_command_args(args: list, allowed_chars: str = "") -> tuple:
    """校验命令行参数安全性。

    Args:
        args: 命令行参数列表
        allowed_chars: 允许的字符集合；空字符串表示使用危险字符黑名单

    Returns:
        (is_safe, error_message)
    """
    if not allowed_chars:
        # 使用危险字符黑名单
        for arg in args:
            for ch in arg:
                if ch in _DANGEROUS_CHARS:
                    return (False, f"包含危险字符 {ch}")
        return (True, "")
    else:
        # 使用白名单
        allowed = set(allowed_chars)
        for arg in args:
            for ch in arg:
                if ch not in allowed:
                    return (False, f"包含不允许的字符 {ch}")
        return (True, "")


if __name__ == "__main__":
    assert validate_command_args(["ls", "-la"]) == (True, "")
    assert validate_command_args(["ls", ";rm -rf /"]) == (False, "包含危险字符 ;")
    assert validate_command_args(["echo", "$(cat /etc/passwd)"]) == (False, "包含危险字符 $")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
    assert validate_command_args(["grep", "test", "file.txt"], allowed_chars=allowed) == (True, "")
    assert validate_command_args([], allowed_chars=allowed) == (True, "")
    print("LC_L1_08 OK")