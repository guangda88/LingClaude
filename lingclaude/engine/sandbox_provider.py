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
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from lingclaude.core.seam import SeamRegistry, SeamType

logger = logging.getLogger(__name__)

# bwrap 可用性探测结果缓存：(None=未探测, (bool, reason))
_bwrap_probe_cache: tuple[bool, str] | None = None


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


def create_default_sandbox_provider() -> SandboxProvider:
    """默认后端：bwrap 可用时用之，否则 noop（fail-safe 降级，策略约束路径由调用方 fail-closed）。

    P12: 与 P5 的 provider/tool 对称 —— 创建默认后端时同步注册到进程内
    SeamRegistry（SeamType.SANDBOX 槽位），建立「sandbox 后端也可经
    SeamRegistry.get(SANDBOX, name) 查询」的统一视图。bash.py 运行时路径
    仍走 CapabilitySeam（lacp 跨进程缝，含治理/审计包装），本注册是
    查询视图补充（热拔插状态可观测），不是替代。
    """
    bwrap = BwrapSandboxProvider()
    if bwrap.available():
        SeamRegistry.register(SeamType.SANDBOX, bwrap.name, bwrap)
        return bwrap
    logger.warning("bwrap 不可用，sandbox 后端降级为 noop（黑名单+资源限制仍生效）")
    noop = NoopSandboxProvider()
    SeamRegistry.register(SeamType.SANDBOX, noop.name, noop)
    return noop
