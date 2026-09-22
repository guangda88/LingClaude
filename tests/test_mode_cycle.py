"""Shift+Tab 四态模式环测试（2026-09-22）。

覆盖:
- 环序推进 auto → ask → strict → plan → 恢复 → auto（plan 叠加语义）
- headless（无 plan provider）plan 档静默空转
- current_display_mode plan 优先展示
- permissions._maybe_reload_mode 跨进程热更（外部改盘 → 进程捡起）
- toolbar ⏸ plan 段渲染（显示/隐藏）
- Shift+Tab 键位注册（full_tui / interface 源码断言）
- shift_mode 输出通道（session.append_output / stdout 降级）
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import lingclaude.cli.mode_cycle as mc
import lingclaude.core.permissions as perms
from lingclaude.cli.mode_cycle import current_display_mode, transition
from lingclaude.cli.status import StatusModel, toolbar_fragments


class FakePlanMode:
    """PlanMode 桩：is_active / enter / exit 同名同语义。"""

    def __init__(self) -> None:
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def enter(self) -> dict[str, str]:
        self._active = True
        return {"status": "entered"}

    def exit(self) -> dict[str, str]:
        self._active = False
        return {"status": "exited"}


def _install_fake_persist(monkeypatch, tmp_path: Path, mode: str) -> Path:
    """权限持久化重定向到 tmp（真实 approvals.json 零触碰）。

    baseline mtime 置 0 保证首次 get 必探测；_GLOBAL_MODE 一并 monkeypatch
    （用例结束自动还原，不污染其他测试）。
    """
    p = tmp_path / "approvals.json"
    p.write_text(json.dumps({"mode": mode, "always_allow": []}), encoding="utf-8")
    monkeypatch.setattr(perms, "_PERSIST_PATH", p)
    monkeypatch.setattr(perms, "_GLOBAL_MODE", mode)
    monkeypatch.setattr(perms, "_LAST_PERSIST_MTIME", 0.0)
    return p


class TestRingTransition:
    def test_full_cycle_with_plan_overlay(self, monkeypatch, tmp_path) -> None:
        """auto→ask→strict→plan→strict→auto 全环；plan 是叠加态不覆盖权限模式。"""
        _install_fake_persist(monkeypatch, tmp_path, "auto")
        monkeypatch.setattr(mc, "_prev_perm_mode", None)
        pm = FakePlanMode()
        mc.set_plan_runtime_provider(lambda: pm)
        try:
            assert transition() == ("auto", "ask")
            assert perms.get_permission_mode() == "ask"
            assert transition() == ("ask", "strict")
            assert perms.get_permission_mode() == "strict"
            # 进入 plan: 权限模式原样保留（叠加态核心语义）
            assert transition() == ("strict", "plan")
            assert pm.is_active
            assert perms.get_permission_mode() == "strict"
            # 退出 plan: 恢复记忆的权限模式，跳位闩置位
            assert transition() == ("plan", "strict")
            assert not pm.is_active
            assert perms.get_permission_mode() == "strict"
            # 环继续: 下一跳越过 plan 槽（跳位闩），strict → auto
            assert transition() == ("strict", "auto")
            assert perms.get_permission_mode() == "auto"
            # 闩已耗尽: 再次环行 strict → plan 正常进入
            assert transition() == ("auto", "ask")
            assert transition() == ("ask", "strict")
            assert transition() == ("strict", "plan")
            assert pm.is_active
        finally:
            mc.set_plan_runtime_provider(None)

    def test_plan_headless_noop(self, monkeypatch, tmp_path) -> None:
        """无 provider（headless）: plan 档静默空转，权限模式不变。"""
        _install_fake_persist(monkeypatch, tmp_path, "strict")
        monkeypatch.setattr(mc, "_prev_perm_mode", None)
        mc.set_plan_runtime_provider(None)
        assert transition() is None
        assert perms.get_permission_mode() == "strict"

    def test_display_mode_plan_priority(self, monkeypatch, tmp_path) -> None:
        """plan 活跃时 current_display_mode 返回 plan。"""
        _install_fake_persist(monkeypatch, tmp_path, "auto")
        monkeypatch.setattr(mc, "_prev_perm_mode", None)
        pm = FakePlanMode()
        mc.set_plan_runtime_provider(lambda: pm)
        try:
            assert current_display_mode() == "auto"
            pm.enter()
            assert current_display_mode() == "plan"
        finally:
            pm.exit()
            mc.set_plan_runtime_provider(None)

    def test_ring_position_tracks_real_state(self, monkeypatch, tmp_path) -> None:
        """环位由实际态推导: 外部热更权限模式后，环从新模式继续。"""
        _install_fake_persist(monkeypatch, tmp_path, "ask")
        monkeypatch.setattr(mc, "_prev_perm_mode", None)
        mc.set_plan_runtime_provider(None)
        # 当前 ask（fake persist 已设）→ 下一档 strict
        assert transition() == ("ask", "strict")


class TestHotReload:
    def test_external_change_picked_up(self, monkeypatch, tmp_path) -> None:
        """外部进程改盘上 mode → 本进程 get_permission_mode 自动捡起。"""
        p = _install_fake_persist(monkeypatch, tmp_path, "auto")
        assert perms.get_permission_mode() == "auto"
        # 模拟另一进程写入（os.utime 显式推进 mtime——tmpfs 同秒写入
        # st_mtime 粒度可能相同，探测会误判「无变化」）
        p.write_text(json.dumps({"mode": "strict", "always_allow": []}), encoding="utf-8")
        st = p.stat()
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        assert perms.get_permission_mode() == "strict"

    def test_invalid_value_ignored(self, monkeypatch, tmp_path) -> None:
        """盘上 mode 非法: 保留内存值（fail-closed 不提权）。"""
        p = _install_fake_persist(monkeypatch, tmp_path, "auto")
        assert perms.get_permission_mode() == "auto"
        p.write_text(json.dumps({"mode": "yolo", "always_allow": []}), encoding="utf-8")
        assert perms.get_permission_mode() == "auto"

    def test_set_mode_syncs_baseline(self, monkeypatch, tmp_path) -> None:
        """set_permission_mode 落盘后基线同步，own-write 不被当外部变更。"""
        _install_fake_persist(monkeypatch, tmp_path, "ask")
        assert perms.set_permission_mode("auto") is True
        assert perms.get_permission_mode() == "auto"

    def test_missing_file_silent(self, monkeypatch, tmp_path) -> None:
        """盘上文件缺失: stat 失败静默保留内存值。"""
        p = tmp_path / "nonexistent.json"
        monkeypatch.setattr(perms, "_PERSIST_PATH", p)
        monkeypatch.setattr(perms, "_GLOBAL_MODE", "ask")
        monkeypatch.setattr(perms, "_LAST_PERSIST_MTIME", 0.0)
        assert perms.get_permission_mode() == "ask"


class TestToolbarPlanSegment:
    def test_plan_segment_shown(self) -> None:
        s = StatusModel()
        s.set_perm_mode("auto")
        s.set_plan_active(True)
        frag = toolbar_fragments(s.snapshot())
        assert any("⏸ plan" in text for _, text in frag)

    def test_plan_segment_hidden(self) -> None:
        s = StatusModel()
        s.set_perm_mode("auto")
        s.set_plan_active(False)
        frag = toolbar_fragments(s.snapshot())
        assert not any("⏸ plan" in text for _, text in frag)

    def test_snapshot_passthrough(self) -> None:
        s = StatusModel()
        s.set_plan_active(True)
        assert s.snapshot().plan_active is True


class TestKeyBindings:
    def test_backtab_registered_full_tui(self) -> None:
        src = (
            Path(__file__).resolve().parent.parent
            / "lingclaude/cli/full_tui.py"
        ).read_text(encoding="utf-8")
        # Keys.BackTab 枚举注册（"backtab" 字符串别名在 PT 中非法，会炸构造）
        assert "add(Keys.BackTab)" in src
        assert "install_mode_toggler" in src

    def test_backtab_registered_interface(self) -> None:
        src = (
            Path(__file__).resolve().parent.parent
            / "lingclaude/cli/interface.py"
        ).read_text(encoding="utf-8")
        assert "add(Keys.BackTab)" in src
        assert "install_mode_toggler" in src


class TestShiftModeOutput:
    def test_output_via_session(self, monkeypatch, tmp_path) -> None:
        """全屏 TUI: 确认行经 session.append_output 进输出窗。"""
        _install_fake_persist(monkeypatch, tmp_path, "auto")
        monkeypatch.setattr(mc, "_prev_perm_mode", None)

        class Stub:
            def __init__(self) -> None:
                self.lines: list[str] = []

            def append_output(self, s: str) -> None:
                self.lines.append(s)

        stub = Stub()
        mc.shift_mode(stub)
        assert any("auto → ask" in line for line in stub.lines)

    def test_output_stdout_fallback(self, monkeypatch, tmp_path, capsys) -> None:
        """无 append_output 的会话: 降级 sys.stdout（P1/裸终端路径）。"""
        _install_fake_persist(monkeypatch, tmp_path, "ask")
        monkeypatch.setattr(mc, "_prev_perm_mode", None)
        mc.shift_mode(object())
        out = capsys.readouterr().out
        assert "ask → strict" in out


class TestProdPersistenceImmunity:
    """生产 approvals.json 对测试免疫哨兵（2026-09-23 conftest 隔离事故）。

    背景：test_gray_zone / test_t0_wiring 裸调 set_permission_mode 曾把
    生产 lingclaude/data/approvals.json 的 mode 覆写（auto/ask 随回归漂移），
    叠加跨进程热更表现为 TUI 权限模式自动跳变。conftest
    _isolate_persistence_files autouse fixture 修复后，本组用例守护：
    1) 生产文件 mtime/内容不被任何权限相关操作改动
    2) 模块级两写点确实指向 tmp（fixture 自身生效性）
    """

    def test_prod_approvals_untouched_by_mode_set(
        self, tmp_path
    ) -> None:
        """裸调 set_permission_mode 全程不触碰生产 approvals.json。"""
        import lingclaude.core.permissions as perms
        from lingclaude.core.approval_matrix import RULES_PATH

        prod_path = Path(__file__).resolve().parent.parent / (
            "lingclaude/data/approvals.json"
        )
        before = (
            prod_path.stat().st_mtime,
            prod_path.read_text(encoding="utf-8"),
        )

        perms.set_permission_mode("auto")
        perms.set_permission_mode("ask")
        perms.set_permission_mode("strict")
        assert perms.get_permission_mode() == "strict"

        # 断言隔离生效：两个模块级写点都已被 conftest 重定向，不在生产
        assert perms._PERSIST_PATH != prod_path
        assert RULES_PATH != prod_path

        after = (
            prod_path.stat().st_mtime,
            prod_path.read_text(encoding="utf-8"),
        )
        assert before == after, "生产 approvals.json 被测试改动！"

    def test_overlay_patch_coexists_with_conftest(self, monkeypatch, tmp_path) -> None:
        """测试自带再-patch（如 _install_fake_persist）与 conftest autouse
        隔离叠加共存：内层 patch 覆盖生效，两层恢复顺序为 LIFO——
        先撤测试内层，conftest 隔离态留存至本 fixture 结束，生产文件
        全程不被暴露。"""
        _install_fake_persist(monkeypatch, tmp_path, "auto")
        assert perms.get_permission_mode() == "auto"
        # 内层 patch 直接覆盖 conftest 的 tmp 路径——两层都是 tmp 域，
        # 无论恢复顺序如何，生产路径从未在场
        assert "pytest-of-" in str(perms._PERSIST_PATH) or "tmp" in str(
            perms._PERSIST_PATH
        ).lower()
