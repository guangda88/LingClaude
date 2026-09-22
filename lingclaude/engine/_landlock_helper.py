"""_landlock_helper.py — 子进程 Landlock self-restrict 后 exec 目标命令。

用法（由 LandlockSandboxProvider.wrap 生成）：
    python3 _landlock_helper.py --writable /path1 /path2 -- /bin/bash -c '<command>'

行为：
- landlock_create_ruleset(444) 版本探测（uapi 标准用法）→ ABI 协商
- prctl(PR_SET_NO_NEW_PRIVS) 前置 → create_ruleset + 逐路径 add_rule(445)
  + restrict_self(446)，成功后 execvp 目标命令
- 写沙箱语义：handled = WRITE/REMOVE/MAKE 系位（ABI2+ 加 REFER，ABI3+ 加
  TRUNCATE）——白名单目录内可写，白名单外这些写操作一律拒绝；
  EXECUTE/READ 位不管辖（否则白名单外连 bash 都无法 exec，沙箱不可用），
  命令执行面由工具层 allowlist 负责。Landlock 规则仅收紧、绝不放宽。

syscall 号依据内核 uapi 头文件（/usr/include/asm/unistd_64.h 等，两架构同号），
tests/test_landlock_syscalls.py 钉死；此前版本的 459/460/461 与 476/477/478
均为错号（导致探测静默失败），436 为 docstring 笔误。

无 Landlock 能力（旧内核 / 非 Linux / syscall 失败）时 fail-open：直接 exec
目标命令并在 stderr 打一行提示（provider.available() 预先把关，正常路径不会到这）。
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno as _errno
import glob as _glob
import os
import sys

# x86_64 / aarch64 Landlock 系统调用号（uapi 两架构同号，tests/test_landlock_syscalls.py 钉死）
_SYSCALLS = {
    "x86_64": {"create_ruleset": 444, "add_rule": 445, "restrict_self": 446},
    "aarch64": {"create_ruleset": 444, "add_rule": 445, "restrict_self": 446},
}

# fs 访问位（uapi landlock.h）：
#   EXECUTE=1<<0 WRITE_FILE=1<<1 READ_FILE=1<<2 READ_DIR=1<<3
#   REMOVE_DIR=1<<4 REMOVE_FILE=1<<5 MAKE_CHAR=1<<6 MAKE_DIR=1<<7
#   MAKE_REG=1<<8 MAKE_SOCK=1<<9 MAKE_FIFO=1<<10 MAKE_BLOCK=1<<11
#   MAKE_SYM=1<<12 REFER=1<<13(ABI2) TRUNCATE=1<<14(ABI3) IOCTL_DEV=1<<15(ABI5)
# 写沙箱管辖位：全部写系位；不含 EXECUTE/READ_*（见模块 docstring）
_FS_WRITE_BASE = (
    (1 << 1)            # WRITE_FILE
    | (0x1F << 4)       # REMOVE_DIR..MAKE_SYM（1<<4 至 1<<12）
)


def _landlock_abi(libc, nr_create: int) -> int:
    """uapi 标准版本探测：create_ruleset(NULL, 0, VERSION) → 最高支持 ABI（<1 即不可用）。"""
    _LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
    libc.syscall.restype = ctypes.c_long
    libc.syscall.argtypes = [ctypes.c_long, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint]
    return int(libc.syscall(ctypes.c_long(nr_create), ctypes.c_void_p(0),
                            ctypes.c_size_t(0), ctypes.c_uint(_LANDLOCK_CREATE_RULESET_VERSION)))


def _apply_landlock(writable: list[str]) -> bool:
    """建规则集 + 每路径加 PATH_BENEATH 可写规则 + restrict_self。成功 True。

    Landlock 语义：handled 声明管辖的访问位在白名单外一律拒绝，
    白名单目录内按 allowed_access 放行；规则只收紧不放宽。
    """
    uname = os.uname().machine
    if uname not in _SYSCALLS:
        return False
    nr = _SYSCALLS[uname]

    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)

    class RulesetAttr(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]

    class PathBeneath(ctypes.Structure):
        # uapi: { __u64 allowed_access; __s32 parent_fd; }（fd 非 dev/ino！）
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]

    # 1) ABI 版本探测（纯内核查询，无状态变更）
    abi = _landlock_abi(libc, nr["create_ruleset"])
    if abi < 1:
        print(f"[landlock-helper] Landlock ABI 探测失败（内核 <5.13 或 LSM 未启用）",
              file=sys.stderr)
        return False

    # 2) handled 位集按 ABI 协商（声明内核不支持的位会被 EINVAL）
    handled = _FS_WRITE_BASE
    if abi >= 2:
        handled |= 1 << 13  # REFER：白名单外重命名/硬链接一并拒绝
    if abi >= 3:
        handled |= 1 << 14  # TRUNCATE

    # 3) prctl(PR_SET_NO_NEW_PRIVS) —— 非特权启用 Landlock 的前置条件
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        return False

    # 4) create_ruleset(attr{handled}, size, flags=0)
    libc.syscall.restype = ctypes.c_long
    libc.syscall.argtypes = [ctypes.c_long, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint]
    attr = RulesetAttr(handled)
    r = libc.syscall(ctypes.c_long(nr["create_ruleset"]), ctypes.byref(attr),
                     ctypes.c_size_t(ctypes.sizeof(attr)), ctypes.c_uint(0))
    if r < 0:
        e = ctypes.get_errno()
        print(f"[landlock-helper] create_ruleset 失败 errno={e}"
              f"({_errno.errorcode.get(e, '?')})", file=sys.stderr)
        return False
    ruleset_fd = int(r)

    # 5) add_rule(ruleset_fd, RULE_PATH_BENEATH, attr, flags=0)，fd 经 O_PATH 打开
    added = 0
    for p in writable:
        # 展开通配符；展不开也放行（少规则=更严，收紧语义安全侧）
        expanded = _glob.glob(p, recursive=True)
        for q in (expanded if expanded else [p]):
            try:
                pfd = os.open(q, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                continue
            try:
                pb = PathBeneath(handled, pfd)
                libc.syscall.argtypes = [ctypes.c_long, ctypes.c_int, ctypes.c_int,
                                         ctypes.c_void_p, ctypes.c_uint]
                r2 = libc.syscall(ctypes.c_long(nr["add_rule"]), ruleset_fd, 1,
                                  ctypes.byref(pb), ctypes.c_uint(0))
                if r2 == 0:
                    added += 1
            finally:
                os.close(pfd)
    if added == 0:
        print("[landlock-helper] 无一条白名单规则可用，restrict 将全拒，走 fail-open",
              file=sys.stderr)
        os.close(ruleset_fd)
        return False

    # 6) restrict_self(ruleset_fd, flags=0) —— 生效后规则随进程与 exec 持续
    libc.syscall.argtypes = [ctypes.c_long, ctypes.c_int, ctypes.c_uint]
    r3 = libc.syscall(ctypes.c_long(nr["restrict_self"]), ruleset_fd, ctypes.c_uint(0))
    os.close(ruleset_fd)
    return r3 == 0


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
            i += 1
    target = argv[i:]
    if not target:
        print("usage: _landlock_helper.py --writable P [P...] -- CMD [ARGS]", file=sys.stderr)
        return 2

    try:
        if _apply_landlock(writable):
            print(f"[landlock-helper] Landlock self-restrict 生效: {writable}", file=sys.stderr)
        else:
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
