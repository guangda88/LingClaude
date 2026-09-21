"""P2-13（2026-09-21，全 15 家精读 §3.2 Pi chord 式 TUI 双代热更）回归测试。

验收（§3.2 #13 缺口：TUI 渲染层双代 cutover，真依赖第 0 步循环纯化）：
FullTuiSession.cutover_generation 蓝绿语义——新一代 candidate 先 build+verify
（不动现役 app），成功才 cutover（旧代优雅退役 exit + 新代接管），失败则
dispose candidate、旧代继续服务（零不可用窗口）。与 plugin_lifecycle.hot_swap
的蓝绿语义对齐，但作用于 TUI 渲染代次。

全离线（fake candidate，不真 run 事件循环，不抢终端）：
1. 非 Application 候选 → 拒绝（返回 False，旧代不动）
2. 候选构建异常 → fail-soft 拒绝（旧代不动）
3. 合法候选 + 显式 verify → cutover 成功，现役指向新代
4. 缺省 _verify_candidate 健全性门（layout 非空）通过
5. 无 layout 候选被缺省健全性门拒绝（旧代不动）
6. 切换时旧代 app.exit() 被调用（优雅退役 → 新代接管）
"""
from __future__ import annotations

import sys

import pytest

from lingclaude.cli.full_tui import FullTuiSession
from prompt_toolkit.application import Application as PTApp
from prompt_toolkit.layout import Layout
from prompt_toolkit.widgets import TextArea


class _Gen2App(PTApp):
    """最小可控 PT Application（不真 run，供双代 cutover 测试注入）。"""

    def __init__(self) -> None:
        super().__init__(layout=Layout(TextArea()), full_screen=False)
        self.exited = False

    def exit(self) -> None:
        self.exited = True

    def run(self) -> None:  # 测试不抢终端，事件循环 no-op
        pass


def _make_session() -> FullTuiSession:
    return FullTuiSession(history_file="/tmp/lcp_tui_p213_test_hist")


def test_cutover_rejects_non_application_candidate():
    """非 PT Application 候选 → 缺省健全性门拒绝，返回 False，旧代不动。"""
    sess = _make_session()
    old = _Gen2App()
    sess._app = old
    sess._app_thread = None

    def bad_build():
        class _NotApp:
            layout = None
        return _NotApp()

    assert sess.cutover_generation(build_new=bad_build) is False
    assert sess._app is old, "候选被拒后现役应保持旧代"


def test_cutover_rejects_builder_exception():
    """候选构建抛异常 → fail-soft 拒绝（不炸宿主），返回 False。"""
    sess = _make_session()
    old = _Gen2App()
    sess._app = old
    sess._app_thread = None

    def throwing_build():
        raise RuntimeError("候选构建炸了")

    assert sess.cutover_generation(build_new=throwing_build) is False
    assert sess._app is old


def test_cutover_success_with_explicit_verify():
    """合法候选 + 显式 verify 通过 → cutover 成功，现役指向新代。"""
    sess = _make_session()
    sess._app_thread = None
    gen2 = _Gen2App()
    assert sess.cutover_generation(
        build_new=lambda: gen2, verify=lambda c: None
    ) is True
    assert sess._app is gen2, "切换成功后现役应指向新代"


def test_cutover_default_verify_sanity_gate():
    """缺省 _verify_candidate 健全性门（layout 非空）通过。"""
    sess = _make_session()
    sess._app_thread = None
    gen3 = _Gen2App()
    assert sess.cutover_generation(build_new=lambda: gen3) is True
    assert sess._app is gen3


def test_cutover_default_verify_rejects_no_layout():
    """无 layout 候选被缺省健全性门拒绝（旧代不动）。"""
    sess = _make_session()
    sess._app_thread = None
    old = _Gen2App()
    sess._app = old

    def no_layout_app():
        a = _Gen2App()
        a.layout = None
        return a

    assert sess.cutover_generation(build_new=no_layout_app) is False
    assert sess._app is old, "无 layout 候选被拒后现役应保持旧代"


def test_cutover_gracefully_retires_old_generation():
    """切换时旧代 app.exit() 被调用（优雅退役）→ 新代接管。"""
    sess = _make_session()
    old = _Gen2App()
    sess._app = old
    sess._app_thread = None
    sess._running = False
    gen4 = _Gen2App()
    assert sess.cutover_generation(build_new=lambda: gen4) is True
    assert old.exited is True, "切换时旧代应被优雅退出（app.exit 被调）"
    assert sess._app is gen4, "新代应接管现役"


def test_cutover_custom_verify_rejection_keeps_old():
    """自定义 verify 拒绝 → 现役保持旧代（不被坏候选替换）。"""
    sess = _make_session()
    sess._app_thread = None
    old = _Gen2App()
    sess._app = old

    def reject(c):
        raise ValueError("verify 拒绝此候选")

    assert sess.cutover_generation(
        build_new=lambda: _Gen2App(), verify=reject
    ) is False
    assert sess._app is old, "verify 拒绝后现役应不变"
