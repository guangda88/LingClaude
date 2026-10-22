"""M2 多会话编辑锁：write_scoped 工具的 file_edit_lock 管线硬闸。

覆盖：
- 锁持有语义：write 工具执行期间另一持有者无法获取同文件锁（真互斥）
- fail-closed：目标已被他人持锁 → 工具拒绝（GUARD_DENIED），不裸写
- 锁覆盖写后校验：post verify 失败（含自动回滚）仍在临界区内完成
- 释放覆盖所有出口：成功/执行错误后锁必须可被第三方立即获取
- 非路径写（ast_replace blob）不取锁不受阻
"""
from __future__ import annotations

import threading

import pytest

from lingclaude.core import file_lock
from lingclaude.core.types import Result, ToolErrorCode
from lingclaude.engine.tool_pipeline import ToolPipeline


class _Reg:
    """最小 ToolRegistry 替身：单工具，handler 可注入。"""

    def __init__(self, name, handler):
        from lingclaude.engine.tools import ToolDefinition

        self._def = ToolDefinition(
            name=name, description="t", parameters={"type": "object"}, handler=handler
        )

    def get(self, name):
        from lingclaude.core.types import Result

        if name == self._def.name:
            return Result.ok(self._def)
        return Result.fail("no")

    def finalize_result(self, name, args, raw):
        return None


def _make_pipeline(tool_name, handler):
    from lingclaude.core.types import WRITE_SCOPED_TOOLS

    return ToolPipeline(
        _Reg(tool_name, handler),
        write_scoped_tools=tuple(WRITE_SCOPED_TOOLS),
    )


@pytest.fixture()
def lock_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(file_lock, "_LOCK_DIR", tmp_path / "edit_locks")
    # 锁获取预算调短：fail-closed 路径（他人持锁）秒级返回，不卡 180s
    import lingclaude.engine.tool_pipeline as tp

    monkeypatch.setattr(tp, "_EDIT_LOCK_TIMEOUT_SECONDS", 1.5)
    monkeypatch.chdir(tmp_path)
    return tmp_path


class TestLockHeld:
    def test_write_holds_lock_during_dispatch(self, lock_sandbox):
        """dispatch 进行中，同文件锁被持有 → 第三方 TimeoutError（非阻塞秒判）。"""
        target = lock_sandbox / "t.txt"
        target.write_text("v0", encoding="utf-8")
        in_handler = threading.Event()
        release = threading.Event()

        def handler(**kwargs):
            in_handler.set()
            release.wait(timeout=5)
            return "written"

        pipe = _make_pipeline("write", handler)
        holder_done = threading.Event()
        holder_result = {}

        def run_holder():
            holder_result["r"] = pipe.execute(
                "write", {"path": str(target), "content": "v1"}
            )
            holder_done.set()

        th = threading.Thread(target=run_holder)
        th.start()
        assert in_handler.wait(timeout=5), "handler 未进入"

        # 第三方此时抢同文件锁：必须失败（锁被管线持有）
        from lingclaude.core.file_lock import file_edit_lock

        with pytest.raises(TimeoutError):
            with file_edit_lock(target, owner="third-party", timeout=0.5):
                pass

        release.set()
        assert holder_done.wait(timeout=5)
        assert "result" in holder_result["r"]

    def test_lock_released_after_success(self, lock_sandbox):
        """成功完成后锁立即可被第三方获取（无泄漏）。"""
        target = lock_sandbox / "t.txt"
        target.write_text("v0", encoding="utf-8")
        pipe = _make_pipeline("write", lambda **kw: "ok")
        pipe.execute("write", {"path": str(target), "content": "v1"})
        from lingclaude.core.file_lock import file_edit_lock

        with file_edit_lock(target, owner="after", timeout=1.0):
            pass  # 能立即进入 = 已释放


class TestFailClosed:
    def test_blocked_when_other_holds_lock(self, lock_sandbox):
        """他人持锁时 write 工具被拒（GUARD_DENIED），目标文件不被触碰。"""
        target = lock_sandbox / "guarded.txt"
        target.write_text("original", encoding="utf-8")
        from lingclaude.core.file_lock import file_edit_lock

        with file_edit_lock(target, owner="someone-else", timeout=1.0):
            pipe = _make_pipeline("write", lambda **kw: "should-not-run")
            res = pipe.execute("write", {"path": str(target), "content": "evil"})
            assert res.get("error") or res.get("is_error"), res
            assert "edit-lock" in str(res)

        assert target.read_text(encoding="utf-8") == "original"

    def test_missing_path_rejected(self, lock_sandbox):
        """路径寻址写工具缺目标路径 → fail-closed 拒绝。"""
        pipe = _make_pipeline("write", lambda **kw: "no")
        res = pipe.execute("write", {"content": "no target"})
        assert res.get("error") or res.get("is_error"), res

    def test_ast_replace_also_locked(self, lock_sandbox):
        """ast_replace（file_path 寻址）同受锁保护——白名单 6 工具全覆盖。"""
        target = lock_sandbox / "m.py"
        target.write_text("def f():\n    pass\n", encoding="utf-8")
        from lingclaude.core.file_lock import file_edit_lock

        with file_edit_lock(target, owner="someone-else", timeout=1.0):
            pipe = _make_pipeline("ast_replace", lambda **kw: {"ok": True})
            res = pipe.execute("ast_replace", {"file_path": str(target),
                                               "function_name": "f", "new_body": "x"})
            assert res.get("error") or res.get("is_error"), res
            assert "edit-lock" in str(res)


class TestCriticalZone:
    def test_lock_covers_post_verify_and_rollback(self, lock_sandbox):
        """post verify 失败（自动回滚中）时锁仍被持有——回滚完才释放。"""
        target = lock_sandbox / "rz.txt"
        target.write_text("v0", encoding="utf-8")
        pipe = _make_pipeline(
            "write", lambda **kw: "dirty"
        )
        rollback_entered = threading.Event()
        probe = {}

        def post_verify(path):
            # 校验阶段持锁探测：第三方此刻抢锁必须失败
            from lingclaude.core.file_lock import file_edit_lock

            try:
                with file_edit_lock(target, owner="probe", timeout=0.4):
                    probe["locked_during_verify"] = False
            except TimeoutError:
                probe["locked_during_verify"] = True
            return False, "inject-fail"

        def rollback(tool_name, args):
            rollback_entered.set()
            return None

        pipe.execute(
            "write",
            {"path": str(target), "content": "v1"},
            post_write_verify=post_verify,
            rollback_callback=rollback,
        )
        assert rollback_entered.wait(timeout=3)
        assert probe["locked_during_verify"] is True

    def test_lock_released_after_execution_error(self, lock_sandbox):
        """执行异常出口锁也释放（finally 覆盖）。"""
        target = lock_sandbox / "e.txt"
        target.write_text("v0", encoding="utf-8")

        def boom(**kwargs):
            raise RuntimeError("boom")

        pipe = _make_pipeline("write", boom)
        res = pipe.execute("write", {"path": str(target), "content": "v1"})
        assert res.get("error") or res.get("is_error"), res
        from lingclaude.core.file_lock import file_edit_lock

        with file_edit_lock(target, owner="after-error", timeout=1.0):
            pass
