from __future__ import annotations

import logging
import re
import resource
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lingclaude.lacp.sandbox_policy import SandboxPolicy


class SandboxUnavailableError(RuntimeError):
    """sandbox_policy 要求 bwrap 但环境不可用时抛出（fail-closed）。

    接线修复：bash.py:188 此前静默降级为无沙箱，违反 fail-closed 原则。
    """


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
        # 修复:原探测命令在 merged-usr 系统(/bin -> usr/bin 符号链接)上自毁——
        # 先绑 /usr 再绑 /bin 会用符号链接目标覆盖 /usr 内路径,execvp /bin/true
        # 必然 ENOENT,导致沙箱被永远误判不可用。改为整根只读绑定。
        r = subprocess.run(
            [bwrap, "--ro-bind", "/", "/", "--", "/bin/true"],
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
    # 2026-09-05 事故加固:lingclaude 会话曾执行 rm -rf .lingclaude,
    # 导致运行时数据(行为/知识/会话库)丢失。关键目录一律禁止 rm。
    "rm -rf .lingclaude", "rm -rf .lingclaude/",
    "rm -rf .git", "rm -rf .git/",
    "rm -rf docs", "rm -rf knowledge",
    "rm -rf lingclaude", "rm -rf src",
    "rm -rf ~", "rm -rf ~/",
    "rm -rf .crush", "rm -rf data",
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
    "crontab",
    # 注：定时任务命令 "at" 不入全文本规则。它是极常见英文子串
    # （cat/stat 含 "at"；grep "at "、echo "at home" 含独立 "at" token），
    # 全文本/全 token 匹配必然误伤（2026-09-08 事故：grep 检索 'at '
    # 字面量被拦，stat 连坐）。改由 _BLOCKED_LEADING_COMMANDS 在「命令名
    # 位置」精确拦截，不连坐参数。
})

# 仅在「命令名位置」（整条命令首 token / 链式子命令首 token）拦截的命令。
# 与 _BLOCKED_BASE_COMMANDS 的区别：后者在子命令中遍历 *所有* token（保守
# 连坐参数，适用于 sudo/su 等危险词）；本集合只匹配命令名本身、不连坐参数
# ——专为 "at" 这类短词/常见英文词命令设计，避免 grep "at" / echo at 误拦。
_BLOCKED_LEADING_COMMANDS = frozenset({
    "at",  # at(1) 定时任务调度，与 crontab 同类
})

_BLOCKED_BASE_COMMANDS = frozenset({
    "sudo", "su", "mkfs", "ssh", "scp", "telnet",
    "mount", "umount", "fdisk", "parted",
    "iptables", "ufw", "firewall-cmd",
    "systemctl", "service",
    "crontab",
})

# 网络白名单（2026-09-11 白名单化）：仅这些「可信 git 远程操作」在沙箱中开放网络
# （bwrap wrap(allow_network=True)，不再注入 --unshare-net）。其余命令仍网络隔离。
# 设计：git 本身不在黑名单（_check_blocked 放行），git 内部调 ssh 是子进程、
# 命令行无 ssh token 不触发 _BLOCKED_BASE_COMMANDS；本白名单只决定「是否去掉网络隔离」。
_NETWORK_ALLOWED_COMMANDS = (
    "git push",
    "git fetch",
    "git pull",
    "git clone",
    "git ls-remote",
    "git remote",
)

# 透明前缀：这些包装命令本身不访问网络，剥离后不影响白名单判定。
# 2026-09-12 修复：agent 习惯给 git 远程命令加 `timeout N`/`env` 前缀，
# 导致全链白名单判定 False → 整条被 `--unshare-net` 隔离 → git 无法联网。
# 仅剥离「纯包装」前缀；前缀后的实际命令仍逐个全链判定（保持 fail-closed）。
_TRANSPARENT_PREFIXES = (
    "timeout",
    "env",
    "nice",
    "stdbuf",
    "setsid",
    "nohup",
    "command",
    "cd",
)


def _strip_transparent_prefix(command: str) -> str:
    """剥离 timeout/env 等透明包装前缀（含其参数），返回实际命令。

    例：``timeout 15 git ls-remote ...`` → ``git ls-remote ...``
    ``env GIT_TERMINAL_PROMPT=0 git fetch`` → ``git fetch``
    ``nice -n 10 git pull`` → ``git pull``
    非透明前缀（echo/head/grep 等）原样返回 → 整条隔离（fail-closed）。
    """
    cmd = command.strip()
    while True:
        tokens = cmd.split()
        if not tokens:
            return cmd
        head = tokens[0].lower()
        if head not in _TRANSPARENT_PREFIXES:
            return cmd
        if head == "cd":
            # cd 是纯导航（shell 内置、无网络副作用）。_split_chain 已把
            # `cd /x && git push` 拆成独立段，这里只需吃掉 `cd /path` 并返回空段
            # （空段在 _is_network_allowed 中被 continue 跳过）。
            return ""
        # 剥离前缀本身
        rest = " ".join(tokens[1:]).strip()
        # 若前缀带独立参数（timeout 15 / env FOO=1 / nice -n 10），一并剥离
        while rest:
            rtoks = rest.split()
            rhead = rtoks[0]
            if head == "timeout" and rhead.isdigit():
                rest = " ".join(rtoks[1:]).strip()
                continue
            if head == "timeout" and rhead in ("-k", "-s", "--signal", "-v"):
                rest = " ".join(rtoks[2:]).strip() if len(rtoks) > 1 else ""
                continue
            if head == "nice" and rhead in ("-n", "--adjustment"):
                rest = " ".join(rtoks[2:]).strip() if len(rtoks) > 1 else ""
                continue
            if head in ("env", "nohup", "setsid", "stdbuf", "command"):
                # env FOO=1 BAR=2 cmd ... / stdbuf -oL cmd / command -v cmd
                if rhead.startswith(("-", "=")) or "=" in rhead and not rhead.startswith("-"):
                    rest = " ".join(rtoks[1:]).strip()
                    continue
            break
        cmd = rest
    return cmd


def _is_network_allowed(command: str) -> bool:
    """判断命令是否命中网络白名单（全链判定，仅 git 远程/认证类操作）。

    修复 2026-09-11（复合命令绕过）：此前仅对整条命令前缀匹配，导致
    ``git push x && wget evil.sh`` 整条放行网络（fail-open）。现复用
    ``_split_chain`` 拆分 ``&&/||/;/|/$()/``` `` 后逐个判定，
    要求**所有子命令都命中**白名单才返回 True（全链白名单，缺一即隔离）。

    修复 2026-09-12（透明前缀误伤）：``timeout 15 git push`` / ``env FOO=1 git fetch``
    因 timeout/env 不在白名单而被整条隔离 → git 无法联网。现先剥离透明包装前缀
    再全链判定；剥离后仍含非白名单子命令（如 ``timeout 15 git push && wget x``）→
    整条隔离（fail-closed 保持）。

    命中返回 True -> _sandbox_command 传 allow_network=True。
    """
    parts = BashExecutor._split_chain(command)
    if not parts:
        return False
    for sub in parts:
        norm = _strip_transparent_prefix(sub).lower().replace("'", "").replace('"', "")
        if not norm:
            continue  # cd /path 等纯导航段剥离后为空，跳过（不参与白名单判定）
        hit = any(
            norm == allowed or norm.startswith(allowed + " ")
            for allowed in _NETWORK_ALLOWED_COMMANDS
        )
        if not hit:
            return False
    return True


# 网络类错误特征（2026-09-12 自动降级判定）：DNS 解析失败 / 网络不可达 / 连接被拒。
_NETWORK_FAILURE_PATTERNS = (
    "could not resolve host",
    "could not resolve",
    "network is unreachable",
    "network unreachable",
    "no route to host",
    "connection refused",
    "connection timed out",
    "temporary failure in name resolution",
    "name or service not known",
    "getaddrinfo failed",
    "device or resource busy",  # seccomp 拦 connect 的典型报错
    "connection reset by peer",
    "ssl: certificate verify failed",
    "ssh: connect to host",
    "fatal: unable to access",
    "fatal: 无法访问",
    "unable to resolve host",
    "无法解析",
    "解析失败",
)


def _looks_like_network_failure(text: str) -> bool:
    """判断命令输出/错误是否属于网络类失败（供自动降级重试判定）。"""
    lowered = text.lower()
    return any(pattern in lowered for pattern in _NETWORK_FAILURE_PATTERNS)


_DEFAULT_MEMORY_LIMIT = 512 * 1024 * 1024  # 512 MB
_DEFAULT_CPU_LIMIT = 30  # seconds
# 修复 2026-09-11：白名单 git 远程操作内存限额放宽（git 需 ~500MB 堆，512MB 撞限）
_NETWORK_ALLOWED_MEMORY_LIMIT = 1024 * 1024 * 1024  # 1 GB

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
        sandbox_policy: SandboxPolicy | None = None,
    ) -> None:
        self.working_dir = working_dir
        self.timeout = timeout
        self.allowed_commands = allowed_commands
        extra_blocked = blocked_commands or []
        self.blocked_commands = _ALWAYS_BLOCKED | frozenset(extra_blocked)
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.sandbox_policy = sandbox_policy

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
            if cmd != command:
                # bwrap 已包裹：bwrap 自身是 argv 边界，shell=True 执行不会二次解析
                # 内层命令的引号/命令替换（P1-1 审计修复）
                result = subprocess.run(  # nosec B602 — 仅 bwrap 包裹路径走 shell=True，_check_blocked 四重缓解仍生效
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=effective_timeout,
                    cwd=self.working_dir,
                    preexec_fn=lambda: self._set_resource_limits(command),
                )
            else:
                # 无 bwrap：显式使用 bash 而非 sh（dash），避免 bash 语法兼容问题
                # shell=True 默认用 /bin/sh（本环境是 dash），不支持数组、() 等语法
                result = subprocess.run(  # nosec B602 — shell=True 由 _check_blocked 黑名单+白名单+资源限制+沙箱四重缓解
                    ['/bin/bash', '-c', cmd],
                    capture_output=True,
                    text=True,
                    timeout=effective_timeout,
                    cwd=self.working_dir,
                    preexec_fn=lambda: self._set_resource_limits(command),
                )
            duration = time.monotonic() - start
            # 2026-09-12：白名单网络命令失败自动降级重试。
            # 若 bwrap 沙箱（--unshare-net 或共享 netns 但 DNS 失效）导致网络命令失败，
            # 自动改用「主进程网络域」执行（剥离 bwrap 前缀，直接 subprocess），
            # 消除 agent 手工切换管线的 token/轮次浪费。仅限白名单命令，且
            # 仅在「网络类错误」时触发（DNS 解析失败 / 网络不可达 / 连接被拒）。
            if (
                result.returncode != 0
                and cmd != command  # 确实走了 bwrap 包裹
                and _is_network_allowed(command)
                and _looks_like_network_failure(result.stdout + result.stderr)
            ):
                logging.getLogger(__name__).warning(
                    "白名单网络命令失败(%s)，自动降级到主进程网络域重试: %s",
                    result.returncode,
                    command[:80],
                )
                result = subprocess.run(  # nosec B603 — 白名单 git 命令，_check_blocked 已前置校验
                    ["/bin/bash", "-c", command],
                    capture_output=True,
                    text=True,
                    timeout=effective_timeout,
                    cwd=self.working_dir,
                    preexec_fn=lambda: self._set_resource_limits(command),
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
        except SandboxUnavailableError:
            # 重新抛出 — 配置错误，不应被吞为普通 exit_code=1
            raise
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
        """B3：沙箱包裹 — 通过 SandboxProvider（默认 bwrap，可换 noop/firejail 等）。

        策略（对标 DSH sandbox read-only/workspace-write）：
        - 系统路径只读（/usr /lib /etc /bin /sbin）
        - 工作目录可写（workspace-write）
        - /tmp 可写（工具产物、spill 文件）
        - 网络隔离（--unshare-net）

        后端获取：默认构造 BwrapSandboxProvider（探测可用性）；若外部已注入
        sandbox provider（set_sandbox_provider），优先使用注入的。
        降级条件：bwrap 不存在、或本环境无权限（uid map / net ns 被禁）。
        降级后黑名单+白名单+资源限制三重缓解仍然生效（fail-safe，不静默）。
        """
        provider = getattr(self, "_sandbox_provider", None)
        if provider is None:
            # 优先从 SANDBOX_SEAM 拿默认后端（半 seam → 全 seam）：
            # seam 里是 SignedProvider 包装（治理/审计），bash 执行需底层 wrap/available，
            # 解包 _wrapped 拿 SandboxProviderAdapter（可用时）。seam 不可用回退 BwrapSandboxProvider。
            try:
                from lingclaude.lacp.capability_seam import SANDBOX_SEAM
                seam = SANDBOX_SEAM.get_provider()
                if hasattr(seam, "_wrapped"):
                    seam = seam._wrapped  # 解包 SignedProvider → SandboxProviderAdapter
                if hasattr(seam, "available") and hasattr(seam, "wrap"):
                    provider = seam
            except Exception:  # noqa: BLE001 — seam 不可用回退直接构造
                provider = None
            if provider is None:
                from lingclaude.engine.sandbox_provider import BwrapSandboxProvider
                provider = BwrapSandboxProvider()
            self._sandbox_provider = provider

        bwrap_ok = provider.available()

        # Fail-closed：sandbox_policy 严格模式 + bwrap 不可用 → 抛异常（不再静默降级）
        if not bwrap_ok and self.sandbox_policy is not None:
            from lingclaude.lacp.sandbox_policy import SandboxMode
            if self.sandbox_policy.mode in (
                SandboxMode.RESTRICTED,
                SandboxMode.STRICT,
                SandboxMode.PARANOID,
            ):
                raise SandboxUnavailableError(
                    f"sandbox_policy={self.sandbox_policy.mode.value} requires bwrap, "
                    f"but bwrap is unavailable in this environment (fail-closed)"
                )

        if not bwrap_ok:
            # T0-10: 降级不再静默 — 无策略约束时 WARNING（策略约束路径上方已 fail-closed）
            logging.getLogger(__name__).warning(
                "bwrap 不可用，bash 命令以非沙箱方式执行（黑名单+资源限制仍生效）: %s",
                command[:80],
            )
            return command
        allow_network = _is_network_allowed(command)
        return provider.wrap(
            command,
            working_dir=self.working_dir,
            allow_network=allow_network,
        )

    def set_sandbox_provider(self, provider: Any) -> None:
        """注入沙箱后端插片（SandboxProvider Protocol：name/available/wrap）。

        默认 bwrap；可注入 NoopSandboxProvider（降级）/ 自定义 firejail 等。
        """
        self._sandbox_provider = provider

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

    @classmethod
    def _rule_matches(cls, text: str, needle: str) -> bool:
        r"""黑名单规则匹配（harness fix 2026-09-06：防子串误伤）。

        此前裸词规则（apt / ssh / sudo 等）走 `needle in haystack` 子串匹配，
        误伤合法参数含同名子串的命令（实测：`pytest --capture` 因含
        `apt` 被拦 → cat VERSION 同样连坐 "at "）。规则规避（Goodhart 反向）：
        安全规则越严，正常用例越绕，token 消耗越高。

        现统一语义（2026-09-06 二次修订，合并两版优点）：
        1. 词边界精确匹配（修误伤）：apt 不再命中 --capture、su 不再命中 resume、
           "at " 不再命中 cat/stat——此前裸子串匹配连坐合法参数
        2. 保留 '?' 单字符变体检测（EXP-S2 混淆防御）：文本里 "s?do" 仍命中
           "sudo" 规则；rm -rf / 等非词尾规则不再被错误的 \\b 追加破坏
        """
        import re

        # 词边界精确匹配（防误伤）：apt 不再命中 --capture、su 不再命中
        # resume/stat、at 不再命中 cat。
        # （EXP-S2 的文本 '?' 混淆检测移至 _check_blocked 的 token 级处理——
        #   在整段文本上做规则变体正则会让 "s." 匹配一切 s 开头词，本轮实测教训）
        pattern = r"(?<![\w-])" + re.escape(needle)
        if needle and (needle[-1].isalnum() or needle[-1] == "_"):
            pattern += r"\b"
        return re.search(pattern, text) is not None

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
            if self._rule_matches(cmd_lower, bl):
                return f"匹配黑名单规则 '{blocked}'"

        sub_commands = self._split_chain(cmd_stripped)
        for sub in sub_commands:
            sub_norm = self._normalize_command(sub)
            for blocked in self.blocked_commands:
                bl = blocked.lower()
                if self._rule_matches(sub_norm.lower(), bl):
                    return f"匹配黑名单规则 '{blocked}'（命令链中检测到）"

            tokens = sub_norm.split()
            if not tokens:
                continue
            # LEADING 集合只拦「命令名位置」（子命令首 token），不连坐参数：
            # 专为 at 这类常见英文短词设计，grep "at" / stat 不误伤。
            lead_name = Path(tokens[0].split("=")[-1]).name
            if lead_name.lower() in _BLOCKED_LEADING_COMMANDS:
                return f"基础命令 '{lead_name}' 被禁止（命令名位置拦截）"
            for token in tokens:
                base_cmd_name = Path(token.split("=")[-1]).name
                if base_cmd_name in _BLOCKED_BASE_COMMANDS:
                    return f"基础命令 '{base_cmd_name}' 被禁止"
                # EXP-S2 '?'-混淆防御（token 级）：shell 里 "s?do" 会被 glob
                # 展开为真实命令。规则与 token 等长、非 '?' 字符全等 → 拦截。
                # token 级比较不会误伤（"stat" len4 ≠ "su" len2）。
                for blocked in self.blocked_commands:
                    bl = blocked.strip()
                    if not bl or " " in bl or any(c in bl for c in "*?"):
                        continue
                    if len(base_cmd_name) == len(bl) and all(
                        tc == "?" or tc == bc
                        for tc, bc in zip(base_cmd_name.lower(), bl)
                    ):
                        return f"匹配黑名单规则 '{blocked}'（'?' 混淆变体）"

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

    def _set_resource_limits(self, command: str | None = None) -> None:
        # 修复 2026-09-11：git 远程操作（push/fetch/pull/clone/ls-remote/remote）
        # 需 ~500MB 堆分配，512MB 默认限制撞限（实测 malloc failed）。白名单命令
        # 放宽至 1GB（NETWORK_ALLOWED_MEMORY_LIMIT），其余保持 512MB fail-safe。
        # 健壮化 2026-09-11：父进程 hard limit 锁死（如 512MB）时 setrlimit 抛 EINVAL，
        # 此前被静默吞掉导致白名单命令实际仍撞限。现在记录日志便于运维诊断。
        memory_limit = self.memory_limit
        if command and _is_network_allowed(command):
            memory_limit = max(memory_limit, _NETWORK_ALLOWED_MEMORY_LIMIT)
        try:
            resource.setrlimit(
                resource.RLIMIT_AS,
                (memory_limit, memory_limit),
            )
        except (ValueError, OSError) as exc:
            soft, hard = resource.getrlimit(resource.RLIMIT_AS)
            logging.warning(
                "RLIMIT_AS setrlimit 失败: target=%sMB err=%s (白名单=%s); "
                "当前 soft=%s hard=%s",
                memory_limit // 1024 // 1024,
                exc,
                bool(command and _is_network_allowed(command)),
                soft // 1024 // 1024 if soft != resource.RLIM_INFINITY else "inf",
                hard // 1024 // 1024 if hard != resource.RLIM_INFINITY else "inf",
            )
        try:
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (self.cpu_limit, self.cpu_limit),
            )
        except (ValueError, OSError) as exc:
            logging.warning("RLIMIT_CPU setrlimit 失败: err=%s", exc)
