"""沙箱 /dev/null 不可写（bwrap 设备白名单缺失）的 pytest 救活插件。

用法: PYTHONPATH=scripts python3 -m pytest -p devnull_fallback_plugin [目标]
或用包装器: scripts/pytest-sandbox [目标]

原理（须在 pytest 捕获初始化前 import）:
1. os.devnull 重定向到可写兜底文件 —— 救 FDCapture / logging FileHandler
2. builtins.open 拦截字面 '/dev/null' —— 救散落的直接引用
宿主侧根因与真修复方案见 docs/cli/ENV_DEVNULL_FIX.md。
"""
import builtins
import os
import tempfile

_FALLBACK = os.path.join(tempfile.gettempdir(), "devnull-fallback")


def _ensure_fallback() -> str:
    global _FALLBACK
    try:
        with open(_FALLBACK, "a", encoding="utf-8"):
            pass
    except OSError:  # /tmp 也不可写时退回 CWD 内临时文件
        _FALLBACK = os.path.abspath(".devnull-fallback")
        with open(_FALLBACK, "a", encoding="utf-8"):
            pass
    return _FALLBACK


_path = _ensure_fallback()
os.devnull = _path  # pytest capture / logging 插件走这里

_orig_open = builtins.open


def _patched_open(file, mode="r", *args, **kwargs):
    if isinstance(file, str) and file == "/dev/null":
        return _orig_open(_path, mode, *args, **kwargs)
    return _orig_open(file, mode, *args, **kwargs)


builtins.open = _patched_open
