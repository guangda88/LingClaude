"""LC_L1_09: 文件路径校验

实现 safe_path_join(base_dir, user_path) -> str
- 用 realpath 解析
- 校验最终路径在 base_dir 内
- 越界返回 None
"""
import os


def safe_path_join(base_dir: str, user_path: str):
    """安全地拼接并校验文件路径。

    Args:
        base_dir: 基础目录（绝对路径）
        user_path: 用户提供的相对路径

    Returns:
        解析后的绝对路径；若越界则返回 None
    """
    base_real = os.path.realpath(base_dir)
    # os.path.realpath 在 Python 3.6+ 支持路径不存在的情况
    full = os.path.realpath(os.path.join(base_real, user_path))

    # 检查 full 是否在 base_real 内
    if full == base_real or full.startswith(base_real + os.sep):
        return full
    return None


if __name__ == "__main__":
    r1 = safe_path_join("/var/www", "uploads/img.jpg")
    assert r1 == "/var/www/uploads/img.jpg", f"got {r1!r}"

    r2 = safe_path_join("/var/www", "../../etc/passwd")
    assert r2 is None, f"got {r2!r}"

    r3 = safe_path_join("/home/user", "../secret.txt")
    assert r3 is None, f"got {r3!r}"

    r4 = safe_path_join("/data/files", "subdir/../subdir/file.txt")
    assert r4 == "/data/files/subdir/file.txt", f"got {r4!r}"
    print("LC_L1_09 OK")