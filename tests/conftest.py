from __future__ import annotations

# H18 环境修复: pytest capture 运行时读 os.devnull — /dev/null 只读沙箱下
# 必须在 pytest 初始化 capture 前重写（conftest 加载早于 capture 初始化，时机正确）。
# __future__ 之后、其余 import 之前，此为本文件第一条可执行语句。
import lingclaude.core.devnull_compat  # noqa: F401

import os
import tempfile
import warnings

import pytest

# T3 handler 解耦迁移期：存量测试直传 ToolDefinition(handler=...) 产生
# DeprecationWarning，在此静默（按 message 前缀匹配 — 测试常用 exec/eval
# 构造，栈帧 module 不落在 tools.py）。新代码一律走 handler_name。
warnings.filterwarnings("ignore", category=DeprecationWarning,
                        message=r"ToolDefinition\(handler=\.\.\.\) 已废弃.*")


@pytest.fixture(autouse=True)
def _isolate_git_hooks(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试仓库不继承用户全局 core.hooksPath（~/.git-hooks 的 post-commit
    会撤销无审计记录的提交，干扰测试夹具仓库）。通过 GIT_CONFIG_* 环境变量
    强制 core.hooksPath 指向空目录，对测试内所有 git 子进程生效。"""
    empty_hooks = tempfile.mkdtemp(prefix="empty_git_hooks_")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", empty_hooks)
    # 2026-09-09: pre-commit 审计钩子在 git commit 进程内跑全量测试，环境带
    # GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE → 测试内所有 git 子进程被劫持指向
    # 主仓库（test_git.py 9 用例假失败，xdist 并行下噪音更大）。统一剥离，
    # 使钩子内跑测试与手动跑测试行为一致。
    for _var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        monkeypatch.delenv(_var, raising=False)




@pytest.fixture(autouse=True)
def _isolate_tool_vocab(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """T5: ToolRouter 学习词表持久化 — 测试一律重定向到 tmp_path，
    不触碰项目 .lingclaude/tool_vocab.json（持久化代码路径仍被真实执行）。"""
    monkeypatch.setenv("LINGCLAUDE_TOOL_VOCAB_PATH",
                       str(tmp_path / "tool_vocab.json"))
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
    # SQLite -wal/-shm 是连接 checkpoint 后的正常瞬态文件，不算破坏。
    # 原子写中间产物 *.tmp<PID>（state_store._atomic_write_json，2026-09-21 账）：
    # 活体灵克进程 3671643 的 tmp 恰跨测试 before/after 快照窗口，replace 后
    # 消失，被误判为「删除类破坏」（meta_cognition.json.tmp3671643 误报实证）。
    # tmp 本就是短命设计产物，与 -wal/-shm 同类豁免。
    import re

    _tmp_pid_re = re.compile(r"\.tmp\d+$")

    def _transient(k: str) -> bool:
        name = k.rsplit("/", 1)[-1]
        return name.endswith(("-wal", "-shm")) or bool(_tmp_pid_re.search(name))

    polluted = sorted(
        k for k, (mt, size) in before.items()
        if not _transient(k) and (k not in after or (after[k][1] == 0 and size > 0))
    )
    if polluted:
        # 2026-09-18 xdist 账：项目外活体进程（daemon watch/其他灵克会话）会写
        # .lingclaude/ 状态文件，其非原子写窗口的瞬时 0 字节态会被快照对比误判
        # 为测试污染（8worker 下 test_absent_carriers_are_honest teardown 误报
        # meta_cognition.json 实证）。生产端已改原子写（state_store._atomic_write_json），
        # 此处再加复查：短暂等待后重查，仍处删除/清零态才判污染——过滤外部
        # 进程的瞬态窗口，也兼容旧版非原子写的活体进程。
        import time
        time.sleep(1.0)
        confirmed: list[str] = []
        for k in polluted:
            fp = project_rt / k
            try:
                if not fp.exists() or (before[k][1] > 0 and fp.stat().st_size == 0):
                    confirmed.append(k)
            except OSError:
                confirmed.append(k)
        polluted = confirmed
    if polluted:
        pytest.fail(
            "测试污染了项目 .lingclaude/（请在 tmp_path 中隔离运行时写入）："
            + ", ".join(polluted[:8])
            + (" ..." if len(polluted) > 8 else ""),
            pytrace=False,
        )
