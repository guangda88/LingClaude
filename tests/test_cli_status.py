"""StatusModel / toolbar_fragments 组件测试 — H17 真缺口补全（阶段0）。"""
from __future__ import annotations

import threading

from lingclaude.cli.status import StatusModel, toolbar_fragments


class TestStatusModel:
    def test_snapshot_thread_safe(self) -> None:
        s = StatusModel()
        s.set_model("test/model")
        s.set_task("生成中")
        s.bump_turns()
        s.set_pending(3)
        s.set_ctx(500, 1000)
        snap = s.snapshot()
        assert snap.model == "test/model"
        assert snap.task == "生成中"
        assert snap.turns == 1
        assert snap.pending == 3
        assert snap.ctx_tokens == 500
        assert snap.ctx_window == 1000

    def test_concurrent_updates(self) -> None:
        s = StatusModel()
        errors: list[Exception] = []

        def writer() -> None:
            try:
                for _ in range(100):
                    s.bump_turns()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert s.snapshot().turns == 400

    def test_refresh_cwd(self) -> None:
        s = StatusModel()
        s.refresh_cwd()
        assert s.snapshot().cwd != ""


class TestToolbarFragments:
    def test_basic_fragments(self) -> None:
        s = StatusModel()
        s.set_model("m1")
        s.cwd = "/tmp"
        s.set_ctx(100, 1000)
        s.set_task("空闲")
        s.bump_turns()
        frag = toolbar_fragments(s.snapshot())
        assert isinstance(frag, list)
        text = "".join(x[1] for x in frag)
        assert "m1" in text
        assert "/tmp" in text
        assert "10%" in text
        assert "1轮" in text

    def test_ctx_ratio_color_green(self) -> None:
        s = StatusModel()
        s.set_ctx(100, 1000)
        frag = toolbar_fragments(s.snapshot())
        green = [x for x in frag if "green" in x[0]]
        assert len(green) > 0

    def test_ctx_ratio_color_yellow(self) -> None:
        s = StatusModel()
        s.set_ctx(700, 1000)
        frag = toolbar_fragments(s.snapshot())
        yellow = [x for x in frag if "yellow" in x[0]]
        assert len(yellow) > 0

    def test_ctx_ratio_color_red_warn(self) -> None:
        s = StatusModel()
        s.set_ctx(900, 1000)
        frag = toolbar_fragments(s.snapshot())
        red = [x for x in frag if "red" in x[0]]
        assert len(red) > 0
        text = "".join(x[1] for x in frag)
        assert "⚠将压缩" in text

    def test_pending_badge(self) -> None:
        s = StatusModel()
        s.set_pending(2)
        frag = toolbar_fragments(s.snapshot())
        text = "".join(x[1] for x in frag)
        assert "挂起×2" in text

    def test_unknown_ctx_window(self) -> None:
        s = StatusModel()
        s.set_ctx(50, 0)
        frag = toolbar_fragments(s.snapshot())
        text = "".join(x[1] for x in frag)
        assert "50tok" in text

    def test_long_task_truncated(self) -> None:
        s = StatusModel()
        s.set_task("x" * 60)
        frag = toolbar_fragments(s.snapshot())
        text = "".join(x[1] for x in frag)
        assert "…" in text
class TestPinFailureClearsStaleStatus:
    """[真实bug修复] /model <name> 失败路径必须清 toolbar [PINNED] 残留状态.

    根因: commands.py:143-145 原实现只 print 错误然后 return,不调用 status.set_pinned(False),
    也不告知用户原 pin 仍生效. 用户看到 toolbar 还显示旧 model + [PINNED] 会以为新 pin 成功了.
    """

    def _make(self, pin_result=None, pinned_name=None):
        """构造 SlashCommandProcessor + mock engine/status."""
        from unittest.mock import MagicMock
        from lingclaude.cli.commands import SlashCommandProcessor

        engine = MagicMock()
        engine.pin_model.return_value = pin_result
        engine.get_pinned_model_name.return_value = pinned_name
        status = MagicMock()
        proc = SlashCommandProcessor(engine=engine, status=status)
        return proc, engine, status

    def test_pin_failure_clears_stale_pinned_flag(self, capsys):
        """核心: pin 失败时 status.set_pinned(False) 必须被调用."""
        from lingclaude.core.types import Result
        proc, _engine, status = self._make(
            pin_result=Result.fail("unknown model 'MiniMax-M3'", code="PROVIDER_CREATE_FAILED"),
            pinned_name="deepseek-v4-flash",
        )
        proc.handle("/model MiniMax-M3")
        status.set_pinned.assert_called_with(False)

    def test_pin_failure_warns_about_stale_pin(self, capsys):
        """失败时若原 pin 仍存在,必须给用户醒目提示."""
        from lingclaude.core.types import Result
        proc, _engine, status = self._make(
            pin_result=Result.fail("unknown model 'MiniMax-M3'", code="PROVIDER_CREATE_FAILED"),
            pinned_name="deepseek-v4-flash",
        )
        proc.handle("/model MiniMax-M3")
        captured = capsys.readouterr()
        assert "[!] 注意: 原钉住 deepseek-v4-flash 仍然有效" in captured.out
        assert "/model --unpin" in captured.out

    def test_pin_failure_no_stale_no_warning(self, capsys):
        """失败时若之前无 pin,不应显示 '原钉住' 警告(避免误导)."""
        from lingclaude.core.types import Result
        proc, _engine, status = self._make(
            pin_result=Result.fail("unknown model 'MiniMax-M3'", code="PROVIDER_CREATE_FAILED"),
            pinned_name=None,
        )
        proc.handle("/model MiniMax-M3")
        captured = capsys.readouterr()
        assert "原钉住" not in captured.out
        status.set_pinned.assert_called_with(False)  # 但清状态仍要做

    def test_pin_success_still_sets_pinned_true(self, capsys):
        """回归: 成功路径仍调用 status.set_pinned(True),不被本次改动破坏."""
        from unittest.mock import MagicMock
        from lingclaude.core.types import Result
        cfg = MagicMock()
        cfg.base_url = "https://api.deepseek.com/v1"
        cfg.model = "deepseek-v4-flash"
        proc, engine, status = self._make(
            pin_result=Result.ok("deepseek-v4-flash"),
            pinned_name=None,
        )
        engine._pinned_model_config = cfg
        engine._pinned_model_expires = float("inf")
        proc.handle("/model deepseek-v4-flash")
        status.set_pinned.assert_called_with(True)
