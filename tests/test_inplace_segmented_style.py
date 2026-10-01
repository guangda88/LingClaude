"""分段原位上色回归（2026-10-01 FP_MISMATCH 事故）。

事故：带工具调用轮次的窗内区段 = 正文行与工具轨迹行（空行 +
"  [bash] …" + "✅ preview"）交错，done 整段指纹与 content 推导序列
必失配 → 几乎所有真实轮回退素字（10:02:47 日志：seg_len=62 vs exp_len=26，
seg_head 含 "  [bash] command=…" 与 "✅ {…}" 工具行）。

修复：tool_call_start/end 事件实写轨迹行快照（repl_io._turn_tool_trace，
按落窗实况分行记），done 分段走查——轨迹行原位保留，正文段与 rich 渲染
行指纹比对后替换。done.content 只含模型正文（10:02 生产日志 exp_head
可证），不含工具行。
"""

from __future__ import annotations

import sys

import pytest

import lingclaude.cli.full_tui as ft
from lingclaude.cli import repl_io
from lingclaude.cli.full_tui import FullTuiSession

# 与 10:02 事故同构：HEAD 正文 → 工具调用 → TAIL 正文 → done
TEXT_HEAD = "## 分段上色验收\n\n正文第一段，含 **粗体**：\n"
TEXT_TAIL = "\n工具跑完了，正文继续。\n"
# content = 模型正文串联（不含工具行——与生产 done.content 语义一致）
FULL = TEXT_HEAD + TEXT_TAIL

PREFIX_LINE = "  [bash] command=git status ..."
RESULT_LINE = "✅ On branch master"


def _drive_turn(s: FullTuiSession) -> None:
    repl_io.set_output_format("text")
    repl_io._turn_trace_reset()
    s.set_streaming(True)
    repl_io._handle_stream_event({"type": "text_delta", "text": TEXT_HEAD})
    repl_io._handle_stream_event(
        {"type": "tool_call_start", "name": "bash", "arguments": '{"command": "git status"}'}
    )
    repl_io._handle_stream_event(
        {"type": "tool_call_end", "is_error": False, "output_preview": "On branch master"}
    )
    repl_io._handle_stream_event({"type": "text_delta", "text": TEXT_TAIL})
    repl_io._handle_stream_event({"type": "done", "content": FULL})


class TestSegmentedInplaceStyle:
    def test_tool_turn_styled_with_trace_kept(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """带工具轮次：正文段上色、工具轨迹行原位保留且不被染色。"""
        monkeypatch.setattr(ft, "_HAS_PROMPT_TOOLKIT", True)
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        repl_io.set_full_tui_managed(True)
        proxy = ft._StdoutProxy(s, sys.stdout)
        monkeypatch.setattr(sys, "stdout", proxy)

        _drive_turn(s)

        lines = s._out_buffer.text.split("\n")
        joined = "\n".join(lines)
        assert not any("**" in ln for ln in lines), "粗体字面量未替换（仍素字）"
        assert RESULT_LINE in joined, "工具结果行丢失"
        assert PREFIX_LINE.rstrip() in [ln.rstrip() for ln in lines], "工具前缀行丢失"
        assert len(s._style_map) >= 1, "正文段样式表为空"
        # 轨迹行必须原样保留：未被样式化、未被改动
        for row, sp in s._style_map.items():
            assert lines[row].rstrip() not in (PREFIX_LINE, RESULT_LINE), (
                f"轨迹行被染色 row={row}: {lines[row]!r}"
            )

    def test_pure_text_turn_unaffected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """无工具轮次走整段旧语义：替换成功（既有行为零回归）。"""
        monkeypatch.setattr(ft, "_HAS_PROMPT_TOOLKIT", True)
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        repl_io.set_full_tui_managed(True)
        proxy = ft._StdoutProxy(s, sys.stdout)
        monkeypatch.setattr(sys, "stdout", proxy)

        content = "## 纯文本\n\n**加粗**\n"
        repl_io.set_output_format("text")
        repl_io._turn_trace_reset()
        s.set_streaming(True)
        repl_io._handle_stream_event({"type": "text_delta", "text": content})
        repl_io._handle_stream_event({"type": "done", "content": content})

        lines = s._out_buffer.text.split("\n")
        assert not any("**" in ln for ln in lines)
        assert len(s._style_map) >= 1

    def test_unexpected_line_falls_back_untouched(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """窗内出现既不在期望、也不在轨迹里的行 → False 且窗内容原样。

        注：轨迹是许可清单（窗内可出现并保留），不是必须消耗的义务——
        窗内无轨迹行而正文全吻合时替换合法（返回 True）。拒绝条件是
        未声明的行。
        """
        monkeypatch.setattr(ft, "_HAS_PROMPT_TOOLKIT", True)
        s = FullTuiSession(history_file=str(tmp_path / "h"))
        repl_io.set_full_tui_managed(True)
        proxy = ft._StdoutProxy(s, sys.stdout)
        monkeypatch.setattr(sys, "stdout", proxy)

        repl_io.set_output_format("text")
        repl_io._turn_trace_reset()
        s.set_streaming(True)
        repl_io._handle_stream_event(
            {"type": "text_delta", "text": "正文A\n意外行\n正文B\n"}
        )
        before = s._out_buffer.text
        s.set_streaming(False)
        ok = s.replace_turn_styled_segmented(
            ["x", "y"], [[], []], ["正文A", "正文B"], ["  [ghost] never-happened ✅"]
        )
        assert ok is False
        assert s._out_buffer.text == before, "失配时窗内容被改动（违规）"

    def test_seg_style_env_off_restores_legacy(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """LINGCLAUDE_SEG_STYLE=0：不用分段法（逃生口）。"""
        monkeypatch.setenv("LINGCLAUDE_SEG_STYLE", "0")
        import importlib

        importlib.reload(repl_io)
        assert repl_io._turn_tool_trace_on is False
        monkeypatch.delenv("LINGCLAUDE_SEG_STYLE")
        importlib.reload(repl_io)
        assert repl_io._turn_tool_trace_on is True
