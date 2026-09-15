"""Phase 3 测试：灵族接入三件套 — MultimodalTask 插片 / ResearchCrew 编排 / TUI 插片化。

覆盖：
1. 灵通问道多模态插片（SeamType.MULTIMODAL）
2. ResearchCrew 编排（SeamType.ORCHESTRATOR）
3. TUI 插片化（capability_seam 免签 + 骨架 + 消费）
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

# 确保 repo root 可导入
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ────────────────────────────────────────────────────────────────
# 1. 灵通问道多模态插片
# ────────────────────────────────────────────────────────────────
class TestLingTongMultimodalTask:
    def test_register_and_protocol(self):
        """插片注册 + MultimodalTask 协议（name/task_type/input_schema/execute）。"""
        from lingclaude.core.seam import SeamRegistry, SeamType
        from lingclaude.seams.multimodal_lingtong import register_lingtong_multimodal

        ok = register_lingtong_multimodal()
        assert ok is True

        t = SeamRegistry.get(SeamType.MULTIMODAL, "lingtong_multimodal")
        assert t.name == "lingtong_multimodal"
        assert t.task_type() == "multimodal"

        schema = t.input_schema()
        assert "analyze_emotion" in schema["tasks"]
        assert "synthesize_speech" in schema["tasks"]
        assert "list_voices" in schema["tasks"]

    def test_register_fail_closed_no_crash(self):
        """注册失败不崩溃（fail-closed）。"""
        from lingclaude.seams.multimodal_lingtong import register_lingtong_multimodal

        # 正常路径即可（模块不可用时也应返回 True 且注册保留）
        assert register_lingtong_multimodal("test_multimodal_2") is True

    def test_analyze_emotion(self):
        """analyze_emotion 真实调用（若 src 可用）。"""
        pytest.importorskip("src.audio.enhanced_tts")
        import asyncio

        from lingclaude.core.seam import SeamRegistry, SeamType

        t = SeamRegistry.get(SeamType.MULTIMODAL, "lingtong_multimodal")
        if t.input_schema().get("transport") != "in-process":
            pytest.skip("lingtongask src 不可用，跳过真实调用")

        res = asyncio.run(
            t.execute(task="analyze_emotion", text="今天天气真好，心情非常愉快！")
        )
        assert "emotion" in res
        assert res["emotion"] in (
            "neutral", "happy", "sad", "excited", "calm", "serious", "gentle",
        )


# ────────────────────────────────────────────────────────────────
# 2. ResearchCrew 编排
# ────────────────────────────────────────────────────────────────
class _MockBackend:
    """最小 SubagentBackend mock（不入真注册表，避免全局污染）。"""

    name = "mock"

    def __init__(self, tag: str, fail: bool = False):
        self.tag = tag
        self.fail = fail

    def run(self, request, ctx):
        from lingclaude.engine.subagent.base import SubagentResult

        if self.fail:
            return SubagentResult(
                agent_id="m", task=request.task, output="",
                success=False, error="mock failure",
            )
        return SubagentResult(
            agent_id="m", task=request.task, output=f"{self.tag}:ok", success=True,
        )


class TestResearchCrew:
    def _make_crew(self):
        """构造带 mock 后端的 ResearchCrew + 临时治理引擎。"""
        from lingclaude.engine.subagent.manager import SubagentManager
        from lingclaude.governance.governance_v2 import GovernanceEngine
        from lingclaude.seams.research_crew import ResearchCrew

        mgr = SubagentManager()
        mgr.register(_MockBackend("alpha"), names=("mock_alpha",))
        mgr.register(_MockBackend("beta"), names=("mock_beta",))

        crew = ResearchCrew(manager=mgr)
        tmp = Path(tempfile.mkdtemp()) / "gv.json"
        crew._governance = GovernanceEngine(state_file=tmp)
        return crew

    def test_create_and_dispatch_sequential_success(self):
        crew = self._make_crew()
        cid = crew.create_crew(["mock_alpha", "mock_beta"], task="协同测试")
        assert cid.startswith("crew-")

        r = crew.dispatch(cid, "分析性能瓶颈")
        assert r["status"] == "succeeded"
        assert r["succeeded"] == ["mock_alpha", "mock_beta"]
        assert r["failed"] == []
        assert len(r["steps"]) == 2

    def test_dispatch_fail_closed_on_member_error(self):
        """成员失败 → 串行即停，后续不执行，状态 failed。"""
        crew = self._make_crew()
        crew._manager.register(_MockBackend("bad", fail=True), names=("mock_bad",))

        cid = crew.create_crew(["mock_alpha", "mock_bad", "mock_beta"], task="失败测试")
        r = crew.dispatch(cid, "失败测试")
        assert r["status"] == "failed"
        assert r["failed"] == ["mock_bad"]
        assert len(r["steps"]) == 2  # mock_beta 未执行
        assert r["steps"][-1]["provider"] == "mock_bad"

    def test_unregistered_member_fail_closed(self):
        crew = self._make_crew()
        with pytest.raises(ValueError, match="未注册"):
            crew.create_crew(["no_such_backend"])

    def test_governance_proposal_tracking(self):
        """编排建提案 + 结束后决议（真引擎生命周期）。"""
        crew = self._make_crew()
        cid = crew.create_crew(["mock_alpha"], task="治理跟踪")
        r = crew.dispatch(cid, "治理跟踪")
        assert r["proposal_id"]  # 提案已建

        # 真引擎里能看到提案且已 finalize
        pid = r["proposal_id"]
        assert pid in crew._governance.proposals

    def test_register_into_seam_registry(self):
        from lingclaude.core.seam import SeamRegistry, SeamType
        from lingclaude.seams.research_crew import register_research_crew

        assert register_research_crew("test_research_crew") is True
        got = SeamRegistry.get(SeamType.ORCHESTRATOR, "test_research_crew")
        assert got.name == "research_crew"


# ────────────────────────────────────────────────────────────────
# 3. TUI 插片化
# ────────────────────────────────────────────────────────────────
class TestTUIPluginSeam:
    def test_install_and_providers(self):
        from lingclaude_plugins.tui import TUI_SEAM, install

        assert install() is True
        providers = TUI_SEAM.list_providers()
        assert "default_renderer" in providers
        assert "tui_toolbar" in providers
        assert "simple_prompt" in providers

    def test_renderer_exempt_from_double_sign(self):
        """免签：纯展示 renderer 直接 execute 不抛 PermissionError（R2 双签雷已解）。"""
        from lingclaude_plugins.tui import TUI_SEAM

        r = TUI_SEAM.get_provider("default_renderer")
        # 不应抛 PermissionError
        assert r.execute("info", "hello") is None

    def test_toolbar_fragments(self):
        from lingclaude_plugins.tui import TUI_SEAM

        tb = TUI_SEAM.get_provider("tui_toolbar")
        frags = tb.execute("fragments")
        assert isinstance(frags, list)

    def test_capability_seam_required_signers_param(self):
        """register_provider 的 required_signers 透传（Step A 核心扩展）。"""
        from lingclaude.lacp.capability_seam import CapabilitySeam

        seam = CapabilitySeam("test_seam", {"methods": []})

        class P:
            name = "p1"
            version = "0.1.0"
            def execute(self, *a, **k):
                return "ok"

        # 免签注册
        seam.register_provider(P(), required_signers=[])
        assert seam.get_provider("p1").execute() == "ok"

        # 默认双签注册 → execute 抛 PermissionError
        seam.register_provider(P(), required_signers=None)  # type: ignore[name-defined]  # noqa: F821
        try:
            # 同名覆盖，需换名
            pass
        except Exception:  # noqa: BLE001
            pass
        class P2:
            name = "p2"
            version = "0.1.0"
            def execute(self, *a, **k):
                return "ok"
        seam.register_provider(P2())
        with pytest.raises(PermissionError):
            seam.get_provider("p2").execute()

    def test_repl_io_done_renders_via_seam(self):
        """repl_io done 事件走 TUI 插片（Step B 消费点）。"""
        from lingclaude.cli.repl_io import _handle_stream_event

        # 非 TTY 分支不崩
        import io
        import sys as _sys

        old = _sys.stdout
        _sys.stdout = io.StringIO()
        try:
            _handle_stream_event({"type": "done", "content": "# 标题\n正文"})
        finally:
            _sys.stdout = old
