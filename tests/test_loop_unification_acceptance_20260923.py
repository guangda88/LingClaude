"""卸重收口验收（2026-09-23）：三运行时共享循环体 + 子代理接治理 seam。

验收标准（panel_20260923/SUMMARY.md:62 采信 codex 版）：
- fake seam 离线驱动（不发真实请求）——本文件全部离线；
- TUI / headless / 子代理三运行时共享实例：
  * TUI 与 headless 均经 QueryEngine → model_call → engine/loop/loop_body（既有事实，结构断言钉住）；
  * 子代理经本轮接线：SubAgent 任务级事件（subagent_start/subagent_result）入共享事件流，
    与主循环 journal 同轨。

契约（hooks.py 停层声明延续）：
- 无 hooks 的老构造点行为零变化；
- 治理旁路 best-effort，异常不反噬子代理主流程。
"""
from __future__ import annotations

import inspect
from typing import Any

from lingclaude.core.types import Result
from lingclaude.engine.loop.sub_agent import SubAgent, SubAgentConfig
from lingclaude.engine.subagent.base import SubagentContext, SubagentRequest
from lingclaude.engine.subagent.inprocess import InProcessSubagentBackend


# ── 离线基建（对齐 test_golden_loop_master 模式）───────────────────────────


def _mk_resp(content: str) -> Any:
    from lingclaude.core.model_types import ModelResponse, ModelUsage

    return ModelResponse(
        content=content,
        model="acceptance",
        usage=ModelUsage(input_tokens=1, output_tokens=1),
        tool_calls=(),
    )


class _ScriptedProvider:
    """按剧本依序吐 ModelResponse 的离线 provider（无 tool_calls 即一轮收敛）。"""

    def __init__(self, rounds: list[Any]) -> None:
        self._rounds = list(rounds)

    def complete(self, messages, config=None, tools=None, **kw) -> Result:
        return Result.ok(self._rounds.pop(0))


class _RecordingHooks:
    """记录 journal 事件序列的 fake hooks（观测面）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def journal_append(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.calls.append((event_type, data))


class _Registry:
    def get_all_definitions(self) -> tuple[dict[str, Any], ...]:
        return ()


class _RuntimeStub:
    """最小 runtime：registry 可答、session_id 明确、hooks 可选注入。"""

    def __init__(self, session_id: str = "sess-acc", hooks: Any = None) -> None:
        self.session_id = session_id
        self.registry = _Registry()
        if hooks is not None:
            self.hooks = hooks


# ── 子代理接缝：事件入共享事件流 ───────────────────────────────────────


class TestSubagentSeamWiring:
    def test_task_events_in_shared_stream(self) -> None:
        """子代理任务级事件（start/result）进入与主循环同轨的事件流。"""
        hooks = _RecordingHooks()
        agent = SubAgent(
            runtime=_RuntimeStub(session_id="sess-acc", hooks=hooks),
            provider=_ScriptedProvider([_mk_resp("done")]),
        )
        result = agent.run("检查代码")

        assert result.success is True
        types = [t for t, _ in hooks.calls]
        assert types[0] == "subagent_start"
        assert types[-1] == "subagent_result"
        assert hooks.calls[0][1]["task"] == "检查代码"
        assert hooks.calls[0][1]["session_id"] == "sess-acc"
        assert hooks.calls[-1][1]["success"] is True
        assert hooks.calls[-1][1]["rounds"] == 1

    def test_failure_result_also_recorded(self) -> None:
        """失败路径（provider 报错）同样有 result 事件收尾，事件流不悬空。"""
        hooks = _RecordingHooks()

        class _ErrProvider:
            def complete(self, messages, config=None, tools=None, **kw) -> Result:
                return Result.fail("模型不可达")

        agent = SubAgent(
            runtime=_RuntimeStub(hooks=hooks),
            provider=_ErrProvider(),
        )
        result = agent.run("会失败的任务")

        assert result.success is False
        types = [t for t, _ in hooks.calls]
        assert types[0] == "subagent_start"
        assert types[-1] == "subagent_result"
        assert hooks.calls[-1][1]["success"] is False
        assert hooks.calls[-1][1]["error"] == "模型不可达"

    def test_session_id_inherited_from_runtime(self) -> None:
        """session_id 缺省从 runtime 继承，显式传入可覆盖。"""
        hooks = _RecordingHooks()
        agent = SubAgent(
            runtime=_RuntimeStub(session_id="from-runtime", hooks=hooks),
            provider=_ScriptedProvider([_mk_resp("ok")]),
            session_id="explicit-sid",
        )
        agent.run("t")
        assert hooks.calls[0][1]["session_id"] == "explicit-sid"

        hooks2 = _RecordingHooks()
        agent2 = SubAgent(
            runtime=_RuntimeStub(session_id="from-runtime", hooks=hooks2),
            provider=_ScriptedProvider([_mk_resp("ok")]),
        )
        agent2.run("t")
        assert hooks2.calls[0][1]["session_id"] == "from-runtime"

    def test_no_hooks_zero_behavior_change(self) -> None:
        """老构造点（无 hooks）：行为零变化——结果正确、无事件副作用、不炸。"""
        runtime = _RuntimeStub(session_id="bare-sess")  # 无 hooks 属性
        agent = SubAgent(runtime=runtime, provider=_ScriptedProvider([_mk_resp("done")]))
        result = agent.run("老路径")
        assert result.success is True
        assert result.output == "done"
        assert self._no_hooks_touched(runtime)

    def _no_hooks_touched(self, runtime: Any) -> bool:
        return not hasattr(runtime, "hooks")

    def test_emit_best_effort_not_backfire(self) -> None:
        """治理旁路异常被吞：hooks 抛错不反噬子代理主流程。"""

        class _BoomHooks:
            def journal_append(self, *a: Any, **k: Any) -> None:
                raise RuntimeError("boom")

        agent = SubAgent(
            runtime=_RuntimeStub(hooks=_BoomHooks()),
            provider=_ScriptedProvider([_mk_resp("still done")]),
        )
        result = agent.run("带故障治理的任务")
        assert result.success is True
        assert result.output == "still done"


# ── 三运行时共享：TUI/headless（结构钉住）+ 子代理（行为实证）──────────


class TestThreeRuntimesSharedInstance:
    def test_tui_and_headless_share_loop_body(self) -> None:
        """TUI 与 headless 都经 QueryEngine → model_call → loop_body 单一实现。

        结构断言钉住 core→engine 唯一消费边（M3 行级豁免的回归哨兵）。
        """
        from lingclaude.core import model_call

        src = inspect.getsource(model_call)
        assert "loop.loop_body import run_call_model_loop" in src
        assert "loop.loop_body import run_stream_call_model_loop" in src

    def test_main_loop_events_via_hooks(self) -> None:
        """主循环体消费同一 hooks 面（与子代理事件词汇同轨）。"""
        from lingclaude.engine.loop.hooks import DefaultLoopHooks, LoopHooks

        assert hasattr(DefaultLoopHooks, "journal_append")
        assert hasattr(LoopHooks, "journal_append")
        sub = SubAgent.__dict__["_emit"]
        assert "journal_append" in inspect.getsource(sub)

    def test_subagent_backend_passes_hooks(self) -> None:
        """inprocess 后端构造 SubAgent 时接线 runtime.hooks（生产构造点）。"""
        hooks = _RecordingHooks()
        runtime = _RuntimeStub(session_id="sess-backend", hooks=hooks)
        backend = InProcessSubagentBackend()
        result = backend.run(
            SubagentRequest(task="后端派活"),
            SubagentContext(runtime=runtime, model_provider=_ScriptedProvider([_mk_resp("ok")])),
        )
        assert result.success is True
        types = [t for t, _ in hooks.calls]
        assert types[0] == "subagent_start"
        assert types[-1] == "subagent_result"

    def test_subagent_backend_parallel_wired(self) -> None:
        """并行模式（parallel=2）每个子任务都发事件对。"""
        hooks = _RecordingHooks()
        runtime = _RuntimeStub(session_id="sess-par", hooks=hooks)
        backend = InProcessSubagentBackend()
        result = backend.run(
            SubagentRequest(task="并行任务", parallel=2),
            SubagentContext(runtime=runtime, model_provider=_ScriptedProvider([_mk_resp("a"), _mk_resp("b")])),
        )
        assert result.success is True
        starts = [t for t, _ in hooks.calls if t == "subagent_start"]
        results = [t for t, _ in hooks.calls if t == "subagent_result"]
        assert len(starts) == 2
        assert len(results) == 2
