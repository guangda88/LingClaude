# lingclaude/core/worktree.py
"""P1-6（2026-09-21，全 15 家精读 §3.2 Orca 式 worktree 扇出）。

Orca 核心语义：每个并行任务/agent 落在独立 git worktree（真实分支），
互不污染共享工作区；结果可 diff、可择优、可回滚/merge。lc 现状是
agent_batch 用 scratch 目录（data/agent_dispatch/<ts>_<uuid>/），
产物不可 diff 择优。本模块把「并行执行单元」升级为 git worktree 一等对象。

设计（lc 适配）：
- 一个 session 的 N 个并行任务 → N 个 worktree，各自在真实分支上工作；
- 主分支（target）保持不动，任务分支从 target 派生；
- 任务完成后：diff 对比 / 择优 / merge 回 target 或丢弃（回滚=删 worktree）；
- 非 git 仓库降级：用 scratch 目录（保留 agent_batch 旧行为，不崩）。

与 Orca 的差异（有意简化）：
- Orca 有 coordinator 状态机 + 线系（parent/child）；lc 当前先做「扇出 + 回收」
  最小闭环，状态机留给族级编排（B 路线）再上。

停层声明（铁律 2 细则 5）：
- 内核 = WorktreeSession（create/diff/merge/cleanup 的纯 git 调用封装）
- 接缝 = WorktreeFanout 协议（fanout() 扇出多个任务到多 worktree）
- 实现 = 单实现（subprocess git），非 git 仓库降级 scratch
边界纪律：只操作 git worktree 子命令，不改 target 分支历史（merge 是显式动作）。
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _git(*args: str, cwd: Path) -> tuple[int, str]:
    """git 子命令执行，返回 (exit_code, stdout+stderr 合并)。永不抛异常。"""
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=str(cwd), capture_output=True, text=True, timeout=60,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


@dataclass
class WorktreeTask:
    """一个并行任务落在的 worktree 单元。"""
    task_id: str
    branch: str
    path: Path
    target: str
    created: float = field(default_factory=time.time)
    merged: bool = False
    discarded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "branch": self.branch,
            "path": str(self.path), "target": self.target,
            "merged": self.merged, "discarded": self.discarded,
        }


class WorktreeSession:
    """git worktree 扇出会话（Orca worktree 一等域对象的 lc 对位）。

    用法：
        ws = WorktreeSession(repo="/path/to/repo", target="master")
        wt = ws.create("audit-lc", base="master")
        ... 在 wt.path 上跑任务 ...
        verdict = ws.diff(wt)          # 与 target 的差异
        ws.merge(wt)                   # 择优合并回 target（显式动作）
        ws.cleanup(wt)                 # 丢弃（删 worktree + 分支）
    """

    def __init__(self, repo: str | Path, target: str = "master") -> None:
        self.repo = Path(repo).resolve()
        self.target = target
        self._wt_root = self.repo / ".lingclaude" / "worktrees"
        self._is_git = self._probe_git()

    def _probe_git(self) -> bool:
        code, _ = _git("rev-parse", "--git-dir", cwd=self.repo)
        return code == 0

    @property
    def available(self) -> bool:
        """git worktree 是否可用（非 git 仓库时降级 scratch，返回 False）。"""
        return self._is_git

    # ── 扇出 ──

    def create(self, task_id: str, base: str | None = None, *, prefix: str = "lc-wt") -> WorktreeTask:
        """为 task_id 建独立 worktree（分支 prefix-<task_id>-<rand>，基于 base/target）。"""
        base_ref = base or self.target
        rand = uuid.uuid4().hex[:6]
        branch = f"{prefix}-{task_id}-{rand}"
        wt_path = self._wt_root / f"{task_id}-{rand}"
        task = WorktreeTask(task_id=task_id, branch=branch, path=wt_path, target=base_ref)

        if not self._is_git:
            # 非 git 仓库降级：直接用 scratch 目录（保留 agent_batch 旧行为）
            wt_path.mkdir(parents=True, exist_ok=True)
            task.branch = ""
            logger.info("worktree: 非 git 仓库，task %s 降级 scratch dir %s", task_id, wt_path)
            return task

        self._wt_root.mkdir(parents=True, exist_ok=True)
        # git worktree add <path> -b <branch> <base_ref>
        code, out = _git("worktree", "add", str(wt_path), "-b", branch, base_ref,
                          cwd=self.repo)
        if code != 0:
            # base_ref 不存在等 → 退回从 HEAD 建
            code, out = _git("worktree", "add", str(wt_path), "-b", branch, "HEAD",
                             cwd=self.repo)
        if code != 0:
            raise RuntimeError(f"git worktree add 失败: {out.strip()}")
        logger.info("worktree created: task=%s branch=%s path=%s (base=%s)",
                    task_id, branch, wt_path, base_ref)
        return task

    def fanout(self, tasks: list[str], base: str | None = None) -> list[WorktreeTask]:
        """一次扇出 N 个任务到 N 个独立 worktree（Orca 扇出的最小形态）。"""
        return [self.create(tid, base) for tid in tasks]

    # ── 回收（diff / 择优 / merge / 丢弃） ──

    def diff(self, task: WorktreeTask, target: str | None = None) -> str:
        """任务 worktree 相对 target 分支的 unified diff（择优依据）。"""
        tgt = target or task.target
        if not task.branch:
            return ""
        code, out = _git("diff", f"{tgt}...{task.branch}", "--stat", cwd=self.repo)
        if code != 0:
            code, out = _git("diff", "HEAD", "--stat", cwd=task.path)
        return out

    def merge(self, task: WorktreeTask, *, message: str | None = None,
              target: str | None = None) -> bool:
        """把任务分支 merge 回 target（择优接受；显式动作，成功才置 merged）。"""
        if not task.branch:
            return False
        tgt = target or self.target
        msg = message or f"merge worktree task {task.task_id} ({task.branch})"
        code, out = _git("merge", "--no-ff", task.branch, "-m", msg, cwd=self.repo)
        if code != 0:
            # 冲突 → 回滚 merge，保留 worktree 供人工处理
            _git("merge", "--abort", cwd=self.repo)
            logger.warning("worktree merge 冲突已 abort: task=%s (%s)", task.task_id, out.strip()[:120])
            return False
        task.merged = True
        logger.info("worktree merged: task=%s %s → %s", task.task_id, task.branch, tgt)
        return True

    def cleanup(self, task: WorktreeTask, *, remove_branch: bool = True) -> None:
        """丢弃任务 worktree（删 worktree + 可选删分支）——回滚语义。"""
        if not task.branch:
            # scratch 降级模式：直接删目录
            shutil.rmtree(task.path, ignore_errors=True)
            task.discarded = True
            return
        if task.merged:
            _git("worktree", "remove", str(task.path), cwd=self.repo)
            if remove_branch:
                _git("branch", "-D", task.branch, cwd=self.repo)
        else:
            # 未 merge 的 worktree 需 --force 才能 remove（有未提交/已分叉）
            _git("worktree", "remove", "--force", str(task.path), cwd=self.repo)
            if remove_branch:
                _git("branch", "-D", task.branch, cwd=self.repo)
        task.discarded = True
        logger.info("worktree cleanup: task=%s discarded=%s merged=%s",
                    task.task_id, task.discarded, task.merged)

    def list_all(self) -> list[dict[str, Any]]:
        """列出当前所有 lc worktree（运维/探活面）。"""
        code, out = _git("worktree", "list", "--porcelain", cwd=self.repo)
        if code != 0:
            return []
        entries: list[dict[str, Any]] = []
        cur: dict[str, Any] = {}
        for line in out.splitlines():
            if line.startswith("worktree "):
                if cur:
                    entries.append(cur)
                cur = {"path": line[len("worktree "):].strip()}
            elif line.startswith("HEAD "):
                cur["head"] = line[len("HEAD "):].strip()
            elif line.startswith("branch "):
                cur["branch"] = line[len("branch "):].strip()
            elif line.strip() == "":
                if cur:
                    entries.append(cur)
                    cur = {}
        if cur:
            entries.append(cur)
        return entries
