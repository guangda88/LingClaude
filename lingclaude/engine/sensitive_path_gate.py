"""SensitivePathGate — 安全路径审批中间件（对标 AtomCode sensitive_path.rs）。

设计原则：
- Safe 读取工具（read_file/grep/glob/list_dir）访问凭据路径 → 必须审批
- 否则模型可 silent read 凭据并直传 LLM provider（secret exfiltration）
- 与 Risky 工具不重复：Risky 工具已走审批，此 gate 只补 Safe 工具的漏洞

敏感路径列表（对齐 AtomCode，用路径形状命名避免误触普通文本）：
- /.ssh、id_rsa、id_ed25519 等 SSH 密钥
- /.aws、/.kube、/.gnupg 等云/容器凭据
- .env、.netrc、.git-credentials 等配置文件
- /.atomcode/auth.toml 等 AtomCode 凭据
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable


# 敏感路径标记（子串匹配，路径形状命名）
SENSITIVE_MARKERS: tuple[str, ...] = (
    # SSH 密钥
    "/.ssh",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "id_dsa",
    # 云/容器凭据
    "/.aws",
    "/.kube",
    "/.gnupg",
    "/.config/gcloud",
    ".netrc",
    ".git-credentials",
    # AtomCode 凭据
    "/.atomcode/auth.toml",
    "/.atomcode/auth/",
    # Docker/npm/pypi
    "/.docker/config",
    ".npmrc",
    ".pypirc",
    # 证书
    ".pem",
    ".p12",
    ".pfx",
    # 其他敏感文件
    ".env",  # 特殊处理：仅匹配路径中的 .env（非文件名 .env）
    "vendor/.env",
    ".aws/credentials",
    ".kube/config",
)

# 特殊规则：.env 仅当在路径中（非文件名）
_ENV_PATH_PATTERN = re.compile(r"(^|/)\.env(/|$)")


def is_sensitive_path(path_str: str) -> bool:
    """检查路径是否引用敏感路径（子串匹配）。

    :param path_str: 路径字符串（来自工具参数）
    :returns: True 如果路径包含敏感标记
    """
    path_lower = path_str.lower()
    # 修复 2026-09-11：无前导 / 的相对路径（.ssh/config、.aws/credentials）此前
    # 因标记含前导 "/" 漏判。统一补 "/" 前缀后匹配，同时保留原样匹配。
    has_leading_slash = len(path_lower) > 0 and path_lower[0] == "/"
    candidates = (path_lower, f"/{path_lower.lstrip('/')}") if not has_leading_slash else (path_lower,)
    for cand in candidates:
        for marker in SENSITIVE_MARKERS:
            if marker == ".env":
                if _ENV_PATH_PATTERN.search(cand):
                    return True
            elif marker in cand:
                return True
    return False


# 访问模式（2026-09-11 分级，四位监督审计 P0-3）：
# - metadata：仅判断存在性/属性（test -f、ls 等）→ 默认放行，不读内容
# - read：读取内容（cat/less/tail 等）→ 需审批
_METADATA_ACCESS_CMDS = frozenset({
    "test", "[", "[[", "ls", "stat", "find", "realpath", "readlink",
    "dirname", "basename", "file", "du", "df",
})
# 明确读取内容的命令（其余未知命令按 fail-closed 保守拦截）
_READ_ACCESS_CMDS = frozenset({
    "cat", "less", "more", "tail", "head", "grep", "sed", "awk",
    "vi", "vim", "nano", "cp", "rsync", "tar", "zip", "unzip",
    "base64", "xxd", "od", "strings", "md5sum", "sha256sum",
})


# 复合命令拆分（与 bash.py._split_chain 同源语义）：按 && / || / ; / | / $() / ` 切子命令。
# 修复 2026-09-12（codex 审计 P0-1）：此前只看整条命令第一个 token，
# ``test -f ~/.ssh/config && cat ~/.ssh/config`` 首 token 是 metadata 命令被放行，
# 后半段读敏感内容被绕过。现逐子命令独立判定意图，任一子命令读敏感 → 拦截。
_COMMAND_CHAIN_SPLIT_RE = re.compile(r"[;|&]|\$\(")


def _split_command_chain(command: str) -> list[str]:
    """把复合命令拆成子命令段（保持顺序，trim 空白）。"""
    parts = _COMMAND_CHAIN_SPLIT_RE.split(command)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        # 去掉行尾命令替换残留（`...` / $() 已拆出）
        p = p.rstrip("`")
        if p:
            out.append(p)
    return out


# ── 2026-09-15: bash 只读命令白名单（ask 模式放行判定，fail-closed）──
# 背景：ask 按 scope 判定，bash 是 execute 域 → ps/ls/git status 也被拦进灰区。
# 按子命令首 token 匹配；不在名单一律视为需审批（fail-closed）。
# 敏感路径不在此层判定 — 仍由 check_sensitive_path 单独拦截。
BASH_READONLY_LEADS: frozenset[str] = frozenset({
    "ls", "pwd", "cat", "head", "tail", "grep", "find", "stat", "wc",
    "file", "du", "df", "which", "whereis", "whoami", "id", "date",
    "uname", "hostname", "echo", "printf", "env", "printenv", "type",
    "realpath", "readlink", "dirname", "basename", "diff", "sort",
    "uniq", "awk", "sed", "cut", "tr", "ps", "top", "free", "vmstat",
    "lsof", "ss", "ip", "ping", "curl", "wget", "true", "test",
    "cd",  # 仅切换目录；后续子命令仍逐段过白名单（cd x && rm 依旧拦）
    "xargs", "/dev/null", "rg", "ag", "tree", "journalctl",
    "git",  # git 有 push/clean 等副作用，须二级子命令白名单（见下）
})
# 2026-09-15 P0-5 修复：移除高危副作用命令
# 从只读白名单移出的命令（因可写/可执行任意代码，ask 模式下须走审批）：
#   tee(写文件) systemctl(系统管理) python/python3/node(解释器任意代码)
#   npm/pip/pip3(装包写系统) make/pytest(写产物/执行任意命令)
# 这些命令即使只读形态（python3 -c "print(1)"）也无法静态证明无副作用 → fail-closed 拦截。
# curl/wget 保留但须通过二级参数白名单（仅纯查询形态放行，见 _is_curl_query_only）。
# git 本体有 push/clean 等副作用，须二级子命令白名单
BASH_READONLY_GIT_SUBS: frozenset[str] = frozenset({
    "status", "log", "diff", "show", "branch", "remote", "rev-parse",
    "describe", "ls-files", "ls-remote", "blame", "shortlog", "tag",
})

# 2026-09-18 P0 补充：jq 为无副作用文本处理工具（输出走 stdout，写文件由
# 重定向检测单独拦截），补入只读名单消除误伤。
BASH_READONLY_LEADS = frozenset(BASH_READONLY_LEADS | {"jq"})

# ── 2026-09-15 P0-5: curl/wget 二级参数白名单（仅纯查询形态放行）──
# curl 默认 GET 会输出内容、-o/-O 写文件、-d/-F/-X 发数据/改方法，均非只读；
# 故保留在 BASH_READONLY_LEADS 但必须逐参数判定，fail-closed（未知标志即拦）。
_CURL_NEUTRAL_FLAGS: frozenset[str] = frozenset({
    "-s", "-S", "-q", "-L", "-k",
    "--silent", "--show-error", "--location", "--insecure",
})
_CURL_QUERY_FLAGS: frozenset[str] = frozenset({"-I", "--head"})
_CURL_HARMFUL_FLAGS: frozenset[str] = frozenset({
    # 写文件
    "-o", "--output", "-O", "--remote-name", "--remote-name-all",
    # 发送数据/上传/自定义方法
    "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode",
    "-F", "--form", "-T", "--upload-file", "-X", "--request",
    # 状态写入
    "-c", "--cookie-jar", "--post-data",
})
_WGET_QUERY_FLAGS: frozenset[str] = frozenset({"--spider", "-V", "--version", "-h", "--help"})
_WGET_HARMFUL_FLAGS: frozenset[str] = frozenset({
    # 写文件（wget 小写 -o 是日志文件，同样写盘）
    "-O", "--output-document", "--output-file", "-o",
    # 输入文件/发数据
    "-i", "--input-file", "--post-data", "--post-file",
})


def _curl_merged_flags_ok(flag: str) -> tuple[bool, bool]:
    """解析 curl 合并短标志（-sI = -s -I）。

    :returns: (全部安全, 含查询标志)。未知/有害字符 → (False, _)。
    """
    safe_neutral = frozenset("sSqLk")
    safe_query = frozenset("I")
    harmful = frozenset("oOdFTeXcunp")  # output/data/form/upload/custom/cookie...
    has_query = False
    for ch in flag[1:]:  # 去掉前导 -
        if ch in safe_query:
            has_query = True
        elif ch not in safe_neutral:
            return False, has_query
    return True, has_query


def _is_curl_query_only(tokens: list[str]) -> bool:
    """curl 是否纯查询形态（fail-closed：默认 GET 输出内容、未知标志一律拦截）。"""
    args = tokens[1:]
    if not args:
        return False
    # 版本/帮助：纯本地，无网络/文件副作用
    if args[0] in ("-V", "--version", "-h", "--help"):
        return True

    def _has_query_flag(a: str) -> bool:
        if a in _CURL_QUERY_FLAGS:
            return True
        if a.startswith("-") and not a.startswith("--") and len(a) > 2:
            ok, q = _curl_merged_flags_ok(a)
            return bool(ok and q)
        return False

    has_query = any(_has_query_flag(a) for a in args)
    has_discard = "-o" in args or "--output" in args
    # 无查询标志且不丢弃输出 → 默认 GET 输出内容，拦截
    if not has_query and not has_discard:
        return False

    i = 0
    while i < len(args):
        a = args[i]
        if a in _CURL_NEUTRAL_FLAGS or a in _CURL_QUERY_FLAGS:
            i += 1
            continue
        # 合并短标志（-sI / -sSL）：逐字符校验
        if a.startswith("-") and not a.startswith("--") and len(a) > 2:
            ok, _ = _curl_merged_flags_ok(a)
            if not ok:
                return False
            i += 1
            continue
        if a in ("-o", "--output"):
            nxt = args[i + 1] if i + 1 < len(args) else ""
            if nxt == "/dev/null":  # 丢弃内容 → 状态码探测，无害
                i += 2
                continue
            return False  # 写到真实文件 → 拦截
        if a in _CURL_HARMFUL_FLAGS:
            return False
        if a.startswith("-"):
            return False  # 未知 curl 标志 → fail-closed
        i += 1  # URL 等普通参数
    return True


def _is_wget_query_only(tokens: list[str]) -> bool:
    """wget 是否纯查询形态（fail-closed：只放行 --spider 探测与版本/帮助）。"""
    args = tokens[1:]
    if not args:
        return False
    if args[0] in ("-V", "--version", "-h", "--help"):
        return True
    if "--spider" in args:
        return not any(a in _WGET_HARMFUL_FLAGS for a in args)
    return False


def _has_unsafe_redirect(command: str) -> bool:
    """检测子命令分隔符之外的写向重定向与命令替换（2026-09-18 P0 安全修复）。

    is_readonly_bash_command 原实现只按 ;|& 拆子命令，子命令内出现的
    `>` / `>>` / `>&` / `&>` / `<>` / `>|` 输出重定向完全不可见：
    `cat /etc/passwd > /tmp/evil` 被判只读放行（ask 模式绕过审批写盘）。

    规则（fail-closed）：
      - 引号内的 > 不算（echo "a > b" 是纯文本输出）——沿用主函数相同的
        引号状态机，保持语义一致
      - heredoc 正文（<<EOF ... EOF）内不受影响，但 heredoc 定界符本身
        含 `<<` 不触发本检测（<< 是输入重定向，无写盘副作用）
      - 命令替换 $(...) / 反引号可执行任意代码 → 一律视为不安全
    """
    i = 0
    n = len(command)
    quote: str | None = None
    while i < n:
        ch = command[i]
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        # 命令替换：$() 或反引号 → 可执行任意代码，fail-closed
        if ch == "`":
            return True
        if ch == "$" and i + 1 < n and command[i + 1] == "(":
            return True
        # 进程替换 <(cmd) 会执行内部任意命令 → fail-closed
        # （<file 纯输入重定向无副作用，不在此列）
        if ch == "<" and i + 1 < n and command[i + 1] == "(":
            return True
        # 输出重定向家族：> >> >& &> <> >|
        if ch == ">":
            nxt = command[i + 1] if i + 1 < n else ""
            nxt2 = command[i + 2] if i + 2 < n else ""
            if nxt == "&" and (nxt2.isdigit() or nxt2 == "-"):
                # 2>&1 / >&2 / >&- : fd→fd 重定向，无写盘副作用，放行
                i += 3
                continue
            # 目标为 /dev/null（含 2>/dev/null、> /dev/null）→ 丢弃输出，无副作用
            j = i + 1
            while j < n and command[j] in " \t":
                j += 1
            if command[j:j + 9] == "/dev/null":
                i = j + 9
                continue
            return True
        if ch == "&" and i + 1 < n and command[i + 1] == ">":
            # &>file / &>>file : stdout+stderr 双写盘 → 拦
            return True
        i += 1
    return False


def is_readonly_bash_command(command: str) -> bool:
    """判断 bash 命令是否整体只读（逐子命令判定，任一非只读 → False）。

    拆分忽略引号内的分隔符（python3 -c "a; b" 不被误拆）；git 前置全局
    选项（-C <path> / -c k=v）跳过后再取子命令。
    """
    if not command or not command.strip():
        return False
    # 2026-09-18 P0 安全修复：先做引号感知的写向重定向/命令替换扫描——
    # 原逻辑只拆 ;|& 子命令，`cat x > /tmp/y` 这类「只读动词+重定向」
    # 会被逐子命令白名单误放行（ask 模式绕过审批写盘）。
    if _has_unsafe_redirect(command):
        return False
    # 引号感知拆分：; | & $() 在单双引号内不作为命令边界
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(command):
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in ";|&":
            # 2>&1 / >&2 / &>file：& 紧邻 > 时是重定向，不是命令分隔符
            prev = command[i - 1] if i > 0 else ""
            nxt = command[i + 1] if i + 1 < len(command) else ""
            if ch == "&" and (prev == ">" or nxt == ">"):
                buf.append(ch)
            else:
                parts.append("".join(buf))
                buf = []
                if nxt == ch:  # && ||
                    i += 1
        else:
            buf.append(ch)
        i += 1
    parts.append("".join(buf))

    for part in parts:
        tokens = part.strip().rstrip("`").split()
        while tokens and "=" in tokens[0] and len(tokens) > 1:  # 跳过 FOO=bar 前缀
            tokens = tokens[1:]
        if not tokens:
            continue
        lead = Path(tokens[0].strip("\"'")).name
        if lead == "git":
            sub_i = 1
            # 跳过 git 前置全局选项：-C <path> / -c <k=v>
            while sub_i < len(tokens) and tokens[sub_i] in ("-C", "-c", "--git-dir", "--work-tree"):
                sub_i += 2 if tokens[sub_i] in ("-C", "-c", "--git-dir", "--work-tree") else 1
            if sub_i >= len(tokens) or tokens[sub_i].strip("\"'") not in BASH_READONLY_GIT_SUBS:
                return False
        elif lead in ("curl", "wget"):
            # 二级参数白名单：仅纯查询形态放行（P0-5）
            if not (_is_curl_query_only(tokens) if lead == "curl" else _is_wget_query_only(tokens)):
                return False
        elif lead not in BASH_READONLY_LEADS:
            return False
    return True


def _cmd_intent(cmd: str) -> str:
    """判断子命令意图：metadata / read / unknown（未知按 fail-closed 保守拦截）。

    - metadata：仅判断存在性/属性（test -f、ls 等）→ 放行，不读内容
    - read：明确读取内容的命令（cat/base64/cp 等）→ 拦截
    - unknown：未归类命令 → 保守拦截（安全优先）
    """
    tokens = cmd.split()
    if not tokens:
        return "metadata"  # 空段（如纯管道符）无操作
    lead = tokens[0]
    # 去路径前缀（/usr/bin/cat → cat）、引号、赋值前缀
    lead_name = Path(lead.replace("\"", "").replace("'", "")).name
    if "=" in lead_name and tokens[0] == lead:
        # 形如 FOO=bar cat ... 的 env 赋值，跳到第一个非赋值 token
        for t in tokens[1:]:
            if "=" in t and t is tokens[1]:
                continue
            lead_name = Path(t.replace("\"", "").replace("'", "")).name
            break
    if lead_name in _METADATA_ACCESS_CMDS:
        return "metadata"
    if lead_name in _READ_ACCESS_CMDS:
        return "read"
    return "unknown"


def check_sensitive_path(
    path_str: str,
    command: str | None = None,
) -> tuple[bool, str | None]:
    """检查路径并返回 (is_sensitive, reason)。

    :param path_str: 路径字符串
    :param command: 完整 bash 命令（可选）——用于区分 metadata vs read 访问。
                    无 command 时（read/grep/glob 等 Safe 工具）保持 fail-closed。
    :returns: (True, reason) 如果敏感/需审批；(False, None) 如果安全
    """
    if not is_sensitive_path(path_str):
        return False, None

    # 有命令上下文时分级：逐子命令分析意图（修复 2026-09-12 P0-1 复合命令绕过）
    if command:
        for sub in _split_command_chain(command):
            intent = _cmd_intent(sub)
            # 仅 metadata 子命令（test -f / ls）放行；read / unknown 一律拦截
            if intent != "metadata":
                return True, "访问敏感路径，需要审批"
        # 全部子命令都是 metadata → 不读内容，放行
        return False, None

    return True, "访问敏感路径，需要审批"


class SensitivePathGate:
    """SensitivePathGate 中间件 — 在工具调用前检查路径。

    用法：
        gate = SensitivePathGate()
        if gate.check("read_file", "/home/user/.ssh/id_rsa"):
            # 触发审批
            pass
    """

    def __init__(
        self,
        approval_callback: Callable[[str, str], Any] | None = None,
    ):
        """
        :param approval_callback: 审批回调 (tool_name, path) -> bool（True=允许，False=拒绝）
        """
        self.approval_callback = approval_callback

    def check(self, tool_name: str, path: str) -> bool:
        """检查工具调用是否访问敏感路径。

        :param tool_name: 工具名称（如 read_file、grep、glob）
        :param path: 路径参数
        :returns: True 如果需要审批/拒绝；False 如果安全
        """
        is_sensitive, reason = check_sensitive_path(path)
        if not is_sensitive:
            return False

        # 有回调 → 触发审批
        if self.approval_callback:
            try:
                decision = self.approval_callback(tool_name, path)
                return not decision  # True = 需要拦截
            except Exception:
                # 审批失败 → fail-closed
                return True

        # 无回调 → 默认拒绝（fail-closed）
        return True

    def get_reason(self, path: str) -> str | None:
        """获取敏感路径拒绝原因。"""
        _, reason = check_sensitive_path(path)
        return reason


# 模块级单例（供工具调用）
_default_gate: SensitivePathGate | None = None


def get_gate() -> SensitivePathGate:
    """获取全局 SensitivePathGate 实例。"""
    global _default_gate
    if _default_gate is None:
        _default_gate = SensitivePathGate()
    return _default_gate


def check_path(path_str: str) -> tuple[bool, str | None]:
    """便捷函数：检查路径是否敏感。"""
    return check_sensitive_path(path_str)
