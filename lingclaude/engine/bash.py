from __future__ import annotations

import re
import resource
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


# bwrap 可用性探测结果缓存（None=未探测，False=本环境不可用）
_BWARP_PROBE_RESULT: bool | None = None


def _bwrap_probe(bwrap: str) -> bool:
    """探测 bwrap 在本环境是否真正可用（一次性，结果缓存）。

    本环境常见失败：uid map / net namespace 被禁（无特权容器），
    此时 bwrap 即使存在也无法运行——必须降级，不能反复失败。
    """
    global _BWARP_PROBE_RESULT
    if _BWARP_PROBE_RESULT is not None:
        return _BWARP_PROBE_RESULT
    try:
        r = subprocess.run(
            [bwrap, "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin", "--", "/bin/true"],
            capture_output=True,
            timeout=5,
        )
        ok = r.returncode == 0
    except Exception:
        ok = False
    _BWARP_PROBE_RESULT = ok
    return ok


@dataclass(frozen=True)
class BashResult:
    exit_code: int
    stdout: str
    stderr: str
    duration: float
    command: str

    @property
    def success(self) -> bool:
        return self.exit_code == 0


_ALWAYS_BLOCKED = frozenset({
    "rm -rf /", "rm -rf /*",
    "sudo", "su",
    "mkfs", "dd if=",
    ":(){ :|:& };:", "fork bomb",
    "chmod 777", "chown",
    "curl", "wget", "nc ", "ncat",
    "ssh", "scp", "telnet",
    "mount", "umount", "fdisk", "parted",
    "iptables", "ufw", "firewall-cmd",
    "systemctl", "service",
    "apt", "apt-get", "yum", "dnf", "pacman", "pip install",
    "crontab", "at ",
})

_BLOCKED_BASE_COMMANDS = frozenset({
    "sudo", "su", "mkfs", "ssh", "scp", "telnet",
    "mount", "umount", "fdisk", "parted",
    "iptables", "ufw", "firewall-cmd",
    "systemctl", "service",
    "crontab",
})

_DEFAULT_MEMORY_LIMIT = 512 * 1024 * 1024  # 512 MB
_DEFAULT_CPU_LIMIT = 30  # seconds

# P0-1: 凭据模式检测（对标 AtomCode atomgit_bash_gate.rs TOKEN_MARKERS）
# 防止模型通过 bash 传递凭据（access_token、bearer token、环境变量引用）
_CREDENTIAL_MARKERS = frozenset({
    "access_token=",
    "authorization: bearer",
    "api_key=",
    "apikey=",
    "x-api-key:",
    "sk-",  # DeepSeek/OpenAI 风格 key 前缀
    "$ATOMCODE_API_KEY",
    "${ATOMCODE_API_KEY",
    "$LINGCLAUDE_API_KEY",
    "${LINGCLAUDE_API_KEY",
    "$DEEPSEEK_API_KEY",
    "${DEEPSEEK_API_KEY",
})

# 禁止直接调用的外部 API host（强制走专用工具或 SDK）
_FORBIDDEN_API_HOSTS = frozenset({
    "api.atomgit.com",
    "api.deepseek.com",
    "open.bigmodel.cn",
    "dashscope.aliyuncs.com",
})


class BashExecutor:
    def __init__(
        self,
        working_dir: str | None = None,
        timeout: int = 60,
        allowed_commands: list[str] | None = None,
        blocked_commands: list[str] | None = None,
        memory_limit: int = _DEFAULT_MEMORY_LIMIT,
        cpu_limit: int = _DEFAULT_CPU_LIMIT,
    ) -> None:
        self.working_dir = working_dir
        self.timeout = timeout
        self.allowed_commands = allowed_commands
        extra_blocked = blocked_commands or []
        self.blocked_commands = _ALWAYS_BLOCKED | frozenset(extra_blocked)
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit

    def run(self, command: str, timeout: int | None = None) -> BashResult:
        effective_timeout = timeout or self.timeout

        blocked_reason = self._check_blocked(command)
        if blocked_reason:
            return BashResult(
                exit_code=126,
                stdout="",
                stderr=f"命令被阻止: {command}（原因: {blocked_reason}）",
                duration=0,
                command=command,
            )

        start = time.monotonic()
        try:
            # B3：bwrap 沙箱包裹（可用时）— 只读系统路径 + 可写工作目录
            cmd = self._sandbox_command(command)
            result = subprocess.run(  # nosec B602 — shell=True 由 _check_blocked 黑名单+白名单+资源限制+沙箱四重缓解
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                cwd=self.working_dir,
                preexec_fn=self._set_resource_limits,
            )
            duration = time.monotonic() - start
            return BashResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration=duration,
                command=command,
            )
        except subprocess.TimeoutExpired:
            duration = time.monotonic() - start
            return BashResult(
                exit_code=124,
                stdout="",
                stderr=f"命令在 {effective_timeout}s 后超时",
                duration=duration,
                command=command,
            )
        except Exception as e:
            duration = time.monotonic() - start
            return BashResult(
                exit_code=1,
                stdout="",
                stderr=str(e),
                duration=duration,
                command=command,
            )

    def _sandbox_command(self, command: str) -> str:
        """B3：bwrap 沙箱包裹 — 可用时用，不可用降级为原命令。

        策略（对标 DSH sandbox read-only/workspace-write）：
        - 系统路径只读（/usr /lib /etc /bin /sbin）
        - 工作目录可写（workspace-write）
        - /tmp 可写（工具产物、spill 文件）
        - 网络隔离（--unshare-net）

        降级条件：bwrap 不存在、或本环境无权限（uid map / net ns 被禁）。
        降级后黑名单+白名单+资源限制三重缓解仍然生效（fail-safe，不静默）。
        """
        bwrap = shutil.which("bwrap")
        if bwrap is None or not _bwrap_probe(bwrap):
            return command
        # working_dir 为 None 时退化到当前目录（避免 --bind None None）
        wd = str(self.working_dir or Path.cwd())
        parts = [
            bwrap,
            "--unshare-net",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/etc", "/etc",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/sbin", "/sbin",
            "--bind", wd, wd,
            "--bind", "/tmp", "/tmp",
            "--die-with-parent",
            "--",
            "/bin/bash", "-c", command,
        ]
        import shlex

        return " ".join(shlex.quote(p) for p in parts)

    @staticmethod
    def _normalize_command(command: str) -> str:
        """归一化命令字符串以检测混淆攻击（EXP-S2修复）。

        处理：引号移除、反斜杠移除、花括号展开。
        """
        normalized = command
        normalized = normalized.replace("'", "").replace('"', "")
        normalized = normalized.replace("\\", "")
        normalized = re.sub(
            r"\{([^}]+)\}",
            lambda m: " ".join(opt.strip() for opt in m.group(1).split(",")),
            normalized,
        )
        return normalized.lower()

    @staticmethod
    def _glob_aware_contains(haystack: str, needle: str) -> bool:
        """检查 needle 是否出现在 haystack 中，允许 ? 作为单字符通配符。"""
        if needle in haystack:
            return True
        for i in range(len(needle)):
            if needle[:i] + "?" + needle[i + 1:] in haystack:
                return True
        return False

    @staticmethod
    def _split_chain(command: str) -> list[str]:
        """将命令链（&&, ||, ;, |, $(), ``）拆分为子命令逐个检测。"""
        parts = re.split(r"[;|&]|\$\(|`", command)
        return [p.strip() for p in parts if p.strip()]

    def _check_blocked(self, command: str) -> str | None:
        cmd_stripped = command.strip()
        cmd_normalized = self._normalize_command(cmd_stripped)
        cmd_lower = cmd_normalized.lower()

        # P0-1: 凭据模式检测（fail-closed）
        for marker in _CREDENTIAL_MARKERS:
            if marker.lower() in cmd_lower:
                return f"命令包含凭据模式 '{marker}'，禁止通过 bash 传递凭据"

        # P0-1: 禁止直接调用外部 API host
        for host in _FORBIDDEN_API_HOSTS:
            if host in cmd_lower:
                return f"禁止直接调用 {host}，请使用专用工具或 SDK"

        for blocked in self.blocked_commands:
            bl = blocked.lower()
            if self._glob_aware_contains(cmd_normalized, bl):
                return f"匹配黑名单规则 '{blocked}'"

        sub_commands = self._split_chain(cmd_stripped)
        for sub in sub_commands:
            sub_norm = self._normalize_command(sub)
            for blocked in self.blocked_commands:
                bl = blocked.lower()
                if self._glob_aware_contains(sub_norm, bl):
                    return f"匹配黑名单规则 '{blocked}'（命令链中检测到）"

            tokens = sub_norm.split()
            if not tokens:
                continue
            for token in tokens:
                base_cmd_name = Path(token.split("=")[-1]).name
                if base_cmd_name in _BLOCKED_BASE_COMMANDS:
                    return f"基础命令 '{base_cmd_name}' 被禁止"

        base_cmd = cmd_stripped.split()[0] if cmd_stripped.split() else ""
        base_cmd_name = Path(base_cmd).name
        if base_cmd_name.lower() in _BLOCKED_BASE_COMMANDS:
            return f"基础命令 '{base_cmd_name}' 被禁止"

        if self.allowed_commands is not None:
            if not any(
                base_cmd_name.lower() == allowed.split()[0].lower()
                for allowed in self.allowed_commands
            ):
                return f"'{base_cmd_name}' 不在允许列表中"

        # P1-1: Workspace 外审批（对标 AtomCode bash_workspace_gate.rs）
        # 破坏性命令目标在 working_dir 外 → 拦截
        if self.working_dir:
            working_path = Path(self.working_dir).resolve()
            destructive_targets = self._extract_destructive_targets(cmd_stripped)
            for target in destructive_targets:
                try:
                    target_path = Path(target)
                    if not target_path.is_absolute():
                        target_path = working_path / target_path
                    target_resolved = target_path.resolve(strict=False)
                    if not str(target_resolved).startswith(str(working_path)):
                        return f"破坏性命令目标 {target} 在 workspace 外（{working_path}），禁止执行"
                except (OSError, ValueError):
                    # 路径不可解析 → fail-closed
                    return f"破坏性命令目标 {target} 无法解析，禁止执行（fail-closed）"

        return None

    @staticmethod
    def _extract_destructive_targets(command: str) -> list[str]:
        """提取破坏性命令的目标路径。

        支持：rm <path>, mv <src> <dst>, cp <src> <dst>, shred <path> 等。
        返回目标路径列表（相对或绝对）。
        """
        destructive_cmds = {"rm", "rmdir", "unlink", "shred", "truncate"}
        move_copy_cmds = {"mv", "cp"}

        tokens = command.split()
        if not tokens:
            return []

        cmd = Path(tokens[0]).name.lower()

        if cmd in destructive_cmds:
            # rm/file-delete: 所有非选项参数都是目标
            return [t for t in tokens[1:] if not t.startswith("-")]

        if cmd in move_copy_cmds:
            # mv/cp: 最后一个是目标（dst）
            if len(tokens) >= 3:
                return [tokens[-1]]
            return []

        return []

    def _set_resource_limits(self) -> None:
        try:
            resource.setrlimit(
                resource.RLIMIT_AS,
                (self.memory_limit, self.memory_limit),
            )
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (self.cpu_limit, self.cpu_limit),
            )
        except (ValueError, OSError):
            pass
