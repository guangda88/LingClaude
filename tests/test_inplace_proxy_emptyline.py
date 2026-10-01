"""端到端回归（2026-09-30 空行吞噬修复）。

事故复盘：_StdoutProxy._flush_line 的 `if line:` 把 "\n\n" 产生的空行整行
吞掉 → 窗内段落间距塌缩，且 _expected_window_lines 指纹（含空行）与窗内
实际行恒失配 → done 原位上色永远静默回退素字。既有测试夹具全部走
append_output 直喂（绕过 proxy 的行边界语义），一个用例都没拦住。

本文件强制走真实出口：_StdoutProxy 接管 sys.stdout + _handle_stream_event，
与用户实际 TUI 链路同构；测试环境旗标用后即复位，不污染同进程后续用例。
"""

from __future__ import annotations

import sys
from unittest.mock import patch

from lingclaude.cli import repl_io
from lingclaude.cli.full_tui import FullTuiSession, _StdoutProxy

CONTENT = (
    "结论先行：**粗体生效**。\n\n"
    "| 项目 | 状态 |\n|---|---|\n| 白名单 | 生效 |\n\n"
    "收尾。"
)


class TestProxyPathInPlaceStyling:
    def test_proxy_emptyline_preserved_and_replacement_fires(self, tmp_path) -> None:
        with patch("lingclaude.cli.full_tui._HAS_PROMPT_TOOLKIT", True):
            s = FullTuiSession(history_file=str(tmp_path / "h"))
            real = sys.stdout
            sys.stdout = _StdoutProxy(s, real)
            try:
                repl_io.set_full_tui_managed(True)
                repl_io.set_stream_bridged(True)
                s.set_streaming(True)
                # 碎片化 delta 必须严格拼接 == content（真实引擎保证），
                # 切点程序化生成防手滑（拼接不等是探针自身 bug，不是被测行为）
                cuts = [12, 31, 49]
                for a, b in zip([0, *cuts], [*cuts, len(CONTENT)]):
                    repl_io._handle_stream_event(
                        {"type": "text_delta", "text": CONTENT[a:b]}
                    )
                repl_io._handle_stream_event({"type": "done", "content": CONTENT})
            finally:
                sys.stdout = real
                repl_io.set_full_tui_managed(False)
                repl_io.set_stream_bridged(False)

            win = s._out_buffer.text.split("\n")
            # ① 空行保真："\n\n" 的段落空行必须占行（此前被 proxy 吞掉）
            assert "" in win[:-1]
            # ② done 原位替换成功：** 字面量消失、样式表非空
            assert not any("**粗体生效**" in ln for ln in win)
            assert s._style_map
            # ③ 内容一遍不重复（替换成功 = 无「底部追加第二遍」）
            assert sum(ln.count("收尾。") for ln in win) == 1

    def test_proxy_expected_fingerprint_includes_empty_lines(self, tmp_path) -> None:
        """指纹侧独立验证：空行必须出现在期望序列中（range(0,0,step) 零分块回归）。"""
        exp = repl_io._expected_window_lines("甲\n\n乙\n\n| a | b |\n|---|---|\n| c | d |\n\n丙")
        assert exp == [
            "甲",
            "",
            "乙",
            "",
            "| a   | b   |",  # 分隔行 --- 三字符参与列宽 → 列宽下限 3（既有行为）
            "| --- | --- |",
            "| c   | d   |",
            "",
            "丙",
        ]
