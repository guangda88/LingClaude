from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def _isolate_git_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试仓库不继承用户全局 core.hooksPath（~/.git-hooks 的 post-commit
    会撤销无审计记录的提交，干扰测试夹具仓库）。通过 GIT_CONFIG_* 环境变量
    强制 core.hooksPath 指向空目录，对测试内所有 git 子进程生效。"""
    empty_hooks = tempfile.mkdtemp(prefix="empty_git_hooks_")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", empty_hooks)


# RFC v0.1 §3 A1-1 修订:pytest 环境默认禁用 BusResponder 后台线程
# 灵克 cli 默认 = 1(生产开),pytest 通过 conftest 设 = 0 防卡死
# 用户主动 export LINGCLAUDE_BUS_LISTENER=1 可强制开(覆盖 conftest)
os.environ["LINGCLAUDE_BUS_LISTENER"] = "0"


# 2026-09-06 rm -rf 事故加固：autouse 检测测试对项目 .lingclaude/ 的写污染。
# 背景：lingclaude 会话为构造"干净 guard 测试环境"执行了 rm -rf .lingclaude，
# 导致运行时四库丢失。规则：测试若要触碰运行时目录，必须 chdir 到 tmp_path——
# 项目真实 .lingclaude/ 在测试前后必须逐字节一致，否则 fail 并列出污染文件。
@pytest.fixture(autouse=True)
def _protect_project_lingclaude(request: pytest.FixtureRequest) -> None:
    import os
    from pathlib import Path

    project_rt = Path(__file__).resolve().parent.parent / ".lingclaude"
    if not project_rt.exists():
        yield
        return

    def _snapshot() -> dict[str, tuple[int, int]]:
        snap: dict[str, tuple[int, int]] = {}
        for p in project_rt.rglob("*"):
            if p.is_file():
                try:
                    st = p.stat()
                    snap[str(p.relative_to(project_rt))] = (st.st_mtime_ns, st.st_size)
                except OSError:
                    continue
        return snap

    before = _snapshot()
    cwd = os.getcwd()
    yield
    os.chdir(cwd)  # 防测试遗留 chdir 污染后续用例的相对路径断言
    after = _snapshot()
    # 事故特征判定（不是任何写都算污染——QueryEngine 正常运行时也会写
    # meta_cognition/knowledge.db，那是设计行为）。只 fail 破坏性签名：
    #   1. 之前存在的文件被删除（rm -rf 类）
    #   2. 之前非空的文件变成 0 字节（截断/清空类）
    # SQLite -wal/-shm 是连接 checkpoint 后的正常瞬态文件，不算破坏
    def _transient(k: str) -> bool:
        return k.endswith(("-wal", "-shm"))

    polluted = sorted(
        k for k, (mt, size) in before.items()
        if not _transient(k) and (k not in after or (after[k][1] == 0 and size > 0))
    )
    if polluted:
        pytest.fail(
            "测试污染了项目 .lingclaude/（请在 tmp_path 中隔离运行时写入）："
            + ", ".join(polluted[:8])
            + (" ..." if len(polluted) > 8 else ""),
            pytrace=False,
        )
