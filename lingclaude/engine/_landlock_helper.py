"""P0-B Landlock 辅助：子进程 self-restrict 到指定可写路径，再 exec 目标命令。

用法（由 LandlockSandboxProvider.wrap 生成）：
    python3 _landlock_helper.py --writable /path1 /path2 -- /bin/bash -c '<command>'

行为：
- prctl(PR_SET_NO_NEW_PRIVS, 1) 阻止子进程后续获取权限
- landlock_create_ruleset(abi 自动协商) + landlock_add_rule(每路径可写) +
  landlock_restrict_self，然后 execvp 目标命令
- Landlock 规则仅收紧、绝不放宽；exec 后规则随进程持续生效

无 Landlock 能力（旧内核 / 非 Linux）时 helper 直接 exec 目标命令并
在 stderr 打一行提示（由 provider.available() 预先把关，正常路径不会到这）。
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import glob as _glob

# x86_64 / aarch64 Landlock 系统调用号
_SYSCALLS = {
    "x86_64": {"create_ruleset": 459, "add_rule": 460, "restrict_self": 461},
    "aarch64": {"create_ruleset": 476, "add_rule": 477, "restrict_self": 478},
}
# ABI 协商：从 v5（支持 LANDLOCK_ACCESS_FS_REFER）往 v1 退
_MAX_ABI = 5

LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
LANDLOCK_ACCESS_FS_SEARCH = 1 << 4
_FS_ALLOWED = (
    LANDLOCK_ACCESS_FS_READ_FILE
    | LANDLOCK_ACCESS_FS_READ_DIR
    | LANDLOCK_ACCESS_FS_SEARCH
    | LANDLOCK_ACCESS_FS_EXECUTE
    | LANDLOCK_ACCESS_FS_WRITE_FILE
)


def _libc() -> ctypes.CDLL:
    name = ctypes.util.find_library("c") or "libc.so.6"
    libc = ctypes.CDLL(name)
    libc.syscall.restype = ctypes.c_long
    libc.syscall.argtypes = (
        [ctypes.c_long]
        + [ctypes.c_void_p] * 1
        + [ctypes.c_size_t, ctypes.c_uint] * 2
    )
    return libc


def _load_structs():
    class _Struct(ctypes.Structure):
        pass

    # struct landlock_ruleset_attr
    class ruleset_attr(_Struct):
        _fields_ = [
            ("handled_access_fs", ctypes.c_uint64),
            ("parent_devno", ctypes.c_uint64),
            ("parent_ino", ctypes.c_uint64),
            ("flags", ctypes.c_uint32),
            ("pad", ctypes.c_uint32),
        ]

    # struct landlock_path_beneath_attr (ABI v1)
    class path_beneath(_Struct):
        _fields_ = [
            ("parent_devno", ctypes.c_uint64),
            ("parent_ino", ctypes.c_uint64),
            ("allowed_access", ctypes.c_uint32),
            ("pad", ctypes.c_uint32),
            ("path_beneath_attr", ctypes.c_char_p * 0),  # placeholder
        ]

    return ruleset_attr


def _apply_landlock(writable: list[str]) -> bool:
    """建规则集 + 每路径加 PATH_BENEATH 可写规则 + restrict_self。成功 True。"""
    uname = os.uname().machine
    if uname not in _SYSCALLS:
        return False
    sysnos = _SYSCALLS[uname]

    libc = _libc()
    libc.prctl.argtypes = [ctypes.c_int] * 4
    libc.prctl.restype = ctypes.c_int
    # PR_SET_NO_NEW_PRIVS = 38
    if libc.prctl(38, 1, 0, 0) != 0:
        return False

    from ctypes import c_uint32, c_uint64, c_void_p, byref, Structure

    # landlock_create_ruleset(attr, size, abi)
    class RulesetAttr(Structure):
        _fields_ = [
            ("handled_access_fs", c_uint64),
            ("parent_devno", c_uint64),
            ("parent_ino", c_uint64),
            ("flags", c_uint32),
            ("pad", c_uint32),
        ]

    class PathBeneath(Structure):
        _fields_ = [
            ("parent_devno", c_uint64),
            ("parent_ino", c_uint64),
            ("allowed_access", c_uint32),
            ("pad", c_uint32),
        ]

    def _call(nr, n_args, *args):
        """泛化 syscall 调用（动态 argtypes，按 n_args 裁剪）。"""
        ctypesypes = [ctypes.c_long, c_void_p, ctypes.c_size_t, c_uint32, ctypes.c_uint]
        libc.syscall.argtypes = [ctypes.c_long] + ctypesypes[: n_args]
        return libc.syscall(ctypes.c_long(nr), *args)

    attr = RulesetAttr(_FS_ALLOWED, 0, 0, 0, 0)
    ruleset_fd = -1
    for abi in range(_MAX_ABI, 0, -1):
        r = _call(sysnos["create_ruleset"], 3, byref(attr), ctypes.sizeof(attr), abi)
        if r >= 0:
            ruleset_fd = int(r)
            break
    if ruleset_fd < 0:
        return False

    # 展开通配符；展不开也放行（Landlock 收紧语义：少规则=更严）
    paths = []
    for p in writable:
        expanded = _glob.glob(p, recursive=True)
        paths.extend(expanded if expanded else [p])

    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        pb = PathBeneath(st.st_dev, st.st_ino, _FS_ALLOWED, 0)
        _call(
            sysnos["add_rule"],
            4,
            c_void_p(ruleset_fd),
            1,  # LANDLOCK_RULE_PATH_BENEATH
            byref(pb),
            ctypes.sizeof(pb),
        )
    _call(sysnos["restrict_self"], 2, c_void_p(ruleset_fd), 0)
    os.close(ruleset_fd)
    return True



def main() -> int:
    argv = sys.argv[1:]
    writable: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--writable":
            i += 1
            while i < len(argv) and not argv[i].startswith("-"):
                writable.append(argv[i])
                i += 1
        elif argv[i] == "--":
            i += 1
            break
        else:
            break
    target = argv[i:]
    if not target:
        print("usage: _landlock_helper.py --writable P [P...] -- CMD [ARGS]", file=sys.stderr)
        return 2

    try:
        ok = _apply_landlock(writable)
        if not ok:
            print(
                f"[landlock-helper] Landlock self-restrict 失败，exec 目标命令不受 Landlock 约束: {writable}",
                file=sys.stderr,
            )
    except Exception as e:  # noqa: BLE001 — helper 永不阻断目标执行（fail-open 显式告警）
        print(f"[landlock-helper] 异常，继续执行: {e}", file=sys.stderr)

    os.execvp(target[0], target)  # 不返回
    return 1


if __name__ == "__main__":
    sys.exit(main())
