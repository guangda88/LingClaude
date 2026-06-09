"""Git integration tools for lingclaude.

Provides structured access to git information without raw shell access:
- git_status: working tree status
- git_diff: staged/unstaged changes
- git_log: commit history
- git_blame: line-level authorship
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
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
    args = ["status", "--porcelain"] if short else ["status"]
    r = _run_git(args, cwd=path)
    if not r.success:
        return Result.fail(r.error)

    files = []
    if short and r.output.strip():
        for line in r.output.strip().split("\n"):
            if len(line) >= 4:
                status = line[:2].strip()
                path = line[3:].strip()
                # Ignore .audit directory created by pre-commit hooks
                if not path.startswith(".audit/"):
                    files.append({
                        "status": status,
                        "path": path,
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
