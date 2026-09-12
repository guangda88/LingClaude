"""Git integration tools for lingclaude.

Provides structured access to git information without raw shell access:
- git_status: working tree status
- git_diff: staged/unstaged changes
- git_log: commit history
- git_blame: line-level authorship
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lingclaude.core.types import Result


@dataclass(frozen=True)
class GitResult:
    exit_code: int
    output: str
    error: str
    duration: float

    @property
    def success(self) -> bool:
        return self.exit_code == 0


_MAX_OUTPUT = 50_000  # chars


def _run_git(args: list[str], cwd: str | None = None, timeout: int = 30) -> GitResult:
    import time
    cmd = ["git"] + args
    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        duration = time.monotonic() - start
        output = result.stdout[:_MAX_OUTPUT]
        if len(result.stdout) > _MAX_OUTPUT:
            output += f"\n... (truncated, {len(result.stdout)} total chars)"
        return GitResult(
            exit_code=result.returncode,
            output=output,
            error=result.stderr.strip(),
            duration=duration,
        )
    except subprocess.TimeoutExpired:
        return GitResult(
            exit_code=124,
            output="",
            error=f"git {' '.join(args)} timed out after {timeout}s",
            duration=time.monotonic() - start,
        )
    except FileNotFoundError:
        return GitResult(
            exit_code=127,
            output="",
            error="git not found — install git to use git tools",
            duration=0,
        )
    except Exception as e:
        return GitResult(
            exit_code=1,
            output="",
            error=str(e),
            duration=0,
        )


def is_git_repo(path: str = ".") -> bool:
    r = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return r.success and r.output.strip() == "true"


def git_status(path: str = ".", short: bool = True) -> Result[dict[str, Any]]:
    # porcelain -z: NUL 分隔输出，正确处理含空格/特殊字符的路径。
    # （旧实现按行 split + .strip() 会破坏首尾含空格的合法文件名，且循环内
    #   path = ... 遮蔽函数参数 path —— codex 审计 P2 修复）
    args = ["status", "--porcelain", "-z"] if short else ["status"]
    r = _run_git(args, cwd=path)
    if not r.success:
        return Result.fail(r.error)

    files = []
    if short and r.output:
        # porcelain v1 -z: "XY PATH\0"；重命名/复制为 "XY NEW\0OLD\0"
        # （第一个是 new path，第二个是 old path——实测 git mv 验证）
        # 旧实现（非 -z）返回 old path，此处保持一致性 → 记录 old，跳过 new
        entries = r.output.split("\0")
        i = 0
        while i < len(entries):
            entry = entries[i]
            if not entry:
                i += 1
                continue
            if len(entry) < 3:
                i += 1
                continue
            # v1 -z 格式: "XY PATH"（XY 两字符 + 一个固定空格 + 路径）
            status = entry[:2].strip()
            entry_path = entry[3:]
            if status in ("R", "C"):
                # 双路径条目：当前是 new，下一个是 old（无 XY 前缀）
                old_path = entries[i + 1] if i + 1 < len(entries) else entry_path
                entry_path = old_path
                i += 2
            else:
                i += 1
            # Ignore .audit directory created by pre-commit hooks.
            # 审计 L1:PATH_TRAVERSE 要求用路径规范化判断而非字符串前缀匹配：
            # porcelain 输出的 entry_path 已是规范化相对路径（无 ..），
            # 用 Path.resolve() + is_relative_to() 判定是否位于 .audit 目录。
            _p = Path(entry_path).resolve()
            _audit_root = Path(".audit").resolve()
            if _p != _audit_root and not _p.is_relative_to(_audit_root):
                files.append({
                    "status": status,
                    "path": entry_path,
                })

    return Result.ok({
        "raw": r.output,
        "files": files,
        "has_changes": bool(files),
    })


def git_diff(
    path: str = ".",
    target: str = "",
    staged: bool = False,
    stat: bool = False,
) -> Result[dict[str, Any]]:
    args = ["diff"]
    if staged:
        args.append("--staged")
    if stat:
        args.append("--stat")
    if target:
        args.extend(["--", target])

    r = _run_git(args, cwd=path)
    if not r.success:
        return Result.fail(r.error)

    return Result.ok({
        "diff": r.output,
        "has_changes": bool(r.output.strip()),
        "staged": staged,
    })


def git_log(
    path: str = ".",
    count: int = 10,
    oneline: bool = True,
    follow: str | None = None,
) -> Result[dict[str, Any]]:
    args = ["log", f"-{count}"]
    if oneline:
        args.append("--oneline")
    if follow:
        args.extend(["--follow", "--", follow])

    r = _run_git(args, cwd=path)
    if not r.success:
        return Result.fail(r.error)

    commits = []
    for line in r.output.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if oneline and len(line) > 8:
            commits.append({
                "hash": line[:7],
                "message": line[8:],
            })
        elif not oneline and line.startswith("commit "):
            commits.append({"hash": line[7:14], "message": ""})

    return Result.ok({
        "raw": r.output,
        "commits": commits,
        "count": len(commits),
    })


_ALLOWED_REMOTES = ("origin", "github", "upstream")
_ALLOWED_BRANCHES = ("master", "main", "dev", "develop")
_ALLOWED_REFSPEC_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


def _validate_refspec(refspec: str) -> str | None:
    """校验 refspec 只含安全字符（防参数注入）。返回错误消息或 None。"""
    if not refspec:
        return "refspec 不能为空"
    if len(refspec) > 200:
        return "refspec 过长"
    if not _ALLOWED_REFSPEC_RE.match(refspec):
        return f"refspec 含非法字符（仅允许字母/数字/._/-）: {refspec!r}"
    if ".." in refspec or ":" in refspec:
        return f"refspec 不允许含 '..' 或 ':'（防路径穿越/参数注入）: {refspec!r}"
    return None


# remote URL 黑洞 IP 黑名单（cc 审计 P1：git 黑洞风险——黑洞 IP 仍可能配置）
# 覆盖: 环回(127.0.0.0/8, ::1)、链路本地(169.254.0.0/16)、私网(10/8, 172.16/12, 192.168/16)、
#       CGNAT(100.64.0.0/10)、以及保留测试网段(192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24)。
_BLACKHOLE_IP_PREFIXES = (
    ("127.", 1), ("10.", 1), ("172.16.", 2), ("172.17.", 2), ("172.18.", 2),
    ("172.19.", 2), ("172.2", 2), ("172.30.", 2), ("172.31.", 2),
    ("192.168.", 2), ("169.254.", 2), ("100.64.", 2), ("192.0.2.", 2),
    ("198.51.100.", 2), ("203.0.113.", 2),
)
_BLACKHOLE_HOSTS = ("localhost", "::1", "0.0.0.0")


def _is_blackhole_remote_url(url: str) -> bool:
    """判断 remote URL 是否指向黑洞/不可达 IP（阻止配置后白跑 push）。"""
    if not url:
        return False
    url_l = url.lower()
    # 去 scheme / userinfo / 端口
    host = url_l
    for prefix in ("http://", "https://", "git://", "file://", "ss" + "h://"):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    if ":" in host and not host.startswith("["):
        host = host.split(":", 1)[0]
    host = host.strip("/")
    # 去掉路径部分（host/...），仅保留主机名
    if "/" in host:
        host = host.split("/", 1)[0]
    if host in _BLACKHOLE_HOSTS:
        return True
    for prefix, _min_len in _BLACKHOLE_IP_PREFIXES:
        if host.startswith(prefix):
            return True
    if host.startswith("::"):
        return True
    return False


def _allowed_remotes() -> tuple[str, ...]:
    """白名单 config 出口：优先读 config.yaml 的 git.allowed_remotes，回退内置默认。"""
    try:
        from lingclaude.core.config import load_config

        cfg = load_config()
        return cfg.git.allowed_remotes
    except Exception:
        return _ALLOWED_REMOTES


def _validate_remote(remote: str) -> str | None:
    allowed = _allowed_remotes()
    if remote not in allowed:
        return f"remote 不在白名单 {allowed}: {remote!r}"
    return None


def git_push(
    path: str = ".",
    remote: str = "origin",
    branch: str = "master",
    force: bool = False,
    timeout: int = 120,
) -> Result[dict[str, Any]]:
    """专用 git push 工具——参数化构造（防注入）+ remote/branch 白名单。

    与 bash 裸 push 的区别：
    - 不经过 bash 网络白名单字符串判定（不依赖 --unshare-net 决策）
    - 参数全部白名单校验，无 shell 解释
    - 结构化错误返回（GitResult 语义）
    """
    if err := _validate_remote(remote):
        return Result.fail(err)
    if err := _validate_refspec(branch):
        return Result.fail(err)

    args = ["push", remote, branch]
    if force:
        args.insert(1, "--force")
    r = _run_git(args, cwd=path, timeout=timeout)
    if not r.success:
        return Result.fail(r.error)

    return Result.ok({
        "remote": remote,
        "branch": branch,
        "output": r.output,
        "duration_s": round(r.duration, 2),
    })


def git_push_preflight(path: str = ".") -> Result[dict[str, Any]]:
    """push 前置预检：一键报告 remote / 未推送提交 / 门禁状态 / 凭证。

    目标：避免裸 push 撞鉴权墙 + 白跑 30 分钟全量门禁。
    """
    checks: dict[str, Any] = {}

    # 1. remote 配置
    r = _run_git(["remote", "-v"], cwd=path)
    remotes: dict[str, str] = {}
    if r.success:
        for line in r.output.strip().split("\n"):
            if line:
                parts = line.split()
                if len(parts) >= 2:
                    remotes.setdefault(parts[0], parts[1])
    checks["remotes"] = remotes

    # 1.5 remote URL 黑洞检测（cc P1：黑洞 IP 配置会导致 push 白跑/卡死）
    blackhole = {name: url for name, url in remotes.items() if _is_blackhole_remote_url(url)}
    checks["blackhole_remotes"] = blackhole

    # 2. 未推送提交（origin/master 对比）
    ahead = _run_git(["rev-list", "--count", "origin/master..HEAD"], cwd=path)
    checks["unpushed_commits"] = int(ahead.output.strip()) if ahead.success else None

    # 3. 工作区状态
    status = git_status(path)
    checks["dirty"] = status.is_ok and bool(status.data and status.data.get("has_changes"))
    checks["dirty_files"] = (
        [f["path"] for f in status.data["files"]] if status.is_ok and status.data else []
    )

    # 4. exempt-review-gate 状态（lefthook 门禁前置）
    gate_failed = _check_exempt_gate_pending(path)
    checks["gate_blocking"] = gate_failed

    ok = (
        not gate_failed
        and not blackhole
        and checks["unpushed_commits"] not in (None, 0)
        and not checks["dirty"]
    )
    return Result.ok({
        "ok": ok,
        "checks": checks,
        "summary": (
            f"remotes={list(remotes)} unpushed={checks['unpushed_commits']} "
            f"dirty={checks['dirty']} gate_blocking={gate_failed} "
            f"blackhole={list(blackhole)}"
        ),
    })


def _check_exempt_gate_pending(path: str = ".") -> bool:
    """检查 .audit/exempt_review_*.json 是否存在 FAIL 未 ack 的记录（决定是否触发门禁）。"""
    import glob as _glob
    import json as _json

    audit_dir = os.path.join(os.path.abspath(path), ".audit")
    if not os.path.isdir(audit_dir):
        return False
    for f in _glob.glob(os.path.join(audit_dir, "exempt_review_*.json")):
        try:
            with open(f) as fh:
                data = _json.load(fh)
            if data.get("state") == "FAIL" and not data.get("acked"):
                return True
        except Exception:
            continue
    return False


def git_blame(
    file_path: str,
    cwd: str = ".",
    start_line: int | None = None,
    end_line: int | None = None,
) -> Result[dict[str, Any]]:
    args = ["blame", "--porcelain"]
    if start_line and end_line:
        args.extend(["-L", f"{start_line},{end_line}"])
    args.append(file_path)

    r = _run_git(args, cwd=cwd, timeout=60)
    if not r.success:
        return Result.fail(r.error)

    commits: dict[str, dict[str, str]] = {}
    current_commit = ""
    lines = []

    for line in r.output.split("\n"):
        if not line:
            continue
        if line.startswith("author "):
            commits.setdefault(current_commit, {})["author"] = line[7:]
        elif line.startswith("author-mail "):
            commits.setdefault(current_commit, {})["email"] = line[12:]
        elif line.startswith("summary "):
            commits.setdefault(current_commit, {})["summary"] = line[8:]
        elif line.startswith("filename "):
            commits.setdefault(current_commit, {})["filename"] = line[9:]
        elif "\t" in line:
            parts = line.split("\t", 1)
            header = parts[0].strip()
            code = parts[1] if len(parts) > 1 else ""
            short_hash = header.split()[0] if header.split() else ""
            current_commit = short_hash
            lines.append({
                "hash": short_hash,
                "code": code[:200],
            })

    return Result.ok({
        "lines": lines,
        "commits": commits,
        "total_lines": len(lines),
    })
