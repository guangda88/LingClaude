"""Sandbox Provider Protocol — bash 沙箱后端抽象（灵元尺子：变化变成插片）。

解耦前：bash.py._sandbox_command 直接调用 bwrap（焊死单后端）。
解耦后：SandboxProvider Protocol + BwrapSandboxProvider（默认）/ NoopSandboxProvider，
        bash.py 通过 CapabilitySeam(SANDBOX_SEAM) 获取 provider，后端可替换。

对应 DSH sandbox 三态/四后端：本实现为 bwrap 单后端 + noop 降级，
扩展点留给 firejail / gVisor 等自定义 Provider。
"""
from __future__ import annotations

import logging
import shlex
import shutil
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# bwrap 可用性探测结果缓存（None=未探测，False=本环境不可用）
_bwrap_probe_cache: bool | None = None


def _bwrap_probe(bwrap: str) -> bool:
    """探测 bwrap 在本环境是否真正可用（一次性，结果缓存）。

    bwrap 依赖 user namespaces / network namespaces，容器或受限环境即使有
    二进制也无法运行——必须探测，不能反复失败。
    """
    global _bwrap_probe_cache
    if _bwrap_probe_cache is not None:
        return _bwrap_probe_cache
    import subprocess

    # 韧性:探测失败重试一次(会话启动期 MCP/后台线程抢资源,超时可能是瞬时的)。
    # 只有连续两次失败才缓存 False,避免单次抖动让整个会话永久降级非沙箱。
    for _attempt in range(2):
      try:
        # 修复:原探测命令在 merged-usr 系统(/bin -> usr/bin 符号链接)上自毁——
        # 先绑 /usr 再绑 /bin 会用符号链接目标覆盖 /usr 内路径,execvp /bin/true
        # 必然 ENOENT,导致沙箱被永远误判不可用。改为整根只读绑定。
        proc = subprocess.run(  # nosec B603 — 探测固定白名单命令
            [bwrap, "--ro-bind", "/", "/", "--", "/bin/true"],
            capture_output=True,
            timeout=15,
        )
        _bwrap_probe_cache = proc.returncode == 0
        if _bwrap_probe_cache:
            break
      except Exception:  # noqa: BLE001 — 探测失败视为不可用
          _bwrap_probe_cache = False
    return _bwrap_probe_cache


@runtime_checkable
class SandboxProvider(Protocol):
    """沙箱后端协议（灵元：插片可换）。

    实现方需提供：
    - name: 后端名（如 bwrap / noop / firejail）
    - available() -> bool: 后端是否可用（探测）
    - wrap(command: str, working_dir: Path | None) -> str: 返回包裹后的命令
    """

    name: str

    def available(self) -> bool:
        """后端是否可用（bwrap 探测 / noop 恒真）。"""
        ...

    def wrap(self, command: str, working_dir: Path | None = None) -> str:
        """包裹命令（原样返回 = 无沙箱；加前缀 = 沙箱）。"""
        ...


class BwrapSandboxProvider:
    """默认实现：bwrap 沙箱包裹（只读系统路径 + 可写工作目录 + 网络隔离）。"""

    name = "bwrap"

    def __init__(self) -> None:
        self._bwrap = shutil.which("bwrap")

    def available(self) -> bool:
        return self._bwrap is not None and _bwrap_probe(self._bwrap)

    def wrap(self, command: str, working_dir: Path | None = None) -> str:
        """bwrap 包裹命令（策略对标 DSH read-only/workspace-write）。"""
        if not self.available():
            return command
        wd = str(working_dir or Path.cwd())
        parts = [
            self._bwrap,
            "--unshare-net",
            # merged-usr 安全绑定:整根只读(/bin 是 usr/bin 的符号链接,
            # 逐目录绑定会自毁路径),再按需放开工作目录与 /tmp。
            "--ro-bind", "/", "/",
            # /dev/null 必须可写(ro-bind 使 /dev 只读,导致 2>/dev/null、
            # git/pytest 等 EACCES——2026-09-06 会话"权限不足"主根因)。
            # --dev-bind 独立于 ro-bind 重新以读写挂载该设备节点。
            "--dev-bind", "/dev/null", "/dev/null",
            "--bind", wd, wd,
            "--bind", "/tmp", "/tmp",
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

    def wrap(self, command: str, working_dir: Path | None = None) -> str:
        return command


def create_default_sandbox_provider() -> SandboxProvider:
    """默认后端：bwrap 可用时用之，否则 noop（fail-safe 降级，策略约束路径由调用方 fail-closed）。"""
    bwrap = BwrapSandboxProvider()
    if bwrap.available():
        return bwrap
    logger.warning("bwrap 不可用，sandbox 后端降级为 noop（黑名单+资源限制仍生效）")
    return NoopSandboxProvider()
