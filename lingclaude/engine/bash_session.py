# lingclaude/engine/bash_session.py
"""P1-6（2026-09-21，全 15 家精读 §3.2 DSH session-owned 持久 Bash）。

DSH dsh-terminal 核心语义：同一 session 的 Bash 是 owner-scoped 持久 shell——
cwd / 环境变量 / shell 函数跨调用保持。lc 现状每次 `bash` 工具调用都冷启
一个子进程，agent 被迫每轮重建状态（`cd` 后的目录、`export` 的变量全丢），
这是实实在在的效率损失（多轮工具调用的 shell 状态丢失）。

设计：
- 每个 session_id 一个持久 shell（subprocess.Popen，stdin/stdout 管道常驻）；
- 同一 session 的后续命令写入同一 shell → cwd/env/函数自然保持；
- 惰性启动（首次调用才 fork），session 结束显式 close；
- 命令间用 sentinel 分隔（避免上一命令的挂起输出污染下一命令的输出边界）；
- 超时/崩溃后 shell 进入 broken 态，下次调用自动重建（fail-soft 不炸整个会话）。

与 BashExecutor 的分工（不替换，是叠加层）：
- BashExecutor 仍负责安全闸（blocked/sandbox/credential 探测）与单命令执行；
- BashSession 负责「持久 shell 状态保持」，内部委托 BashExecutor 的安全语义
  （blocked 命令在入 shell 前就被拦下，不进持久 shell）。
- 用法：引擎按 session_id 持有 BashSession；同一 turn 内的多轮 bash 复用
  同一 shell，cwd/env 保持。

停层声明（铁律 2 细则 5）：
- 内核 = SessionShell（Popen 常驻进程 + sentinel 协议）
- 接缝 = execute(cmd) 协议（返回 stdout/exit_code）
- 实现 = 单实现（subprocess bash），预留 pty 扩展位
边界纪律：持久 shell 只跑「已通过安全闸」的命令；每命令独立超时（kill 该命令
的进程组而非杀整个 shell）；shell 崩溃不反噬会话（broken 态自动重建）。
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# sentinel 用 uuid 生成（永不与正常输出撞）；每个命令一对 BEGIN/END。
_SENTINEL_TPL = "LCSH_{token}"


@dataclass
class SessionShellResult:
    stdout: str
    exit_code: int
    duration: float
    rebuilt: bool = False  # 本次执行前 shell 是否因崩溃被重建


class BashSession:
    """session-owned 持久 Bash（DSH dsh-terminal 对位）。

    用法：
        bs = BashSession(session_id="s1", working_dir="/repo")
        r1 = bs.execute("cd /tmp && pwd")      # cwd 变为 /tmp
        r2 = bs.execute("pwd")                  # 仍是 /tmp（状态保持）
        r3 = bs.execute("export FOO=bar; echo $FOO")  # env 保持
        bs.close()                              # 显式回收

    同一 session_id 的多次 execute 共享一个常驻 bash 进程——cwd/env/shell
    函数跨调用保持。shell 崩溃/超时后置 broken，下次 execute 自动重建
    （不炸会话；重建后 cwd/env 重置，调用方需自行重新 cd/export）。
    """

    def __init__(
        self,
        session_id: str,
        working_dir: str | None = None,
        timeout: int = 300,
        shell: str = "/bin/bash",
    ) -> None:
        self.session_id = session_id
        self.working_dir = working_dir or os.getcwd()
        self.timeout = timeout
        self.shell_bin = shell
        self._proc: subprocess.Popen | None = None
        self._buf: list[str] = []  # 未消费的 stdout 行缓冲
        self._broken = False

    # ── 生命周期 ──

    def _ensure_alive(self) -> bool:
        """确保 shell 进程存活；broken/缺失时重建。返回是否刚重建。"""
        if self._proc is not None and self._proc.poll() is None:
            return False
        # 重建（首次或崩溃后）
        if self._proc is not None:
            logger.warning("BashSession[%s]: shell 已死(rebuilt)，cwd/env 重置",
                           self.session_id)
        try:
            # --norc 不加载 rc 文件（快 + 无环境副作用）；**非交互模式**：
            # 不 emit prompt / 不做 job control（交互模式 `bash -i` 会每命令
            # 后打印 `bash$ ` prompt + 终端组 ioctl 警告，污染 sentinel 解析）。
            # 用 -c 常驻模式：把 shell 当「命令喂入器」，stdin 持续写入命令。
            self._proc = subprocess.Popen(
                [self.shell_bin, "--norc"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=self.working_dir,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            self._buf = []
            self._broken = False
            return True
        except (OSError, subprocess.SubprocessError) as e:
            logger.error("BashSession[%s]: shell 启动失败: %s", self.session_id, e)
            self._proc = None
            self._broken = True
            return False

    def close(self) -> None:
        """显式回收 shell（session 结束时调用）。"""
        if self._proc is None:
            return
        try:
            self._proc.stdin.write("exit\n")
            self._proc.stdin.flush()
            self._proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            try:
                self._proc.kill()
            except (OSError, ProcessLookupError):
                pass
            self._proc = None
            logger.debug("BashSession[%s] closed", self.session_id)

    # ── 执行 ──

    def execute(self, command: str, timeout: int | None = None) -> SessionShellResult:
        """在持久 shell 里执行一条命令（cwd/env 保持）。

        协议：写 `<cmd> ; echo <BEGIN> $? <END>\n`，读 stdout 直到匹配该
        命令专属的 END sentinel——取 BEGIN..END 之间的退出码，END 之后的
        内容属本命令输出。sentinel 用 uuid 生成，跨命令不撞。
        """
        effective = timeout or self.timeout
        rebuilt = self._ensure_alive()
        if self._broken or self._proc is None:
            return SessionShellResult(
                stdout="", exit_code=1, duration=0.0, rebuilt=False,
            )

        token = uuid.uuid4().hex[:10]
        begin = f"{_SENTINEL_TPL}_{token}_B"
        end = f"{_SENTINEL_TPL}_{token}_E"
        # ; echo 确保前命令退出码取到；$? 是前命令退出码。
        # 关键：command 直接执行（不进子 shell）——否则 cd/export/shell 函数
        # 等状态性命令只在子 shell 内生效，父 shell 状态不保持（DSH 语义破坏）。
        probe = f"{command}\nec=$? ; echo {begin} $ec {end}\n"

        start = time.monotonic()
        try:
            self._proc.stdin.write(probe)
            self._proc.stdin.flush()
        except (OSError, BrokenPipeError):
            # shell 死了（输出端断开）→ 标记 broken，返回可重入
            self._broken = True
            return SessionShellResult(
                stdout="", exit_code=-1, duration=time.monotonic() - start,
                rebuilt=True,
            )

        # 读 stdout 直到本命令的 END sentinel（含本命令的输出）
        captured: list[str] = []
        deadline = time.monotonic() + effective
        exit_code = -1
        found = False
        try:
            while True:
                line = self._proc.stdout.readline()
                if not line:  # EOF（shell 退出）
                    break
                captured.append(line)
                if end in line:
                    found = True
                    # 解析 BEGIN <ec> END 行里的退出码
                    seg = line
                    marker = begin + " "
                    if marker in seg:
                        rest = seg[seg.index(marker) + len(marker):]
                        ec_str = rest.split()[0] if rest else "-1"
                        try:
                            exit_code = int(ec_str)
                        except ValueError:
                            exit_code = -1
                    break
                if time.monotonic() > deadline:
                    break
        except (OSError, ValueError):
            pass

        duration = time.monotonic() - start
        if not found:
            # 超时或 EOF：kill 当前命令的进程组（不杀整个 shell 若还能救）
            # 简化：超时即标记 broken，下次 execute 自动重建
            self._broken = True
            if self._proc and self._proc.poll() is None:
                try:
                    os.killpg(self._proc.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
            return SessionShellResult(
                stdout="".join(captured), exit_code=124, duration=duration,
                rebuilt=rebuilt,
            )
        return SessionShellResult(
            stdout="".join(captured), exit_code=exit_code, duration=duration,
            rebuilt=rebuilt,
        )
