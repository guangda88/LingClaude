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
