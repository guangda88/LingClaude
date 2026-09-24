"""
沙箱后端解耦层（灵元：插片可换）。

解耦前：bash.py._sandbox_command 直接调用 bwrap（焊死单后端）。
解耦后：SandboxProvider Protocol + BwrapSandboxProvider（默认）/ NoopSandboxProvider，
        bash.py 通过 CapabilitySeam(SANDBOX_SEAM) 获取 provider，后端可替换。

对应 DSH sandbox 三态/四后端：本实现为 bwrap 单后端 + noop 降级，
扩展点留给 firejail / gVisor 等自定义 Provider。

网络策略（2026-09-11 白名单化）：
- 默认：--unshare-net 网络隔离（fail-closed）
- 例外：wrap(allow_network=True) 时不注入 --unshare-net，保留文件系统沙箱，
  仅用于白名单内的可信 git 远程操作（git push/pull/fetch/clone/ls-remote）。
  白名单判定在 bash.py._NETWORK_ALLOWED_COMMANDS，本层只负责按标记组装 bwrap 参数。
"""
from __future__ import annotations

import logging
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from lingclaude.core.seam import SeamRegistry, SeamType
from lingclaude.lacp.sandbox_policy import is_safe_writable_dir

logger = logging.getLogger(__name__)

# bwrap 可用性探测结果缓存：(None=未探测, (bool, reason))
_bwrap_probe_cache: tuple[bool, str] | None = None
# P0-B (2026-09-22): Landlock 探测缓存（与 bwrap 对称，一次探测全程复用）
_landlock_probe_cache: tuple[bool, str] | None = None

# P0-B (2026-09-22): 工具类别沙箱豁免清单（NOT_GOOD_AT seam 字段）。
# 哪些工具类别不进沙箱 —— 只读 grep/rg/cat/ls 类命令沙箱无收益（纯读、无写风险），
# 走 noop 直通省 20-40ms 探测+wrap 开销。本层只声明类别，
# 具体命令判定仍在 bash.py._is_readonly_diag_command（本清单是其语义注记 + 未来
# 扩展锚点，避免把配置槽塞进 LoopHooks 变成第二个 god object）。
NOT_GOOD_AT: dict[str, str] = {
    "readonly_diag": "只读 grep/rg/cat/ls/head/tail 类：无写面，沙箱探测开销 > 安全收益",
}


def _bwrap_probe(bwrap: str) -> tuple[bool, str]:
    """探测 bwrap 在本环境是否真正可用（一次性，结果缓存）。

    bwrap 依赖 user namespaces / network namespaces，容器或受限环境即使有
    二进制也无法运行——必须探测，不能反复失败。

    返回 (available, reason)：reason 为 None 表示可用，否则为不可用原因
    （如 "user namespace 受限: Read-only file system"），供上层诊断/告警。
    """
    global _bwrap_probe_cache
    if _bwrap_probe_cache is not None:
        return _bwrap_probe_cache
    import subprocess

    # 韧性:探测失败重试一次(会话启动期 MCP/后台线程抢资源,超时可能是瞬时的)。
    # 只有连续两次失败才缓存 False,避免单次抖动让整个会话永久降级非沙箱。
    last_err = "未知错误"
    for _attempt in range(2):
        try:
            # 修复(2026-09-11 codex 审计):探测必须使用与真实执行一致的 flag 组合——
            # 真实 wrap 默认注入 --unshare-net。此前探测不带 --unshare-net，在
            # 网络 namespace 受限环境会「探测通过、真实执行失败」(echo hello 都挂)。
            # 同时兼容 merged-usr 系统(/bin -> usr/bin 符号链接)整根只读绑定。
            proc = subprocess.run(  # nosec B603 — 探测固定白名单命令
                [
                    bwrap,
                    "--ro-bind", "/", "/",
                    "--unshare-net",
                    "--", "/bin/true",
                ],
                capture_output=True,
                timeout=15,
            )
            if proc.returncode == 0:
                _bwrap_probe_cache = (True, None)
                return _bwrap_probe_cache
            last_err = (proc.stderr or b"").decode(errors="replace").strip() or f"exit={proc.returncode}"
        except Exception as e:  # noqa: BLE001 — 探测失败视为不可用
            last_err = str(e)
    _bwrap_probe_cache = (False, last_err)
    return _bwrap_probe_cache


@runtime_checkable
class SandboxProvider(Protocol):
    """沙箱后端协议（灵元：插片可换）。

    实现方需提供：
    - name: 后端名（如 bwrap / noop / firejail）
    - available() -> bool: 后端是否可用（探测）
    - wrap(command: str, working_dir: Path | None, allow_network: bool) -> str:
      返回包裹后的命令
    """

    name: str

    def available(self) -> bool:
        """后端是否可用（bwrap 探测 / noop 恒真）。"""
        ...

    def wrap(
        self, command: str, working_dir: Path | None = None, allow_network: bool = False
    ) -> str:
        """包裹命令（原样返回 = 无沙箱；加前缀 = 沙箱）。

        allow_network=True 时保留文件系统沙箱但开放网络（仅限白名单 git 远程操作）。
        """
        ...


class BwrapSandboxProvider:
    """默认实现：bwrap 沙箱包裹（只读系统路径 + 可写工作目录 + 网络白名单例外）。"""

    name = "bwrap"

    def __init__(self) -> None:
        self._bwrap = shutil.which("bwrap")

    def available(self) -> bool:
        return self._bwrap is not None and _bwrap_probe(self._bwrap)[0]

    def probe_reason(self) -> str | None:
        """返回 bwrap 不可用原因（None=可用/未探测到问题）。"""
        if self._bwrap is None:
            return "bwrap 二进制不在 PATH"
        ok, reason = _bwrap_probe(self._bwrap)
        return None if ok else reason

    def wrap(
        self,
        command: str,
        working_dir: Path | None = None,
        allow_network: bool = False,
        extra_writable_dirs: list[str] | None = None,
    ) -> str:
        """bwrap 包裹命令（策略对标 DSH read-only/workspace-write）。

        allow_network=True → 不注入 --unshare-net（网络白名单例外，仅限可信 git
        远程操作；白名单判定在 bash.py._NETWORK_ALLOWED_COMMANDS，不在白名单内一律隔离）。
        """
        if not self.available():
            return command
        wd = str(working_dir or Path.cwd())
        parts = [
            self._bwrap,
        ]
        # 网络策略：默认隔离（fail-closed）；allow_network=True 时才开放（白名单例外）。
        if not allow_network:
            parts.append("--unshare-net")
        parts += [
            # merged-usr 安全绑定:整根只读(/bin 是 usr/bin 的符号链接,
            # 逐目录绑定会自毁路径),再按需放开工作目录与 /tmp。
            "--ro-bind", "/", "/",
            # /dev/null 必须可写(ro-bind 使 /dev 只读,导致 2>/dev/null、
            # git/pytest 等 EACCES——2026-09-06 会话"权限不足"主根因)。
            # --dev-bind 独立于 ro-bind 重新以读写挂载该设备节点。
            "--dev-bind", "/dev/null", "/dev/null",
            # /dev/urandom|random 同样必须可写: git add/commit 需取随机字节
            # 创建临时对象, 只读 /dev 导致 "unable to get random bytes" EACCES
            # (2026-09-12 会话 git 全挂根因——仅修 null 漏修 urandom 的副损伤)。
            # 可写绑定不泄露数据: 字符设备只提供熵, 无文件系统访问面。
            "--dev-bind", "/dev/urandom", "/dev/urandom",
            "--dev-bind", "/dev/random", "/dev/random",
            "--bind", "/tmp", "/tmp",
        ]
        # 额外可写目录白名单（策略层 allowed_paths 对齐）：仅显式列出的协作
        # 路径放开写（如 /home/ai/lingcode），其余保持只读。安全边界=白名单。
        # 顺序关键: 先绑父目录（/home/ai），再绑子目录（wd）——bwrap mount 堆叠，
        # 后 bind 的更具体路径生效；若先绑 wd 再绑 /home/ai，父级会覆盖子级，
        # 导致工作目录写权限丢失（2026-09-13 实测 B1 对齐时的坑）。
        extra_paths: list[str] = []
        for d in (extra_writable_dirs or []):
            rp = os.path.realpath(d)
            if rp not in ("", "/") and rp != os.path.realpath(wd) and rp != "/tmp":
                extra_paths.append(rp)
        # 先绑父目录（路径深度大的先），再绑子目录 —— bwrap mount 堆叠，
        # 后 bind 的更具体路径生效；若父 bind 在子 bind 之后会覆盖子级写权限。
        for rp in sorted(extra_paths, key=lambda p: p.count("/"), reverse=True):
            parts += ["--bind", rp, rp]
        # 工作目录：若已被某可写父目录覆盖（startswith），则无需重复 bind；
        # 否则显式 bind（保证 wd 始终可写，且不得晚于父目录 bind——此处天然在父之后）。
        wd_real = os.path.realpath(wd)
        if wd_real != "/tmp" and not any(
            wd_real.startswith(os.path.realpath(ep) + "/")
            for ep in extra_paths
        ):
            parts += ["--bind", wd, wd]
        parts += [
            "--die-with-parent",
            "--",
            "/bin/bash", "-c", command,
        ]
        return " ".join(shlex.quote(p) for p in parts)


class NoopSandboxProvider:
    """降级实现：无沙箱（原样返回命令；黑名单+资源限制仍由 bash.py 兜底）。"""

    name = "noop"

    def available(self) -> bool:
        return True

    def wrap(
        self, command: str, working_dir: Path | None = None, allow_network: bool = False
    ) -> str:
        return command


# ── P0-B (2026-09-22): Landlock 轻量后端（Linux，无需 root，内核 5.8+）──
# 与 bwrap 互补：bwrap 依赖 user namespaces（容器/受限环境常被禁），Landlock
# 只依赖内核 LSM 钩子（CONFIG_SECURITY_LANDLOCK），无 user namespace、无 mount
# 命名空间开销，适合 bwrap 探测失败时的细粒度降级。实现策略：
# - 探测：检查 /proc/sys/kernel/unprivileged_userns_clone 与 landlock ABI 版本
#   （/sys/kernel/security/landlock? 无公开 ABI 文件 → 用 prctl/uname 侧信道：
#   直接尝试一次 prctl(PR_SET_KEEPCAPS) 无效 → 改走 python ctypes 尝试
#   调 syscall(436, ...) 判断 ENOSYS/EPERM。失败缓存 False。
# - wrap：Landlock 是进程自约束（需子进程主动 apply_rules+create_ruleset 后
#   self-restrict），无法像 bwrap 那样在父进程包裹。故 Landlock provider 的
#   wrap 生成「pre-exec hook 脚本」：fork 子进程后子进程先建 ruleset
#   （wd + /tmp 可写，其余只读）再 exec 目标命令。
# - 默认 bwrap 不可用时，create_default_sandbox_provider 优先探 Landlock，
#   Landlock 不可用再落 noop。


def _landlock_probe() -> tuple[bool, str]:
    """探测 Landlock 是否可用（Linux 内核 5.13+，无需 root，无副作用）。

    uapi 标准版本探测：create_ruleset(attr=NULL, size=0, flags=VERSION(1<<0))
    → 返回内核支持的最高 ABI（>=1 即可用）；errno 经 use_errno 取回。
    不可用 empty-attr 探测：handled_access_fs=0 会返回 ENOMSG（uapi 文档化行为，
    2026-09-22 本机实证踩坑——此前版本以 flags 位误作 abi 传入或空规则集探测，
    导致 6.8 内核上恒 False 假不可用）。号依据见 tests/test_landlock_syscalls.py。
    """
    global _landlock_probe_cache
    if _landlock_probe_cache is not None:
        return _landlock_probe_cache
    if os.name != "posix" or sys.platform != "linux":
        _landlock_probe_cache = (False, "Landlock 仅 Linux 支持")
        return _landlock_probe_cache
    import ctypes
    import ctypes.util

    libc_name = ctypes.util.find_library("c") or "libc.so.6"
    try:
        libc = ctypes.CDLL(libc_name, use_errno=True)
    except OSError as e:
        _landlock_probe_cache = (False, f"libc 加载失败: {e}")
        return _landlock_probe_cache

    SYS_landlock_create_ruleset = 444  # x86_64 / aarch64 同号（uapi 头文件实证）
    _LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
    try:
        libc.syscall.restype = ctypes.c_long
        libc.syscall.argtypes = [ctypes.c_long, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint]
        import errno as _errno
        r = libc.syscall(SYS_landlock_create_ruleset, ctypes.c_void_p(0),
                         ctypes.c_size_t(0), ctypes.c_uint(_LANDLOCK_CREATE_RULESET_VERSION))
        if r >= 1:
            _landlock_probe_cache = (True, None)
        else:
            e = ctypes.get_errno()
            _landlock_probe_cache = (
                False,
                f"landlock_create_ruleset(VERSION) 失败 r={r} errno={e}"
                f"({_errno.errorcode.get(e, '?')})；需 Linux 5.13+ 且 LSM 启用",
            )
    except Exception as e:
        _landlock_probe_cache = (False, f"Landlock syscall 异常: {e}")
    return _landlock_probe_cache


class LandlockSandboxProvider:
    """P0-B 轻量后端：Linux Landlock LSM（无 user namespace 依赖，内核 5.13+）。

    wrap 返回 bash 命令前缀脚本：fork 后子进程 self-restrict 到
    （wd + /tmp + extra_writable_dirs）可写、其余只读，再 exec 原命令。
    bwrap 不可用时由 create_default_sandbox_provider 优先选中（本机 6.8 内核
    有 CONFIG_SECURITY_LANDLOCK 才真正生效，否则探测判 False 回 noop）。
    """

    name = "landlock"

    def available(self) -> bool:
        return _landlock_probe()[0]

    def probe_reason(self) -> str | None:
        ok, reason = _landlock_probe()
        return None if ok else reason

    def wrap(
        self,
        command: str,
        working_dir: Path | None = None,
        allow_network: bool = False,
        extra_writable_dirs: list[str] | None = None,
    ) -> str:
        """Landlock 包裹：python helper 子进程 self-restrict 到可写白名单再 exec。

        helper 内部先 prctl(NO_NEW_PRIVS) + landlock 规则集，再 execvp 目标命令；
        命令以 ``--writable ... -- bash -c <command>`` 形式传入，helper 负责真正的
        ruleset apply。allow_network 在 Landlock 语义下无对应（Landlock 只管文件
        写面，网络隔离仍由 bwrap --unshare-net 承担）——Landlock 作为 bwrap 缺席
        时的细粒度降级，网络面由 bash.py 黑名单+资源限制兜底。
        """
        if not self.available():
            return command
        wd = str(working_dir or Path.cwd())
        # 2026-09-24（V1 纵深，灵安交叉审计）：bwrap 层已滤 "/"，此层（landlock）
        # 与 Seatland 层原样拼接 extra_writable_dirs——env 驱动的 /、/etc 直通。
        # 统一过 is_safe_writable_dir 钳制（wd//tmp 是沙箱语义内置可写面，不过滤）。
        safe_extra = [d for d in (extra_writable_dirs or []) if is_safe_writable_dir(d)]
        writable = [wd, "/tmp"] + safe_extra
        writable_args = " ".join(shlex.quote(p) for p in writable)
        helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_landlock_helper.py")
        return (
            f"python3 {shlex.quote(helper)} --writable {writable_args} "
            f"-- /bin/bash -c {shlex.quote(command)}"
        )


_landlock_helper_src = None  # 懒加载


class SeatlandSandboxProvider:
    """P0-B macOS 后端：sandbox-exec（Seatland，轻量系统级沙箱，无需 root）。

    仅 macOS；Linux 上 available() 恒 False。sandbox-exec 用 profile 表达式
    限制文件写面到 (wd + /tmp + extra)，网络白名单例外同 bwrap 语义。
    """

    name = "seatland"

    def available(self) -> bool:
        import platform
        return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None

    def probe_reason(self) -> str | None:
        import platform
        if sys.platform != "darwin":
            return "Seatland 仅 macOS"
        return None if self.available() else "sandbox-exec 不在 PATH"

    def wrap(
        self,
        command: str,
        working_dir: Path | None = None,
        allow_network: bool = False,
        extra_writable_dirs: list[str] | None = None,
    ) -> str:
        if not self.available():
            return command
        wd = str(working_dir or Path.cwd())
        # 2026-09-24（V1 纵深）：同 landlock 层——extra_writable_dirs 过白名单钳制
        safe_extra = [d for d in (extra_writable_dirs or []) if is_safe_writable_dir(d)]
        writable = [wd, "/tmp"] + safe_extra
        profile_lines = [
            "(version 1)",
            "(allow default)",
        ]
        for p in writable:
            profile_lines.append(f'(allow file-write* (path-substring {shlex.quote(p)}))')
        if not allow_network:
            profile_lines.insert(1, "(deny network-connect network-bind network-listen)")
        profile = "\n".join(profile_lines)
        return f"sandbox-exec -p {shlex.quote(profile)} -- /bin/bash -c {shlex.quote(command)}"


def create_default_sandbox_provider() -> SandboxProvider:
    """默认后端选择（P0-B 2026-09-22 三级降级链）：bwrap → landlock → noop。

    优先级：bwrap（最强：mount+net namespace 全隔离）可用即用；
    bwrap 不可用（容器/受限 user namespace）时，Linux 上探 Landlock LSM
    （无 user namespace 依赖，细粒度文件写面降级），可用则用 landlock；
    都不可用落 noop（fail-safe 降级，策略约束路径由调用方 fail-closed）。
    macOS 上 bwrap 天然缺席，探 seatland（sandbox-exec）。

    P12: 与 P5 的 provider/tool 对称 —— 创建默认后端时同步注册到进程内
    SeamRegistry（SeamType.SANDBOX 槽位），建立「sandbox 后端也可经
    SeamRegistry.get(SANDBOX, name) 查询」的统一视图。bash.py 运行时路径
    仍走 CapabilitySeam（lacp 跨进程缝，含治理/审计包装），本注册是
    查询视图补充（热拔插状态可观测），不是替代。
    """
    bwrap = BwrapSandboxProvider()
    if bwrap.available():
        SeamRegistry.register(SeamType.SANDBOX, bwrap.name, bwrap)
        # P13 (S2): 消费侧统一查询视图 —— 注册 "default" 别名指向选中的后端，
        # 供 bash.py 运行时经 SeamRegistry.get_optional(SANDBOX, "default") 获取。
        # 热拔插语义：外部 register(SANDBOX, "default", new_provider) 覆盖后，
        # 下个 bash 命令即用新后端（无需重启、无需动 bash.py）。
        SeamRegistry.register(SeamType.SANDBOX, "default", bwrap)
        return bwrap
    # P0-B: bwrap 缺席 → 平台降级链（Linux: landlock / macOS: seatland）
    if sys.platform == "linux":
        ll = LandlockSandboxProvider()
        if ll.available():
            SeamRegistry.register(SeamType.SANDBOX, ll.name, ll)
            SeamRegistry.register(SeamType.SANDBOX, "default", ll)
            logger.info(
                "bwrap 不可用，降级 Landlock（不可用原因: %s）",
                bwrap.probe_reason(),
            )
            return ll
        logger.warning(
            "bwrap 不可用（%s）且 Landlock 不可用（%s），sandbox 降级 noop（黑名单+资源限制仍生效）",
            bwrap.probe_reason(),
            LandlockSandboxProvider().probe_reason(),
        )
    elif sys.platform == "darwin":
        sl = SeatlandSandboxProvider()
        if sl.available():
            SeamRegistry.register(SeamType.SANDBOX, sl.name, sl)
            SeamRegistry.register(SeamType.SANDBOX, "default", sl)
            logger.info("bwrap 不可用（macOS），降级 Seatland（sandbox-exec）")
            return sl
        logger.warning(
            "bwrap/seatland 均不可用（sandbox-exec 缺失），sandbox 降级 noop（黑名单+资源限制仍生效）"
        )
    else:
        logger.warning("bwrap 不可用，sandbox 后端降级为 noop（黑名单+资源限制仍生效）")
    noop = NoopSandboxProvider()
    SeamRegistry.register(SeamType.SANDBOX, noop.name, noop)
    SeamRegistry.register(SeamType.SANDBOX, "default", noop)
    return noop
