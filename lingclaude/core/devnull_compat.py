"""devnull_compat — /dev/null 只读沙箱兼容层（H18 环境修复项）。

背景：lingxi/bwrap 沙箱将 /dev 以只读方式挂入（根文件系统 ro），导致
open("/dev/null", "w") 抛 EACCES。连带故障面（均已在事故中实证）：
  - subprocess.DEVNULL / subprocess._get_devnull()：运行时读 os.devnull
  - pytest capture（FDCapture）：运行时读 os.devnull
  - 一切 shell 重定向 "> /dev/null"

解法：本模块在导入时探测 /dev/null 可写性；不可写则在可写目录
（$LC_DEVNULL_PATH > /tmp）创建一个普通 0 字节文件，并重写
os.devnull 指向它。subprocess / pytest 均在调用时动态读取 os.devnull，
因此这一处修补可同时修复二者；git 等外部进程通过继承本进程的
stdin/stdout/stderr（由 Popen 显式提供合法句柄）绕开自身对 /dev/null
的内部打开。

幂等：重复导入无副作用；宿主环境（/dev/null 正常）下零行为变化。
"""
from __future__ import annotations

import os
import tempfile

_original_devnull: str = os.devnull
_compat_path: str | None = None
_patched: bool = False


def _candidate_dirs() -> list[str]:
    dirs = []
    env_dir = os.environ.get("LC_DEVNULL_DIR")
    if env_dir:
        dirs.append(env_dir)
    dirs.append(tempfile.gettempdir())  # /tmp（沙箱内 rw 实测可用）
    dirs.append(os.path.expanduser("~/.lingclaude"))
    return dirs


def _ensure_compat_file() -> str | None:
    """在首个可写目录中创建丢弃型文件，返回其路径；全失败返回 None。"""
    for d in _candidate_dirs():
        try:
            os.makedirs(d, exist_ok=True)
            fd, path = tempfile.mkstemp(prefix=".devnull-compat-", dir=d)
            os.close(fd)
            return path
        except OSError:
            continue
    return None


def _patch() -> bool:
    global _compat_path, _patched
    if _patched:
        return _compat_path is not None
    _patched = True
    try:
        # 真实写测试：权限位正常也可能因挂载 ro 被拒（本事故形态）
        with open(_original_devnull, "ab"):
            pass
        return False  # 正常环境，不接管
    except OSError:
        path = _ensure_compat_file()
        if path is None:
            return False
        _compat_path = path
        os.devnull = path  # type: ignore[misc] — subprocess/pytest 运行时动态读取
        return True


_is_degraded = _patch()


def is_degraded() -> bool:
    """True = 当前进程运行在 /dev/null 只读沙箱，已启用兼容路径。"""
    return _is_degraded


def devnull_path() -> str:
    """返回实际生效的 null 设备路径（原始或兼容路径）。"""
    return os.devnull
