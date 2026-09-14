"""Bash 网络判定（纯函数，灵元 1.0：砍到最薄）。

2026-09-14: 由 lingclaude/engine/bash.py 剥离 —— 网络白名单判定 + 透明前缀
剥离 + git 子命令安全判定 + 网络失败识别 + 命令链拆分，全部是纯函数
（不依赖 BashExecutor 实例状态），独立成模块可单独测试。

bash.py 通过 re-export 保持向后兼容（tests 直接 from bash import _is_network_allowed）。
"""
from __future__ import annotations

import re
from typing import Any


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
    parts = _split_chain(command)
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





# ── 命令链拆分（原 BashExecutor._split_chain，纯函数化）──

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

