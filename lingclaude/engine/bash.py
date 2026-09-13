from __future__ import annotations

import logging
import os
import re
import resource
import shutil
import signal
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
# 2026-09-12（codex 审计 P0-3）：删除本模块独立探测——旧探测不带 --unshare-net，
# 在 netns 受限环境会「探测通过、执行失败」。统一委托 sandbox_provider._bwrap_probe
# （带 --unshare-net 与真实 wrap 一致，且返回 reason 供诊断）。消除二义性。
_BWARP_PROBE_RESULT: bool | None = None


def _bwrap_probe(bwrap: str) -> bool:
    """探测 bwrap 在本环境是否真正可用（一次性，结果缓存）。

    委托 sandbox_provider._bwrap_probe——与真实 wrap 使用完全一致的 flag 组合
    （--ro-bind / + --unshare-net），避免「探测通过、真实执行失败」的二义性。
    """
    global _BWARP_PROBE_RESULT
    if _BWARP_PROBE_RESULT is not None:
        return _BWARP_PROBE_RESULT
    try:
        from lingclaude.engine.sandbox_provider import _bwrap_probe as _probe
        ok, _reason = _probe(bwrap)
    except Exception:  # noqa: BLE001 — 探测失败视为不可用
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
    # 2026-09-12（codex 审计 P0-3）：降级显式化——bwrap 不可用时置 True，
    # 供上层工具输出/审计可见（不再静默降级，只在日志 WARN）。
    degraded: bool = False

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
    "nc ", "ncat",
    "apt", "apt-get", "yum", "dnf", "pacman", "pip install",
    # 注：curl/wget 从全量黑名单移除（2026-09-13 细颗粒度优化）——
    # 只读网络探测（curl -I / --head / -s -o /dev/null）是安全健康检查的
    # 合法操作，不应误杀。写/下载形态仍由 _is_readonly_network_probe
    # 之外的路径拦截（见 _check_blocked 网络命令处理）。
    # 注：ssh/scp/telnet/mount/systemctl/service 等从全文本规则移除
    # （2026-09-13 细颗粒度优化）——全文本 _rule_matches 会连坐参数
    # （grep -n "ssh" / cat proxy3.service 被误杀），改由
    # _BLOCKED_CMD_NAME_ONLY 仅在命令名位置拦截。
    # 注："at" 不入全文本规则（见下）。
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

# 命令名位置拦截（2026-09-13 细颗粒度优化）：
# 仅当这些命令出现在「命令名位置」（子命令首 token）时拦截，不连坐参数。
# 此前在 token 级遍历 *所有* token（含 grep 的模式参数、cat/cp 的文件参数），
# 导致 `grep -rn "ssh" docs/`、`cat proxy3.service`、`grep systemctl` 被误杀
# （2026-09-13 拦截统计实证：磁盘/挂载探测 17 次、env 探测 10 次多为此类误杀）。
# 命令名位置检查已由 _check_blocked 的 lead_name 逻辑覆盖，此处仅保留
# 全文本规则兜底（首 token 命令名）——不参与 token 参数遍历。
_BLOCKED_BASE_COMMANDS = frozenset({
    "sudo", "su", "mkfs", "ssh", "scp", "telnet",
    "mount", "umount", "fdisk", "parted",
    "iptables", "ufw", "firewall-cmd",
    "systemctl", "service",
    "crontab",
})

# 命令名位置拦截（细颗粒度）：这些命令仅在「子命令首 token」被检查。
# 与 _BLOCKED_BASE_COMMANDS 全文本规则的区别：本集合的检查逻辑在
# _check_blocked 的 token 循环中，只匹配「命令名 token」本身，
# 不遍历参数 token（避免 grep "ssh" / cat proxy3.service 误杀）。
_BLOCKED_CMD_NAME_ONLY = frozenset({
    "ssh", "scp", "telnet",
    "mount", "umount", "fdisk", "parted",
    "iptables", "ufw", "firewall-cmd",
    "systemctl", "service",
    "crontab",
})

# 危险传递命令：命令名位置 + 参数位置都拦截（防 `echo x | sudo tee`、
# `echo $(sudo whoami)` 等混淆绕过）。sudo/su 是提权命令，即使作为
# 参数出现（被 eval/管道消费）也是提权路径，必须 fail-closed。
_BLOCKED_DANGER_ANYWHERE = frozenset({
    "sudo", "su", "mkfs",
})

# 网络白名单（2026-09-11 白名单化）：仅这些「可信 git 远程操作」在沙箱中开放网络
# （bwrap wrap(allow_network=True)，不再注入 --unshare-net）。其余命令仍网络隔离。
# 设计：git 本身不在黑名单（_check_blocked 放行），git 内部调 ssh 是子进程、
# 命令行无 ssh token 不触发 _BLOCKED_BASE_COMMANDS；本白名单只决定「是否去掉网络隔离」。
# P2-1 (灵元): 白名单外置 policies/sandbox_policy.yaml，走 PolicyLoader 热更（mtime watch）。
def _network_allowed_commands() -> tuple[str, ...]:
    """网络白名单：优先策略文件，读失败回退内置默认（graceful degrade）。"""
    from lingclaude.core.policy_loader import get as policy_get

    data = policy_get("sandbox_policy")
    cmds = data.get("network_allowed_commands")
    if isinstance(cmds, list) and cmds:
        return tuple(str(c) for c in cmds)
    return (
        "git push",
        "git fetch",
        "git pull",
        "git clone",
        "git ls-remote",
        "git remote",
    )


_NETWORK_ALLOWED_COMMANDS = _network_allowed_commands()

# 透明前缀：这些包装命令本身不访问网络，剥离后不影响白名单判定。
# 2026-09-12 修复：agent 习惯给 git 远程命令加 `timeout N`/`env` 前缀，
# 导致全链白名单判定 False → 整条被 `--unshare-net` 隔离 → git 无法联网。
# 仅剥离「纯包装」前缀；前缀后的实际命令仍逐个全链判定（保持 fail-closed）。
# git 认证禁用，防 n_tty_read 抢占终端（灵安审计 P0-①）
_GIT_NO_PROMPT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}

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


# 输出修饰段（无网络副作用）：管道消费命令与重定向。
# 判定原则（fail-closed）：只有「明确无网络面」的段才豁免网络白名单；
# 任何有潜在网络面的命令（xargs/tee/ssh/curl/wget/nc/telnet/...）一律不豁免
# → 整条仍被 --unshare-net 隔离。
# 2026-09-12 修复：agent 实际命令几乎都带 `2>&1 | head` 输出裁剪，`_split_chain`
# 把 `2>&1`（按 & 拆）、`| head`（按 | 拆）切成独立段 → 不匹配 git 白名单 →
# 全链 False → git 远程操作被误隔离（白名单形同虚设）。输出修饰本身不发起网络，
# 豁免后仅对「实际网络命令段」做白名单判定，fail-closed 语义不损失。
_OUTPUT_MODIFIER_PREFIXES = frozenset({
    # 管道消费：纯 stdin→stdout 处理，无网络能力
    "head", "tail", "grep", "egrep", "fgrep", "cat", "sed", "awk", "wc",
    "sort", "uniq", "cut", "tr", "column", "fold", "nl", "od", "xxd",
    "hexdump", "diff", "cmp", "comm", "paste", "fmt",
    "md5sum", "sha1sum", "sha256sum", "cksum", "base64",
    # shell 内置 / 状态卫兵：无网络面（`git fetch ... || true` 场景）。
    # 注意：刻意不含 echo —— echo 常作命令开头，豁免会导致「非网络命令也放行
    # 网络」的语义污染（echo hello → allow_network=True）。true/false/: 仅作
    # `|| true` 状态卫兵，且不会出现在命令首部，豁免安全。
    "true", "false", ":",
})
# 重定向段：2>&1 / >/dev/null / >>log / <file / 2>file（本地读写，无网络面）。
# _split_chain 按 & 拆分会把 `2>&1` 切成 `2` + `>1` 两段：`2` 是纯数字段、
# `>1` 是纯重定向段，各自匹配；`_is_output_modifier("2>&1")` 直接调用时
# 也需匹配完整形态（含中间 &）。
_REDIRECT_SEG_RE = re.compile(r"^(?:[0-9]*[<>]+[^\s|;]*|[0-9]+)$")


def _is_output_modifier(sub: str) -> bool:
    """判断子命令段是否为纯输出修饰（重定向 / 纯消费管道），无网络副作用。"""
    s = sub.strip()
    if not s:
        return True  # 空段（cd 剥离后）跳过，不参与判定
    if _REDIRECT_SEG_RE.match(s):
        return True
    head = s.split()[0].lower()
    return head in _OUTPUT_MODIFIER_PREFIXES


# 危险 git 参数（可执行任意代码/指定远程程序，网络白名单命中时仍需拒绝）
_GIT_DANGEROUS_PARAMS = (
    "--upload-pack",
    "--receive-pack",
    "--exec",
    "--config-env",
    "-c ",  # git -c core.sshCommand='...' 可执行任意命令（含空格版本）
    "-c=",
    "--config=",
    "--git-dir=",
    "--work-tree=",
    "--namespace=",
)
# 远程白名单（git push/fetch/pull/clone/ls-remote 的 remote 目标）
# 空 = 不限制（保持向后兼容）；可配置时仅放行已登记的 remote。
_ALLOWED_GIT_REMOTES: frozenset[str] = frozenset({"origin", "github", "upstream", "gh"})
# 明确允许的 git 子命令（其余 git 子命令不放行网络）
_GIT_NETWORK_SUBCOMMANDS = frozenset({"push", "fetch", "pull", "clone", "ls-remote", "remote"})
# P0-②（灵安审计）：git 本地只读子命令（status/log/diff/show 等）不访问网络，
# 但 _is_network_allowed 判 False 会被 --unshare-net 隔离（无害但语义错误）。
# 这些只读子命令放行网络是安全的——它们本来就不联网。
_GIT_READONLY_SUBCOMMANDS = frozenset({
    "status", "log", "diff", "show", "branch", "tag", "stash",
    "rev-parse", "rev-list", "blame", "shortlog", "describe",
    "ls-files", "ls-tree", "cat-file", "config", "remote",
})


def _git_network_safe(sub: str) -> bool:
    """校验单个 git 网络子命令是否参数安全（P0-4 增强，2026-09-12）。

    字符串前缀白名单无法挡住 ``git push --upload-pack='evil'`` 参数注入：
    --upload-pack / --receive-pack / -c / --config-env 可让 git 远程端执行任意程序。
    此处对 git 命中段做参数级校验——含危险参数或命令替换 → 不放行网络。
    """
    s = sub.lower()
    # 命令替换（$() / `...`）在 _split_chain 已拆出独立段判定，此处再兜底
    if "$(" in s or "`" in s:
        return False
    # 去透明前缀后取 git 子命令
    toks = _strip_transparent_prefix(sub).split()
    if not toks or toks[0].replace("/", "").rstrip(".") not in ("git", "git.exe"):
        return False
    # git remote（无子命令）→ 只读列出，安全
    if len(toks) == 1:
        return True
    subcmd = toks[1]
    # P0-②（灵安审计）：git 本地只读子命令不访问网络，直接放行（无害）
    if subcmd in _GIT_READONLY_SUBCOMMANDS:
        return True
    if subcmd not in _GIT_NETWORK_SUBCOMMANDS:
        return False
    # 参数级校验：危险参数一律拒绝
    for i, t in enumerate(toks):
        if t in ("-c", "--upload-pack", "--receive-pack", "--exec", "--config-env"):
            return False
        if any(t.startswith(p) for p in _GIT_DANGEROUS_PARAMS):
            return False
    # remote 白名单（clone/ls-remote/push/fetch/pull 的目标）
    if subcmd in ("push", "fetch", "pull", "clone", "ls-remote"):
        for t in toks[2:]:
            # 选项参数与重定向（2>&1 / >log / 1>>log）跳过
            if t.startswith("-") or t.startswith(">") or (
                len(t) >= 2 and t[0].isdigit() and t[1] in ">"
            ):
                continue
            # 第一个非选项参数是 remote/url — 仅放行白名单内或 URL 形
            if subcmd == "clone":
                return True  # clone 的 URL 由用户显式指定，白名单难覆盖，保持放行（网络面=git 自身）
            if _ALLOWED_GIT_REMOTES and t not in _ALLOWED_GIT_REMOTES and "://" not in t and "@" not in t:
                return False
            break
    return True


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

    修复 2026-09-12b（输出修饰段误伤）：``git push 2>&1 | head`` 的 ``2>&1``/``head``
    段不匹配 git 白名单 → 整条隔离（白名单形同虚设）。现先过滤纯输出修饰段
    （重定向 + 纯消费管道），仅对实际网络命令段判定；含网络面命令（curl/wget/
    xargs/tee/nc 等）不豁免 → fail-closed 保持。

    命中返回 True -> _sandbox_command 传 allow_network=True。
    """
    parts = BashExecutor._split_chain(command)
    if not parts:
        return False
    first_real_seen = False
    for sub in parts:
        # 输出修饰段（2>&1 / >/dev/null / | head / | grep ...）无网络面 → 豁免。
        # 但「命令首部」的修饰命令（`head -3 && git push`）不豁免——它是独立
        # 命令而非输出消费，豁免会让非网络命令放行网络（fail-closed 保持）。
        if first_real_seen and _is_output_modifier(sub):
            continue
        norm = _strip_transparent_prefix(sub).lower().replace("'", "").replace('"', "")
        if not norm:
            continue  # cd /path 等纯导航段剥离后为空，跳过（不参与白名单判定）
        first_real_seen = True
        # P0-②（灵安审计）：git 整体放行网络（参数注入仍由 _git_network_safe 274 行兜底）
        if norm == "git" or norm.startswith("git "):
            hit = True
        else:
            # P2-1: 运行时读策略（热更生效），模块常量仅作回退
            allowed_list = _network_allowed_commands()
            hit = any(
                norm == allowed or norm.startswith(allowed + " ")
                for allowed in allowed_list
            )
        if not hit:
            return False
        # P0-4 增强 (2026-09-12): git 命中段再做参数级校验 —
        # 防 `git push --upload-pack='evil'` / `git -c core.sshCommand=... push`
        # 参数注入（字符串前缀白名单挡不住）。
        if norm.split()[0].replace("/", "").rstrip(".") in ("git", "git.exe"):
            if not _git_network_safe(sub):
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


_DEFAULT_MEMORY_LIMIT = 1024 * 1024 * 1024  # 1 GB（git 实测需 ~500MB，留余量）
_DEFAULT_CPU_LIMIT = 120  # seconds（30s 误杀编译/大grep）
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
# 设计意图（灵安审计 P2-② 确认）：deepseek/bigmodel/dashscope 均已有专用
# SDK 路由（task_router.py），bash 层拦截防止 agent 绕过 SDK 直接 curl 调用
# （可能泄漏凭证或绕过鉴权）。atomgit.com 是安全参考来源，保留拦截。
# 若未来某 host 需要直连，应从本集合移除并补充注释说明理由。
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
        timeout: int = 300,   # P1（灵安审计）：git push 大传输可超 60s
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
        # 2026-09-12（codex 审计 P0-3）：降级显式化标记（每命令执行前复位）
        self._last_degraded = False

    def run(self, command: str, timeout: int | None = None) -> BashResult:
        effective_timeout = timeout or self.timeout
        self._last_degraded = False  # 2026-09-12：每次执行前复位降级标记

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
                    start_new_session=True,  # 2026-09-12 (P1-1): 新会话组 — 超时 killpg 整组
                    stdin=subprocess.DEVNULL,  # P0-① 隔离 stdin，子进程不再抢占终端
                    env={**os.environ, **_GIT_NO_PROMPT_ENV},  # 认证失败立即返回，不挂起读终端
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
                    start_new_session=True,  # 2026-09-12 (P1-1): 新会话组 — 超时 killpg 整组
                    stdin=subprocess.DEVNULL,  # P0-① 隔离 stdin，子进程不再抢占终端
                    env={**os.environ, **_GIT_NO_PROMPT_ENV},  # 认证失败立即返回，不挂起读终端
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
                    start_new_session=True,  # 2026-09-12 (P1-1): 新会话组 — 超时 killpg 整组
                    stdin=subprocess.DEVNULL,  # P0-① 隔离 stdin，子进程不再抢占终端
                    env={**os.environ, **_GIT_NO_PROMPT_ENV},  # 认证失败立即返回，不挂起读终端
                    preexec_fn=lambda: self._set_resource_limits(command),
                )
                duration = time.monotonic() - start
            return BashResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration=duration,
                command=command,
                degraded=self._last_degraded,
            )
        except subprocess.TimeoutExpired as te:
            duration = time.monotonic() - start
            # 2026-09-12 (P1-1): 超时真正取消 — start_new_session 建了新进程组，
            # 这里 killpg 整组（含 bash 子进程），避免 daemon 线程/子进程残留。
            try:
                pgid = te.process.pid if te.process is not None else None
                if pgid:
                    os.killpg(pgid, signal.SIGKILL)
            except Exception:  # noqa: BLE001 — killpg 失败仅记录（进程可能已退出）
                pass
            return BashResult(
                exit_code=124,
                stdout="",
                stderr=f"命令在 {effective_timeout}s 后超时（已终止进程组）",
                duration=duration,
                command=command,
                degraded=self._last_degraded,
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
            # 2026-09-12（codex 审计 P0-3）：降级显式化 — 标记本次执行降级，
            # run() 读取后写入 BashResult.degraded，工具输出/审计可见。
            self._last_degraded = True
            return command
        allow_network = _is_network_allowed(command)
        # 额外可写目录白名单：策略层 allowed_paths 允许的协作路径在此放开写。
        # 对齐 sandbox_policy.DEFAULT_POLICY.allowed_paths=["/home/ai","/tmp"]——
        # 策略层已声明整个 /home/ai 可信，执行层不应与策略脱节。
        # 可通过环境变量显式覆盖（逗号分隔）；默认注入 /home/ai（用户工作区根）。
        extra_dirs: list[str] = []
        env_extra = os.environ.get("LINGCLAUDE_EXTRA_WRITABLE_DIRS", "")
        if env_extra:
            # 显式设置：全量采用用户指定目录（不隐式加 /home/ai，尊重覆盖意图）
            extra_dirs = [d.strip() for d in env_extra.split(",") if d.strip()]
        else:
            # 未显式设置：默认对齐策略层 allowed_paths（P2-1: 读 sandbox_policy.yaml
            # 的 default_writable_dirs，热更生效；读失败回退 /home/ai）
            from lingclaude.core.policy_loader import get as policy_get

            sdata = policy_get("sandbox_policy")
            defaults = sdata.get("default_writable_dirs")
            if isinstance(defaults, list) and defaults:
                extra_dirs = [str(d) for d in defaults]
            else:
                extra_dirs = ["/home/ai"]
        # 兼容旧 wrap 签名（无 extra_writable_dirs 参数的 provider，如测试 Fake）：
        # 尝试传 extra_writable_dirs，TypeError 则回退旧参数（能力降级不报错）。
        try:
            return provider.wrap(
                command,
                working_dir=self.working_dir,
                allow_network=allow_network,
                extra_writable_dirs=extra_dirs or None,
            )
        except TypeError:
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
        """将命令链（&&, ||, ;, |, $(), ``）拆分为子命令逐个检测。

        2026-09-13 修复（细颗粒度）：引号感知扫描——此前用裸正则
        ``re.split(r\"[;|&]|\\$\\(|`\", command)`` 拆分，不感知单/双引号，
        导致 ``grep -rn "ssh|SSH" docs/`` 被误拆成 ``grep -rn "ssh`` +
        ``SSH" docs/``，黑名单词出现在「子命令首 token」→ 误杀
        （拦截统计实证：搜索含 | 的模式参数是误杀重灾区）。
        现在逐字符扫描，引号内（'..' / ".." / $'..'）的分隔符不拆分。
        """
        parts: list[str] = []
        buf: list[str] = []
        quote: str | None = None
        i = 0
        n = len(command)
        while i < n:
            ch = command[i]
            if quote:
                buf.append(ch)
                if ch == quote:
                    quote = None
                # 处理转义：\x 在引号内跳过下一字符
                if ch == "\\" and i + 1 < n:
                    buf.append(command[i + 1])
                    i += 1
                i += 1
                continue
            if ch in ("'", '"'):
                quote = ch
                buf.append(ch)
                i += 1
                continue
            # 命令替换 $() / ``：作为整体一段（内含分隔符不拆分，
            # 子命令检测时再递归处理——当前保守策略：整体保留，
            # 让后续 _rule_matches 全文本规则兜底危险词）
            if ch == "$" and i + 1 < n and command[i + 1] == "(":
                depth = 1
                buf.append("$(")
                i += 2
                while i < n and depth > 0:
                    if command[i] == "(":
                        depth += 1
                    elif command[i] == ")":
                        depth -= 1
                    buf.append(command[i])
                    i += 1
                continue
            if ch == "`":
                # 反引号命令替换：扫到下一个未转义 ` 为止
                buf.append(ch)
                i += 1
                while i < n and command[i] != "`":
                    buf.append(command[i])
                    i += 1
                if i < n:
                    buf.append(command[i])  # 收尾 `
                    i += 1
                continue
            if ch in (";", "|", "&"):
                # && || ; | 都是分隔符；& 单独出现也分隔
                seg = "".join(buf).strip()
                if seg:
                    parts.append(seg)
                buf = []
                # 跳过连续的 & | 或单个
                i += 1
                if i < n and command[i] in ("&", "|"):
                    i += 1
                continue
            buf.append(ch)
            i += 1
        seg = "".join(buf).strip()
        if seg:
            parts.append(seg)
        return parts

    def _is_credential_search(self, command: str) -> bool:
        """判断命令是否为「只读凭据搜索」（B3：安全工具自身合法操作）。

        豁免条件（同时满足才豁免）：
        1. 命令首 token 是搜索工具（grep/egrep/fgrep/rg/ack/ripgrep）
           或管道到搜索工具（如 `cat file | grep pattern`）
        2. 存在 `-rn`/`-r`/`-R` 等递归参数（说明是搜文件，不是执行）
           或管道形态（cat/head/tail 等只读命令接 grep）
        3. 命中凭据形态出现在「模式/参数」位置（非管道首、非重定向到外部）

        不满足豁免 → 照常拦截（写入/传递类 curl/export/echo 仍 fail-closed）。
        """
        s = command.strip()
        if not s:
            return False
        tokens = s.split()
        head = Path(tokens[0]).name.lower()
        # 形态1：grep/egrep/fgrep/rg/ack/ripgrep 直接开头
        if head in ("grep", "egrep", "fgrep", "rg", "ack", "ripgrep"):
            pass  # 继续检查递归参数
        # 形态2：cat/head/tail/less 等只读命令管道到 grep（如 `cat config | grep api.deepseek.com`）
        elif head in ("cat", "head", "tail", "less", "more"):
            # 检查管道后是否是 grep
            if not re.search(r"\|\s*(grep|egrep|fgrep|rg|ack|ripgrep)\b", s):
                return False
        else:
            return False
        # 必须含递归参数，才视为「文件搜索」而非其它用途
        joined = " ".join(tokens)
        if not any(flag in tokens for flag in ("-r", "-R", "-rn", "-rR", "-Rr")):
            # 也接受合并形态如 -rn 已被拆词，这里直接查子串
            if not re.search(r"-\w*[rR]\w*", joined):
                # 管道形态（cat file | grep pattern）不需要递归参数
                if not re.search(r"\|\s*(grep|egrep|fgrep|rg|ack|ripgrep)\b", s):
                    return False
        # 防止 `grep sk- | curl ...` 这类搜索后外泄：若命令含管道到网络/写入，
        # 不豁免（保持 fail-closed）。仅当整条命令无写/网络副作用时放行。
        if re.search(r"\|\s*(curl|wget|nc|ncat|ssh|scp|telnet|tee|>)", s):
            return False
        return True

    @staticmethod
    def _is_readonly_network_probe(command: str) -> bool:
        """判断命令是否为「只读网络探测」（2026-09-13 细颗粒度）。

        豁免条件（任一满足即视为只读探测）：
        - curl/wget 带 -I/--head（仅取响应头）
        - curl 带 -s -o /dev/null（静默丢弃响应体，只测连通/状态码）
        - wget --spider（爬虫模式，不下载内容）
        - curl -sS -o /dev/null -w（写 /dev/null + 输出格式化，探测专用）

        注意：即使命令首 token 是 curl/wget，若出现下载/执行形态
        （-o 非 /dev/null、-O、| sh、| bash、-d 提交数据），返回 False → 拦截。
        """
        s = command.strip()
        if not s:
            return False
        tokens = s.split()
        head = Path(tokens[0].split("=")[-1]).name.lower()
        if head not in ("curl", "wget", "timeout", "env", "nice"):
            # 处理 timeout 5 curl ... 包装形态
            for t in tokens:
                if Path(t.split("=")[-1]).name.lower() in ("curl", "wget"):
                    head = Path(t.split("=")[-1]).name.lower()
                    break
            else:
                return False
        joined = " ".join(tokens)
        # 下载/执行形态 → 非只读（fail-closed）
        if re.search(r"-o\s+(?![\"\']?/dev/null[\"\']?)\S+", joined):
            return False
        if re.search(r"(?<!\w)-O\b", joined):
            return False
        if re.search(r"\|\s*(sh|bash|zsh)\b", joined):
            return False
        if re.search(r"(?<!\w)-d\b", joined):
            return False
        # 只读形态：-I / -sI / --head / --spider / -s -o /dev/null
        # 注意 curl 短选项可合并（-sI、-sS -o /dev/null 等），需匹配
        # 「选项串中含 I」或「独立 --head/--spider」
        if re.search(r"(?<!\w)--head\b|--spider\b", joined):
            return True
        if re.search(r"(?<![A-Za-z0-9])-[A-Za-z]*I\b", joined):
            return True
        if re.search(r"-o\s+[\"\']?/dev/null[\"\']?", joined):
            return True
        # P0-④（灵安审计）：curl 直接输出到 stdout（无 -o 落盘、无 |sh 执行）属只读抓取
        if head == "curl" and not re.search(r"-o\s+\S+", joined) and not re.search(r"\|\s*(sh|bash|zsh)\b", joined):
            return True
        return False

    @staticmethod
    def _detect_network_command(command: str) -> str | None:
        """检测命令中是否含网络命令（curl/wget/nc/ncat），返回命令名或 None。

        与 _is_readonly_network_probe 配合：只读探测已豁免，走到这里说明
        是下载/写/执行形态 → 返回命令名供拦截消息使用。
        """
        s = command.strip()
        if not s:
            return None
        tokens = s.split()
        for t in tokens:
            name = Path(t.split("=")[-1]).name.lower()
            if name in ("curl", "wget", "nc", "ncat"):
                return name
        return None

    def _check_blocked(self, command: str) -> str | None:
        cmd_stripped = command.strip()
        cmd_normalized = self._normalize_command(cmd_stripped)
        cmd_lower = cmd_normalized.lower()

        # P0-1: 凭据模式检测（fail-closed）
        # B3 (2026-09-13): 只读搜索豁免 —— grep/egrep/fgrep/rg/ack 等搜索命令中
        # 的凭据形态是「查找泄漏」的安全工具自身合法操作，不应误杀
        # （此前连 `grep -rn "sk-"` 都被拦，安全工具无法工作=能力绞杀）。
        # 但写入/传递类（curl/export/echo 到文件等）仍全量拦截（fail-closed）。
        if not self._is_credential_search(cmd_stripped):
            for marker in _CREDENTIAL_MARKERS:
                if marker.lower() in cmd_lower:
                    return f"命令包含凭据模式 '{marker}'，禁止通过 bash 传递凭据"

        # P0-1: 禁止直接调用外部 API host
        # P2-②（灵安审计）：只读搜索（grep/cat 等）豁免——安全工具查找泄漏是合法操作
        if not self._is_credential_search(cmd_stripped):
            for host in _FORBIDDEN_API_HOSTS:
                if host in cmd_lower:
                    return f"禁止直接调用 {host}，请使用专用工具或 SDK"

        # 网络命令细颗粒度（2026-09-13）：
        # curl/wget 从 _ALWAYS_BLOCKED 移除后，这里按「读写形态」分流：
        # - 只读探测（curl -I / --head / -s -o /dev/null / wget --spider）→ 放行
        #   （健康检查/连通性探测是合法安全操作，误杀=能力绞杀）
        # - 下载/写文件/执行（curl -o file / curl | sh / wget 无 --spider）→ 拦截
        if not self._is_readonly_network_probe(cmd_stripped):
            net_cmd = self._detect_network_command(cmd_stripped)
            if net_cmd:
                return f"网络命令 '{net_cmd}' 被禁止（非只读探测形态；只读探测请用 -I/--head/-s -o /dev/null/--spider）"

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
            # 命令名位置拦截（2026-09-13 细颗粒度）：子命令首 token 检查
            # _BLOCKED_BASE_COMMANDS 与 _BLOCKED_CMD_NAME_ONLY。
            # 注意：首 token 可能是 `sudo cmd` 中的 cmd（sudo 由 DANGER_ANYWHERE
            # 在参数位置兜底），也可能是 env/timeout 包装后的真实命令——统一
            # 取第一个「非透明包装」token 检查（见 _normalize_command 剥离前缀）。
            if lead_name.lower() in _BLOCKED_BASE_COMMANDS or lead_name.lower() in _BLOCKED_CMD_NAME_ONLY:
                # P2-①（灵安审计）：只读诊断形态豁免 — systemctl status/is-active 等 + mount 无参
                if lead_name.lower() == "systemctl" and len(tokens) > 1 and tokens[1] in (
                    "status", "is-active", "is-enabled", "is-failed", "list-units", "show",
                ):
                    pass  # 只读，放行
                elif lead_name.lower() == "mount" and len(tokens) == 1:
                    pass  # 无参数=仅查看挂载，放行
                else:
                    return f"基础命令 '{lead_name}' 被禁止"
            # EXP-S2 '?'-混淆防御（命令名位置）：shell 里 "s?do" 会被 glob
            # 展开为真实命令。对命令名 token 与「命令名拦截集合」全部比较，
            # 规则与 token 等长、非 '?' 字符全等 → 拦截。
            # token 级比较不会误伤（"stat" len4 ≠ "su" len2）。
            # P2-①：只读诊断形态豁免同样适用于 '?' 混淆检查
            _is_readonly_diag = (
                lead_name.lower() == "systemctl" and len(tokens) > 1 and tokens[1] in (
                    "status", "is-active", "is-enabled", "is-failed", "list-units", "show",
                )
            ) or (lead_name.lower() == "mount" and len(tokens) == 1)
            _name_blocks = _BLOCKED_BASE_COMMANDS | _BLOCKED_CMD_NAME_ONLY | _BLOCKED_DANGER_ANYWHERE
            for bl in _name_blocks:
                if not bl or " " in bl or any(c in bl for c in "*?"):
                    continue
                if len(lead_name.lower()) == len(bl) and all(
                    tc == "?" or tc == bc
                    for tc, bc in zip(lead_name.lower(), bl)
                ):
                    if not _is_readonly_diag:
                        return f"匹配黑名单规则 '{bl}'（'?' 混淆变体）"
            # DANGER_ANYWHERE：参数位置也拦 sudo/su/mkfs（防管道/命令替换混淆绕过）
            for token in tokens[1:]:
                param_name = Path(token.split("=")[-1]).name
                if param_name.lower() in _BLOCKED_DANGER_ANYWHERE:
                    return f"危险命令 '{param_name}' 出现在参数位置（防提权绕过）"

        base_cmd = cmd_stripped.split()[0] if cmd_stripped.split() else ""
        base_cmd_name = Path(base_cmd).name
        # P2-①（灵安审计）：只读诊断形态豁免同样适用于循环外的基础命令检查
        _is_readonly_diag_base = (
            base_cmd_name.lower() == "systemctl" and len(cmd_stripped.split()) > 1
            and cmd_stripped.split()[1] in ("status", "is-active", "is-enabled", "is-failed", "list-units", "show")
        ) or (base_cmd_name.lower() == "mount" and len(cmd_stripped.split()) == 1)
        if base_cmd_name.lower() in _BLOCKED_BASE_COMMANDS and not _is_readonly_diag_base:
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
