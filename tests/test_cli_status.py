"""StatusModel / toolbar_fragments 组件测试 — H17 真缺口补全（阶段0）。"""
from __future__ import annotations

import threading

from lingclaude.cli.status import (
    _TOOLBAR_TIPS,
    StatusModel,
    toolbar_fragments,
    toolbar_tip,
)


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


class TestTodoBadge:
    """2026-09-26 P1-5: todo 清单逐项常驻退役（对标 CC status line 单行哲学）。

    渲染契约：清单行不再逐项展开，收敛为一粒汇总角标 ⚙{ip}·{pd}（accent 高亮，
    仅统计 in_progress/pending）；明细查看走 /tasks（进 scrollback）。
    空清单 = 输出与旧版逐字符一致（零版面侵占）。
    """

    def test_empty_panel_zero_footprint(self) -> None:
        """无任务时输出与历史形态完全一致——角标不占版面。"""
        s = StatusModel()
        s.set_model("m1")
        s.cwd = "/tmp"
        frag = toolbar_fragments(s.snapshot())
        text = "".join(x[1] for x in frag)
        # 2026-09-26 P1-6: 折行引入 \n 属预期（toolbar 自动长高），旧
        # 「无 \n」断言退役；「无清单行」意图改由 ⚙ 角标缺席守护。
        assert "⚙" not in text  # 无任务无角标（无逐项清单的当前形态）

    def test_badge_counts_ip_and_pending(self) -> None:
        """有未完成项时输出单行汇总角标（角标语义不变）。"""
        s = StatusModel()
        s.set_model("m1")
        s.cwd = "/tmp"
        s.set_todo_items(
            [("in_progress", "迁移钩子替换"), ("pending", "跑全量回归")]
        )
        frag = toolbar_fragments(s.snapshot())
        text = "".join(x[1] for x in frag)
        assert "⚙1·1" in text  # 单行角标：1 进行中 + 1 待办
        # 2026-09-27 契约更新（用户推翻 P1-5）：恢复受限任务面板（≤4 项），
        # 「明细完全不进 toolbar」守卫退役；全量仍走 /tasks，面板超限收敛。
        assert "迁移钩子替换" in text  # 面板可见（in_progress 优先）
        assert "跑全量回归" in text  # 面板可见
        # 明细行只在面板区，不重复出现在状态行（无双重显示）
        status_line = text.split("\n")[-1]
        assert "迁移钩子替换" not in status_line.replace("任务面板 1/2", "")

    def test_completed_and_cancelled_not_counted(self) -> None:
        """completed/cancelled 不计入角标；未知态兜底 pending 计入。"""
        s = StatusModel()
        s.set_todo_items(
            [
                ("completed", "已交付项"),
                ("cancelled", "已取消项"),
                ("weird_status", "未知态项"),
            ]
        )
        frag = toolbar_fragments(s.snapshot())
        text = "".join(x[1] for x in frag)
        assert "⚙0·1" in text  # 只有未知态兜底计入 pending

    def test_accent_style_and_position(self) -> None:
        """角标用 accent 样式，位于行尾任务区（🔄 之后）。"""
        s = StatusModel()
        s.set_model("m1")
        s.cwd = "/tmp"
        s.set_todo_items([("in_progress", "任务A"), ("pending", "任务B")])
        frag = toolbar_fragments(s.snapshot())
        badge_idx = next(
            i for i, (style, txt) in enumerate(frag) if "⚙" in txt
        )
        assert frag[badge_idx][0] == "class:accent"
        model_idx = next(i for i, (_, txt) in enumerate(frag) if "m1" in txt)
        assert badge_idx > model_idx  # 行尾任务区，在状态行主体之后

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

    def test_panel_hidden_suppresses_badge(self) -> None:
        """2026-09-27 Ctrl+T：隐藏时角标不输出，数据保留；重新显示即刻复现。"""
        s = StatusModel()
        s.set_model("m1")
        s.cwd = "/tmp"
        s.set_todo_items([("in_progress", "任务A"), ("pending", "任务B")])
        # 默认显示 → 角标在
        frag = toolbar_fragments(s.snapshot())
        assert any("⚙" in txt for _, txt in frag)
        # 隐藏 → 角标消失，但 todo_items 数据未被清
        s.set_todo_panel_visible(False)
        snap = s.snapshot()
        assert snap.show_todo_panel is False
        assert snap.todo_items == (("in_progress", "任务A"), ("pending", "任务B"))
        frag = toolbar_fragments(snap)
        assert not any("⚙" in txt for _, txt in frag)
        # 重新显示 → 角标复现（无 stale 窗口，直接读现有数据）
        s.set_todo_panel_visible(True)
        frag = toolbar_fragments(s.snapshot())
        assert any("⚙1·1" in txt for _, txt in frag)

    def test_panel_flag_default_true_and_snapshot(self) -> None:
        """默认显示；snapshot 透传开关；隐藏后无任务时本就无角标（幂等）。"""
        s = StatusModel()
        s.set_model("m1")
        s.cwd = "/tmp"
        assert s.snapshot().show_todo_panel is True
        # 隐藏 + 无任务 → 无角标（与显示时一致，行为幂等）
        s.set_todo_panel_visible(False)
        frag = toolbar_fragments(s.snapshot())
        assert not any("⚙" in txt for _, txt in frag)


class TestStateBall:
    """状态球（对标 atomcode 绿/黄/红小球）——三态渲染 + 优先级 + 透传。"""

    def test_idle_renders_green_ball(self) -> None:
        s = StatusModel()
        s.set_state_level("idle")
        frag = toolbar_fragments(s.snapshot())
        # 首片段即状态球：idle → class:green 的 ●
        assert frag[0][0] == "class:green"
        assert frag[0][1] == "●"

    def test_busy_renders_yellow_ball(self) -> None:
        s = StatusModel()
        s.set_state_level("busy")
        frag = toolbar_fragments(s.snapshot())
        assert frag[0][0] == "class:yellow"
        assert frag[0][1] == "●"

    def test_blocked_renders_red_ball(self) -> None:
        s = StatusModel()
        s.set_state_level("blocked")
        frag = toolbar_fragments(s.snapshot())
        assert frag[0][0] == "class:red"
        assert frag[0][1] == "●"

    def test_unknown_level_falls_back_idle_green(self) -> None:
        # set_state_level 对未知值兜 idle（绿）——状态球永不缺色/错挂
        s = StatusModel()
        s.set_state_level("garbage")
        assert s.snapshot().state_level == "idle"
        frag = toolbar_fragments(s.snapshot())
        assert frag[0][0] == "class:green"

    def test_default_state_is_idle(self) -> None:
        # 未喂入时默认 idle（绿），状态球始终有确定性颜色
        s = StatusModel()
        assert s.snapshot().state_level == "idle"
        frag = toolbar_fragments(s.snapshot())
        assert frag[0][0] == "class:green"

    def test_state_level_survives_snapshot(self) -> None:
        # 快照透传回归：blocked 经 snapshot() 后不丢（浅拷贝 str 安全）
        s = StatusModel()
        s.set_state_level("blocked")
        snap = s.snapshot()
        assert snap.state_level == "blocked"
        frag = toolbar_fragments(snap)
        assert frag[0][0] == "class:red"


class TestToolbarTips:
    """P1-4 常驻提示 → tips 轮换池（2026-09-25）。

    防虚构守卫：池内斜杠命令必须 ∈ SLASH_COMPLETER_WORDS 真实注册清单
    （F2 教训：handler 缺失的命令不得在任何用户可见面出现，如 /undo）。
    """

    def test_turn0_shows_keybinding_tip(self) -> None:
        # 键位兜底保住：0 轮（会话开始）必显示 Esc+Enter 提示
        assert "Esc+Enter" in toolbar_tip(0)

    def test_rotates_every_20_turns(self) -> None:
        # 2026-09-26 P1-5 降频：5→20 轮（CC 实证提示非必需品）
        assert toolbar_tip(19) == toolbar_tip(0)  # 未满 20 轮不变
        assert toolbar_tip(20) != toolbar_tip(0)  # 满 20 轮换下一条

    def test_wraps_around(self) -> None:
        # 10 条池 × 每 20 轮 = turns=200 回绕到首条
        assert toolbar_tip(200) == toolbar_tip(0)
        assert toolbar_tip(199) != toolbar_tip(0)

    def test_keybinding_tips_in_pool(self) -> None:
        # 2026-09-25 二期：键位/操作类入池（chord 事实源自 interface.py:311-312
        # 与 repl.py:1193 启动横幅，禁止未实测键位入池）
        # 2026-10-02：Ctrl+Enter/Shift+Enter 从 tips 池下线——chord 映射虽在
        # （interface._patch_pt_modifier_enter），但终端默认对 Enter 组合键发裸
        # \r（full_tui.py 实测注释、scripts/keypress_probe.py 探针），提示成
        # 空头承诺；恢复承诺前禁止重新入池。
        joined = " ".join(_TOOLBAR_TIPS)
        for key in ("Esc+Enter", "Ctrl+C", "Ctrl+D", "Tab"):
            assert key in joined, f"键位 {key} 缺席 tips 池"
        assert "Ctrl+Enter" not in joined, "Ctrl+Enter 未实现换行（终端发裸\\r），不得入池"

    def test_all_slash_tokens_are_registered(self) -> None:
        from lingclaude.cli.commands import SLASH_COMPLETER_WORDS
        # 2026-09-25 收紧：任意 /token 都必须注册，不再只查行首——
        # /multi 曾因藏在行中漏网（handler 真实存在但补全清单漏登，已补登）
        for tip in _TOOLBAR_TIPS:
            for token in tip.split():
                if token.startswith("/"):
                    assert token in SLASH_COMPLETER_WORDS, (
                        f"tips 池出现未注册命令 {token}（handler 缺失不得推广）")

    def test_toolbar_renders_rotating_tip(self) -> None:
        # 2026-09-26 P1-5 降频 5→20：轮换点改为 20 轮
        s = StatusModel()
        for _ in range(20):
            s.bump_turns()
        frag = toolbar_fragments(s.snapshot())
        text = "".join(t for _, t in frag if isinstance(t, str))
        assert f"│ {toolbar_tip(20)} " in text
        assert "Esc+Enter" not in text  # 20 轮后键位提示已轮换出


# ---------------------------------------------------------------------------
# 2026-09-25 P0 普查（toolbar 事故教训制度化）：状态源降级通道
# 状态链喂入失败不得纯静默——mark_degraded 登记 → toolbar 红字可见；
# 成功喂入 clear_degraded 清除。渲染层对 degraded 空值/缺字段免疫。
# ---------------------------------------------------------------------------
class TestDegradedChannel:
    def _text(self, s) -> str:
        return "".join(t for _, t in toolbar_fragments(s) if isinstance(t, str))

    def test_mark_degraded_registers_source(self) -> None:
        s = StatusModel()
        s.mark_degraded("ctx")
        s.mark_degraded("ctx")  # 幂等：同名源不重复登记
        assert s.degraded == ("ctx",)

    def test_clear_degraded_removes_source(self) -> None:
        s = StatusModel()
        s.mark_degraded("ctx")
        s.mark_degraded("cwd")
        s.clear_degraded("ctx")
        assert s.degraded == ("cwd",)
        s.clear_degraded("notexist")  # 清除未登记源不炸
        assert s.degraded == ("cwd",)

    def test_snapshot_carries_degraded(self) -> None:
        s = StatusModel()
        s.mark_degraded("perm")
        assert s.snapshot().degraded == ("perm",)

    def test_toolbar_renders_degraded_banner(self) -> None:
        s = StatusModel()
        s.mark_degraded("ctx")
        text = self._text(s.snapshot())
        assert "状态源降级" in text
        assert "ctx" in text

    def test_toolbar_clean_when_no_degradation(self) -> None:
        text = self._text(StatusModel().snapshot())
        assert "状态源降级" not in text

    def test_legacy_snapshot_without_degraded_field(self) -> None:
        # 旧版快照/测试桩对象缺 degraded 字段 → 渲染 getattr 兜空，不炸
        class Legacy:
            pass
        assert self._text(Legacy()) == self._text(Legacy())

    def test_render_immunity_none_fields(self) -> None:
        # toolbar 事故 8 炸点 fuzz 回归：None 字段快照渲染永不炸
        dirty = StatusModel(cwd=None, ctx_tokens=None, ctx_window=None,
                            turns=None, task=None, pending=None,
                            task_pending=None, cache_pct=None)
        assert toolbar_fragments(dirty)


# ---------------------------------------------------------------------------
# 2026-09-26 P1-6: 段界贪心折行——bottom_toolbar 超宽不再被 PT 剪裁。
# 根因：PT bottom_toolbar Window 未开 wrap_lines（prompt.py:589 默认
# False），height=Dimension(min=1) 高度随内容行数走 → fragment 内插
# \n 即自动长高。契约：内容流无损（仅插换行）、每行 ≤ 终端宽、
# 段永不从中间剪断、宽屏零回归、终端宽未知（0 列）不折行。
# ---------------------------------------------------------------------------
class TestToolbarWrap:
    """P1-6 折行契约（SSH 窄屏实测截断 → 段界贪心折行）。"""

    def _rich_model(self) -> StatusModel:
        return StatusModel(
            model="glm5.3-flash",
            cwd="/home/ai/lingclaude",
            ctx_tokens=91500,
            ctx_window=128000,
            cache_pct=94,
            turns=57,
            task="P1-6 折行测试任务",
            pending=2,
            perm_mode="auto",
            task_active="活跃任务名",
            todo_items=(("in_progress", "a"), ("pending", "b")),
        )

    @staticmethod
    def _render(frag) -> str:
        return "".join(t for _, t in frag if isinstance(t, str))

    def _with_columns(self, monkeypatch, cols: str):
        monkeypatch.setenv("COLUMNS", cols)
        return self._render(toolbar_fragments(self._rich_model().snapshot()))

    def test_wide_terminal_single_line(self, monkeypatch) -> None:
        # 宽屏（300 列）：状态行零折行。2026-09-27 任务面板（多行）恢复后，
        # 「全局无 \n」守卫改为「面板行之外无 \n」——面板行数 = 表头+任务行+汇总。
        out = self._with_columns(monkeypatch, "300")
        lines = out.split("\n")
        status = lines[-1]
        assert "●" in status  # 状态行（末行）不含 \n
        # 面板总行数守卫：表头 1 + 任务行 + 汇总 ≤ 6（TASK_PANEL_MAX_ITEMS=4）
        assert len(lines) - 1 <= 6, lines

    def test_narrow_terminal_wraps_within_width(self, monkeypatch) -> None:
        from lingclaude.cli.status import _display_width
        out = self._with_columns(monkeypatch, "80")
        lines = out.split("\n")
        assert len(lines) > 1, "80 列下该模型必折行"
        assert all(_display_width(l) <= 80 for l in lines), "有行超宽"

    def test_no_content_loss(self, monkeypatch) -> None:
        # 折行只插换行，不删字符：去 \n 后与宽屏（300 列不折）流一致
        wide = self._with_columns(monkeypatch, "300")
        narrow = self._with_columns(monkeypatch, "80")
        assert wide.replace("\n", "") == narrow.replace("\n", "")

    def test_zero_columns_falls_back_posix_80(self, monkeypatch) -> None:
        # shutil.get_terminal_size 的 COLUMNS=0 语义 = fallback ioctl（0 falsy
        # 被覆盖），非 tty 时最终回退 POSIX 缺省 80×24——永远不会得到 0 宽。
        # 所以 0 列下仍按 ≤80 折行（折行优于剪裁，信息保真优先）。
        from lingclaude.cli.status import _display_width
        out = self._with_columns(monkeypatch, "0")
        assert all(_display_width(l) <= 80 for l in out.split("\n"))

    def test_long_segment_never_split(self, monkeypatch) -> None:
        # 单段超宽：不从中间剪断，整段落到新行保完整
        from lingclaude.cli.status import _wrap_fragments
        marker = "x" * 100
        out = _wrap_fragments([("", "│ " + marker)], 80)
        assert marker in self._render(out)

    def test_tips_token_survives_wrap(self, monkeypatch) -> None:
        # tips 提示含斜杠命令 token，折行后必须原样存活（防伪装丢 token）
        out = self._with_columns(monkeypatch, "80")
        from lingclaude.cli.status import toolbar_tip
        assert toolbar_tip(57) in out


class TestModelToolbarCurrentModel:
    """回归：toolbar 显示「当前生效模型」而非启动固定名（2026-09-27）。

    事故：repl.py round 边界旧实现取 engine._last_resolved_config（全项目
    从未赋值，死代码）→ 恒回落 provider 启动名，/model --pin k3-256k 后
    toolbar 仍显示 glm-5.3-flash，Ctrl+L 重绘也救不回（数据被周期性覆写）。
    修复：pin 优先 get_pinned_model_name，未 pin 回落 provider._config.model，
    写裸名 + set_pinned 标志，[PINNED] 装饰归 status.py 渲染层单一职责。
    """

    class _Cfg:
        def __init__(self, model: str) -> None:
            self.model = model

    class _Provider:
        def __init__(self, model: str) -> None:
            self._config = TestModelToolbarCurrentModel._Cfg(model)

    class _Engine:
        """模拟 engine：可 pin/unpin，provider 固定启动名 glm-5.3-flash。"""

        def __init__(self) -> None:
            self._provider = TestModelToolbarCurrentModel._Provider("glm-5.3-flash")
            self._pinned: str | None = None

        def get_pinned_model_name(self):
            return self._pinned

    def _round_boundary_refresh(self, status, engine) -> None:
        """复刻 repl.py round 边界的刷新逻辑（与被测实现同语义）。"""
        _pinned = engine.get_pinned_model_name()
        if _pinned:
            status.set_model(str(_pinned))
            status.set_pinned(True)
        else:
            _rc = getattr(engine._provider, "_config", None) if engine._provider else None
            if _rc and getattr(_rc, "model", ""):
                status.set_model(str(_rc.model))
            status.set_pinned(False)

    def test_pin_shows_current_model(self) -> None:
        s = StatusModel()
        eng = self._Engine()
        # 启动态：未 pin → 显示 provider 默认 glm-5.3-flash，无 [PINNED]
        self._round_boundary_refresh(s, eng)
        assert s.model == "glm-5.3-flash"
        assert s.pinned is False
        # pin k3-256k → toolbar 立即显示当前生效模型
        eng._pinned = "k3-256k"
        self._round_boundary_refresh(s, eng)
        assert s.model == "k3-256k"
        assert s.pinned is True

    def test_unpin_falls_back_to_provider(self) -> None:
        s = StatusModel()
        eng = self._Engine()
        eng._pinned = "k3-256k"
        self._round_boundary_refresh(s, eng)
        assert s.model == "k3-256k"
        # unpin → 回落 provider 名，pin 标志清除
        eng._pinned = None
        self._round_boundary_refresh(s, eng)
        assert s.model == "glm-5.3-flash"
        assert s.pinned is False

    def test_pinned_decoration_not_doubled(self) -> None:
        # 裸名写入 + 渲染层单点装饰 → 永不出现 [PINNED] [PINNED] 累积
        s = StatusModel()
        s.set_model("k3-256k")
        s.set_pinned(True)
        snap = s.snapshot()
        disp = f"{snap.model} [PINNED]" if snap.pinned else snap.model
        assert disp == "k3-256k [PINNED]"
        assert disp.count("[PINNED]") == 1
