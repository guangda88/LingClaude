"""多 agent 文件编辑锁 — flock + 陈旧锁回收。

背景：2026-09-06 前后，lingclaude 会话、Claude Code、监督者三方先后编辑
同一批文件（app.py/daemon.py/config.yaml），叠加出缩进损坏与互相覆盖。

为什么此前没有锁：
1. 各 agent 属不同运行时（CLI/python/外部工具），没有共享协调层
2. 过去故障模式是"顺序踩踏"（A 改完 B 又改），不是严格同时写——
   flock 解决后者，防不了前者，所以还需要"改前先看 git status/mtime"的纪律
3. 锁的持有者死亡会留死锁，必须有陈旧回收

用法:
    from lingclaude.core.file_lock import file_edit_lock
    with file_edit_lock("lingclaude/cli/app.py", owner="codex-supervisor"):
        ...读取-修改-写入...
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import time
from contextlib import contextmanager
from pathlib import Path

_LOCK_DIR = Path(".lingclaude") / "locks"
_STALE_SECONDS = 600  # 超过 10 分钟视为陈旧锁，允许夺走


def _lock_path(target: Path) -> Path:
    digest = hashlib.sha1(str(target.resolve()).encode()).hexdigest()[:16]  # nosec B324 — 非安全用途
    return _LOCK_DIR / f"{digest}.lock"


@contextmanager
def file_edit_lock(target: Path | str, owner: str = "", timeout: float = 30.0):
    """对 target 获取排他编辑锁；失败抛 TimeoutError（fail-closed）。"""
    target = Path(target)
    _LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(target)
    deadline = time.monotonic() + timeout

    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                # 陈旧锁回收：持锁进程已死或锁太老 → 夺走
                st = os.fstat(fd)
                holder_pid = _parse_holder_pid(lock_path)
                stale = (time.time() - st.st_mtime) > _STALE_SECONDS or (
                    holder_pid is not None and not os.path.exists(f"/proc/{holder_pid}")
                )
                if stale:
                    os.close(fd)
                    fd = os.open(lock_path, os.O_RDWR | os.O_TRUNC | os.O_CREAT, 0o644)
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"文件编辑锁超时: {target}（持有者: {holder_pid or '未知'}，"
                        f"owner={owner}）— 请确认其他 agent 是否仍在编辑"
                    )
                time.sleep(0.2)

        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}|{owner}|{time.time()}".encode())
        yield lock_path
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _parse_holder_pid(lock_path: Path) -> int | None:
    try:
        first = lock_path.read_text(encoding="utf-8").split("|")[0].strip()
        return int(first) if first.isdigit() else None
    except (OSError, ValueError):
        return None
