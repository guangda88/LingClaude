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

    def test_pin_success_updates_model_in_toolbar(self, capsys):
        """核心修复: pin 成功后 status.set_model 必须被调用,否则 toolbar 仍显示 stale model.

        根因: commands.py:142 原实现只调 status.set_pinned(True),
        toolbar 渲染 f"{s.model} [PINNED]" 时 s.model 是旧值 → 用户看到 'deepseek-v4-flash [PINNED]'.
        修复: 在 set_pinned(True) 之前调 status.set_model(str(result.data)).
        """
        from unittest.mock import MagicMock
        from lingclaude.core.types import Result
        cfg = MagicMock()
        cfg.base_url = "https://api.minimaxi.com/v1"
        cfg.model = "MiniMax-M3"
        proc, engine, status = self._make(
            pin_result=Result.ok("MiniMax-M3"),
            pinned_name=None,
        )
        engine._pinned_model_config = cfg
        engine._pinned_model_expires = float("inf")
        proc.handle("/model MiniMax-M3")
        # ★ 核心断言: set_model 被以新 model name 调用
        status.set_model.assert_called_with("MiniMax-M3")
        # 回归: pinned 仍要设 True
        status.set_pinned.assert_called_with(True)


class TestTodoPanel:
    """2026-09-22: todo panel 常驻 toolbar 上方（对标 atomcode）。

    渲染契约：toolbar_fragments 在状态行之前展开清单行（每行以 \\n 结尾），
    状态行保持原有单行形态；空清单 = 输出与旧版逐字符一致（零版面侵占）。
    """

    def test_empty_panel_zero_footprint(self) -> None:
        """无任务时输出与历史形态完全一致——面板不占版面。"""
        s = StatusModel()
        s.set_model("m1")
        s.cwd = "/tmp"
        frag = toolbar_fragments(s.snapshot())
        text = "".join(x[1] for x in frag)
        assert "\n" not in text  # 单行形态，无清单行

    def test_in_progress_and_pending_rows(self) -> None:
        s = StatusModel()
        s.set_model("m1")
        s.cwd = "/tmp"
        s.set_todo_items(
            [("in_progress", "迁移钩子替换"), ("pending", "跑全量回归")]
        )
        frag = toolbar_fragments(s.snapshot())
        text = "".join(x[1] for x in frag)
        assert "⚙ 迁移钩子替换\n" in text
        assert "· 跑全量回归\n" in text
        # 清单行必须先于状态行（面板在 toolbar 上方）
        assert text.index("⚙") < text.index("m1")

    def test_completed_row_and_unknown_status(self) -> None:
        s = StatusModel()
        s.set_todo_items(
            [("completed", "已交付项"), ("weird_status", "未知态项")]
        )
        frag = toolbar_fragments(s.snapshot())
        text = "".join(x[1] for x in frag)
        assert "✓ 已交付项\n" in text
        assert "· 未知态项\n" in text  # 未知状态兜底为 pending 形态

    def test_long_content_truncated(self) -> None:
        s = StatusModel()
        s.set_todo_items([("pending", "x" * 80)])
        frag = toolbar_fragments(s.snapshot())
        text = "".join(x[1] for x in frag)
        assert "…" in text
        # 面板 1 行 + 状态行 1 行 = 2；长内容必须折叠进单行清单，不得换行溢出
        lines = [ln for ln in text.split("\n") if ln.strip()]
        assert len(lines) == 2

    def test_snapshot_thread_safe(self) -> None:
        import threading as _t

        s = StatusModel()
        s.set_todo_items([("pending", "a"), ("in_progress", "b")])
        snap = s.snapshot()
        assert snap.todo_items == (("pending", "a"), ("in_progress", "b"))
        # 并发写不崩（锁面回归）
        errs: list[Exception] = []

        def _w() -> None:
            try:
                for i in range(100):
                    s.set_todo_items([("pending", f"t{i}")])
            except Exception as e:  # noqa: BLE001
                errs.append(e)

        ths = [_t.Thread(target=_w) for _ in range(4)]
        [t.start() for t in ths]
        [t.join() for t in ths]
        assert not errs

    def test_set_none_clears(self) -> None:
        s = StatusModel()
        s.set_todo_items([("pending", "a")])
        s.set_todo_items(None)
        assert s.snapshot().todo_items == ()
