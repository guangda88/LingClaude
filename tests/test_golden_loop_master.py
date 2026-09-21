"""P0-A 阶段 0：循环体 golden-master 行为基线（全离线，不依赖 stub 逐字段比对）。

裁判机制（docs/CORE_SURFACE_CONTRACT.md §五）：
- 借真身驱动：用真实 QueryEngine（真实 _finalize_turn/_log_model_request/打转检测/
  journal/checkpoint）+ 定点 stub（provider/配置解析/磁盘落点），保证被测循环体
  与生产是同一段代码。
- 快照对象 = 可观测行为面：journal 事件、stream 事件序列、返回文本、hooks 调用
  序列、provider 请求消息序列、会话镜像、usage。
- 用法：P0-A 迁移每一步之后跑本文件——与基线不一致 = 行为漂移，先查原因再动代码。
  反向纪律：任何想让测试变绿的改动先问「快照是否本来就该变」，该变就显式更新
  并注明原因（对应契约 §五.3）。

剧本（场景 × 双路径，行为真相来自 2026-09-21 探针实测）：
  A. 纯文本单轮     —— 1 个请求轮，无工具调用
  B. 工具+文本多轮   —— R1 工具轮(read) → R2 文本轮收尾
  C1. 连续模型失败熔断 —— 非 stream：hard_interrupt 收口；stream：yield error 即返
  C2. 同参打转熔断     —— stream 三轮同参工具调用 → loop-abort（finalized=False）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lingclaude.engine.loop.hooks import DefaultLoopHooks  # noqa: F401 — 契约参照物
from lingclaude.core.model_call import _ToolLoopDetector  # noqa: F401
from lingclaude.core.model_call import AGENT_MAX_TOOL_ROUNDS
from lingclaude.core.model_types import (
    MessageRole,
    ModelConfig,
    ModelMessage,
    ModelResponse,
    ModelUsage,
    ToolCall,
)
from lingclaude.core.types import Result
from lingclaude.core.query_engine import QueryEngine, QueryEngineConfig

from tests.test_loop_seam import _SpyHooks  # 复用第 0 步已验证的 spy

_SENTINEL_CFG = ModelConfig(model="golden-master", api_key="test")


def _mk_resp(content: str, tool_calls: tuple[ToolCall, ...] = ()) -> ModelResponse:
    return ModelResponse(
        content=content,
        model="golden-master",
        usage=ModelUsage(input_tokens=10, output_tokens=5),
        tool_calls=tool_calls,
    )


class _FakeProvider:
    """按剧本依序吐 ModelResponse / stream 事件的离线 provider。

    - complete(): 第 idx 次调用返回 rounds[idx]（Result.ok 包装）；idx∈fail_rounds → 失败
    - stream_complete(): 第 idx 次调用生成 stream_rounds[idx] 全部事件，末尾补 finish
    - requests: 记录 (messages, config, tools)，供快照 provider 面真相
    """

    def __init__(
        self,
        rounds: list[ModelResponse] | None = None,
        stream_rounds: list[list[dict[str, Any]]] | None = None,
        fail_rounds: set[int] | None = None,
        stream_fail_rounds: set[int] | None = None,
    ) -> None:
        self._rounds = rounds or []
        self._stream_rounds = stream_rounds or []
        self._fail_rounds = fail_rounds or set()
        self._stream_fail_rounds = stream_fail_rounds or set()
        self.requests: list[tuple[tuple, Any, Any]] = []

    def complete(self, messages, config=None, tools=None, **kw) -> Result:
        idx = len(self.requests)
        self.requests.append((messages, config, tools))
        if idx in self._fail_rounds:
            return Result.fail("boom-provider-error")
        return Result.ok(self._rounds[idx])

    def stream_complete(self, messages, config=None, tools=None, **kw):
        idx = len(self.requests)
        self.requests.append((messages, config, tools))
        if idx in self._stream_fail_rounds:
            yield {"type": "error", "error": "boom-stream-error"}
            return
        for ev in self._stream_rounds[idx]:
            yield ev
        yield {
            "type": "finish",
            "usage": ModelUsage(input_tokens=10, output_tokens=5),
        }


class _ScriptedHooks(_SpyHooks):
    """spy 记录 + 真实转发：治理副作用既可观测（spy 面），也走生产路径
    （DefaultLoopHooks → 真实 journal/flywheel/task_router）。行为零变化原则下，
    golden-master 必须同时钉住这两层。"""

    def __init__(self) -> None:
        super().__init__()
        self._default: Any = None  # bind() 后为 DefaultLoopHooks(engine)

    def bind(self, engine: Any) -> None:
        from lingclaude.engine.loop.hooks import DefaultLoopHooks as _DLH
        self._default = _DLH(engine)

    def journal_append(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.journal_calls.append((event_type, data))
        if self._default is not None:
            self._default.journal_append(event_type, data)

    def record_provider_outcome(self, cfg: Any, kind: str, error: str | None = None) -> str | None:
        self.provider_calls.append((cfg, kind, error))
        if self._default is not None:
            return self._default.record_provider_outcome(cfg, kind, error)
        return "spy_provider"

    def switch_target_health(self, provider_name: str) -> tuple[bool, str]:
        self.switch_health.append(provider_name)
        if self._default is not None:
            return self._default.switch_target_health(provider_name)
        return (True, "spy：门禁未启用")

    def log_flywheel(self, **kwargs: Any) -> None:
        self.flywheel_calls.append(kwargs)
        if self._default is not None:
            self._default.log_flywheel(**kwargs)

    def should_hallucination_correct(self, prompt: str, used_tools: bool, messages: list) -> bool:
        return False  # 固定关闭修正分支，快照聚焦循环体本体

    def hallucination_correction(self, messages, content, tools, config) -> str:
        return content


def _make_engine(tmp_path: Path, provider: _FakeProvider, hooks: Any) -> QueryEngine:
    """真实引擎 + 定点 stub：只隔离磁盘落点、模型配置解析、易变注入源。"""
    engine = QueryEngine(QueryEngineConfig())
    engine.config = QueryEngineConfig(max_turns=3)  # 缩短轮次上限，剧本可控
    engine._provider = provider
    hooks.bind(engine)  # spy 记录 + DefaultLoopHooks 真实转发
    engine._loop_hooks = hooks
    # 边界 stub：模型配置解析（真实实现读 config.yaml / 路由器，测试外置）
    engine._resolve_model_config = lambda prompt: (_SENTINEL_CFG, None)
    # 边界 stub：flywheel 落盘 → 内存记录（保留调用面真相当快照项）
    engine._flywheel_log: list[tuple[str, str]] = []
    engine._log_to_flywheel = (
        lambda event, detail, **kw: engine._flywheel_log.append((event, detail))
    )
    # 磁盘隔离：journal / 会话历史 / checkpoint 全部落进临时目录
    engine._journal_dir = tmp_path / "journal"
    engine._session_history_path = tmp_path / "session_history.json"
    # 易变源隔离：避免环境噪声进快照（system prompt 由真实 builder 生成）
    engine._dementia_detector = None
    engine._dementia_state = None
    engine._dementia_stats = None
    if hasattr(engine, "_dementia_injection"):
        engine._dementia_injection = ""
    if hasattr(engine, "_dementia_extra"):
        engine._dementia_extra = ""
    if hasattr(engine, "_project_index"):
        engine._project_index = {"files": [], "symbols": []}
    engine._session_cache_hits = 0
    engine._model_switch_note = None
    return engine


def _journal_lines(journal_dir: Path) -> list[str]:
    # SessionJournal 落盘文件为 <journal_dir>/<session_id>.jsonl（session_id 为 uuid）
    lines: list[str] = []
    if not journal_dir.exists():
        return lines
    for f in sorted(journal_dir.glob("*.jsonl")):
        lines.extend(ln for ln in f.read_text(encoding="utf-8").splitlines() if ln)
    return lines


def _normalize_journal(line: str) -> str:
    d = json.loads(line)
    d.pop("timestamp", None)  # 唯一非确定字段
    d.pop("session_id", None)  # uuid 每次构造都变
    return json.dumps(d, ensure_ascii=False, sort_keys=True)


def _snapshot(
    engine: QueryEngine,
    provider: _FakeProvider,
    hooks: _ScriptedHooks,
    journal_dir: Path,
) -> dict[str, Any]:
    """可观测行为面快照：journal / hooks 序列 / provider 面真相 / 会话镜像。"""
    msgs = []
    for messages, _cfg, _tools in provider.requests:
        msgs.append([
            {"role": str(getattr(m, "role", "")).split(".")[-1],
             "content": str(getattr(m, "content", ""))}
            for m in messages
        ])
    return {
        "journal": [_normalize_journal(ln) for ln in _journal_lines(journal_dir)],
        "journal_types": [json.loads(ln)["type"] for ln in _journal_lines(journal_dir)],
        "hooks_journal": json.dumps(hooks.journal_calls, ensure_ascii=False, sort_keys=True),
        "hooks_provider": json.dumps(hooks.provider_calls, ensure_ascii=False, sort_keys=True, default=str),
        "provider_msgs": json.dumps(msgs, ensure_ascii=False, sort_keys=True),
        "conversation": json.dumps(list(engine._conversation), ensure_ascii=False),
        "messages": json.dumps(list(engine._messages), ensure_ascii=False),
        "usage": engine._usage.to_dict(),
        "flywheel": list(engine._flywheel_log),
        "last_turn_input": getattr(engine, "_last_turn_input", None),
        "behavior_tool_calls": engine._behavior.tool_call_count,
    }


def _types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


# ---------------------------------------------------------------------------
# 场景 A：纯文本单轮（双路径）
# ---------------------------------------------------------------------------

def test_golden_a_nonstream_single_text_round(tmp_path) -> None:
    provider = _FakeProvider(rounds=[_mk_resp("你好，世界")])
    hooks = _ScriptedHooks()
    engine = _make_engine(tmp_path, provider, hooks)

    out = engine._call_model("场景A提问")

    snap = _snapshot(engine, provider, hooks, engine._journal_dir)
    # 返回文本 = 模型正文经 _finalize_turn 收口（脱敏/校验）后的最终内容
    conv = json.loads(snap["conversation"])
    assert out == conv[-1][1] and conv[-1][0] == "assistant"
    assert out in snap["messages"]
    # 单轮：provider 只被请求一次
    assert len(provider.requests) == 1
    # hooks 面真相：provider success ×1（非 stream 路径无 turn_end journal 事件）
    assert snap["hooks_provider"].count('"success"') == 1
    # usage 真实累计（_accumulate_usage）
    assert snap["usage"] == {"input_tokens": 10, "output_tokens": 5, "cached_tokens": 0}
    # 纯文本成功路径无 flywheel 事件
    assert snap["flywheel"] == []


def test_golden_a_stream_single_text_round(tmp_path) -> None:
    # 一个请求轮 = stream_rounds 的一个元素；轮内多个 delta 依序转发
    provider = _FakeProvider(stream_rounds=[
        [{"type": "text_delta", "text": "你好，"},
         {"type": "text_delta", "text": "世界"}],
    ])
    hooks = _ScriptedHooks()
    engine = _make_engine(tmp_path, provider, hooks)

    events = list(engine.stream_call_model("场景A提问"))

    snap = _snapshot(engine, provider, hooks, engine._journal_dir)
    # 事件流形状：两个 text_delta → done（无工具轮，无 round_end）
    assert _types(events) == ["text_delta", "text_delta", "done"]
    done = events[-1]
    assert done["content"] == "你好，世界"
    assert done["usage"] == {"input_tokens": 10, "output_tokens": 5, "cached_tokens": 0}
    assert done["finalized"] is True
    # 会话镜像已写（finalized=True 契约，H20 统一点）
    assert json.loads(snap["conversation"])[-1] == ["assistant", "你好，世界"]
    # journal turn_end 来自真实 SessionJournal（stream 路径专属语义）
    assert "turn_end" in snap["journal_types"]
    assert snap["hooks_provider"].count('"success"') == 1
    assert snap["flywheel"] == []


# ---------------------------------------------------------------------------
# 场景 B：工具轮 + 文本轮收尾（双路径）
# ---------------------------------------------------------------------------

def _tool_call() -> ToolCall:
    return ToolCall(id="call_1", name="read", arguments='{"path": "/tmp/gm.txt"}')


def test_golden_b_nonstream_tool_then_text(tmp_path) -> None:
    provider = _FakeProvider(rounds=[
        _mk_resp("先读文件", (_tool_call(),)),   # R1 工具轮
        _mk_resp("文件内容是 hello"),             # R2 文本轮收尾
    ])
    hooks = _ScriptedHooks()
    engine = _make_engine(tmp_path, provider, hooks)
    # stub 工具执行器（隔离真实工具系统），行为与生产契约逐字对齐：
    # ToolCallExecutor.process = append ASSISTANT(content+tool_calls) + 逐个 TOOL 结果
    def _process(tcs, messages, content=""):
        messages.append(ModelMessage(role=MessageRole.ASSISTANT, content=content, tool_calls=tcs))
        for tc in tcs:
            messages.append(ModelMessage(
                role=MessageRole.TOOL, content="hello", name=tc.name, tool_call_id=tc.id,
            ))
    engine._tool_call_executor = type("_TCE", (), {"process": staticmethod(_process)})()

    out = engine._call_model("场景B提问")

    snap = _snapshot(engine, provider, hooks, engine._journal_dir)
    conv = json.loads(snap["conversation"])
    assert out == conv[-1][1] == "文件内容是 hello"
    # 两轮请求；R2 请求里回灌了 R1 的工具结果（role=tool）
    assert len(provider.requests) == 2
    r2_msgs = json.loads(snap["provider_msgs"])[1]
    assert any(m["role"].upper() == "TOOL" for m in r2_msgs)
    # journal 有 tool_call 事件（经 hooks）
    assert any(ev[0] == "tool_call" for ev in hooks.journal_calls)
    # usage 两轮累计
    assert snap["usage"] == {"input_tokens": 20, "output_tokens": 10, "cached_tokens": 0}


def test_golden_b_stream_tool_then_text(tmp_path) -> None:
    provider = _FakeProvider(stream_rounds=[
        [
            {"type": "text_delta", "text": "先读文件"},
            {"type": "tool_call_complete", "id": "call_1", "name": "read",
             "arguments": '{"path": "/tmp/gm.txt"}'},
        ],
        [{"type": "text_delta", "text": "文件内容是 hello"}],
    ])
    hooks = _ScriptedHooks()
    engine = _make_engine(tmp_path, provider, hooks)
    engine._execute_tool_with_retry = lambda name, args: "hello"

    events = list(engine.stream_call_model("场景B提问"))

    snap = _snapshot(engine, provider, hooks, engine._journal_dir)
    # 事件流形状（探针实测基线）：R1(text+tool 对) → round_end → R2(text) → done
    # 注意：纯文本收尾轮【没有】round_end —— round_end 只在带工具的轮尾发
    assert _types(events) == [
        "text_delta", "tool_call_start", "tool_call_end", "round_end",
        "text_delta", "done",
    ]
    assert events[3] == {"type": "round_end", "round_idx": 0,
                         "has_tool_calls": True, "error_count": 0}
    # tool_call_end 事件带输出预览与错误标记
    assert events[2]["output_preview"] == "hello" and events[2]["is_error"] is False
    # R1 工具结果回灌 provider（R2 请求含 tool 消息）
    r2_msgs = json.loads(snap["provider_msgs"])[1]
    assert any(m["role"].upper() == "TOOL" for m in r2_msgs)
    # done 契约：两轮 usage 累计 + finalized=True
    assert events[-1]["content"] == "文件内容是 hello"
    assert events[-1]["usage"] == {"input_tokens": 20, "output_tokens": 10, "cached_tokens": 0}
    assert events[-1]["finalized"] is True
    # journal：tool_call / tool_result / checkpoint / turn_end 齐全（stream 路径）
    kinds = {ev[0] for ev in hooks.journal_calls}
    assert {"tool_call", "tool_result", "checkpoint", "turn_end"} <= kinds
    # 会话镜像
    assert json.loads(snap["conversation"])[-1] == ["assistant", "文件内容是 hello"]


# ---------------------------------------------------------------------------
# 场景 C1：连续模型失败（双路径语义分叉——这是基线要钉死的事实）
# ---------------------------------------------------------------------------

def test_golden_c1_nonstream_provider_fail_hard_interrupt(tmp_path) -> None:
    provider = _FakeProvider(rounds=[_mk_resp("x")] * 5, fail_rounds={0, 1, 2})
    hooks = _ScriptedHooks()
    engine = _make_engine(tmp_path, provider, hooks)

    out = engine._call_model("场景C1提问")

    snap = _snapshot(engine, provider, hooks, engine._journal_dir)
    # 非 stream：连续失败达 limit → hard_interrupt 文本直接返回（不经 finalize）
    assert out.startswith("[硬中断]") and "模型调用失败" in out
    # 三次错误都被 hooks 记录（record_provider_outcome error ×3）
    assert snap["hooks_provider"].count('"error"') == 3
    # hard_interrupt 记入 flywheel（model_call scope 有 flywheel，与 stream 不同）
    assert snap["flywheel"] == [("hard_interrupt", "连续模型调用失败 3 次")]
    # 会话镜像不写（未走 finalize）——熔断轮的消息不进 conversation
    assert json.loads(snap["conversation"]) == []


def test_golden_c1_stream_provider_fail_yields_error_and_returns(tmp_path) -> None:
    # 基线事实（探针实测）：stream provider 失败 → yield error → 立即 return，
    # 【不累计熔断】——stream 熔断只发生在工具全错轮（tool_loop_stream scope）。
    provider = _FakeProvider(
        stream_rounds=[[{"type": "text_delta", "text": "x"}]],
        stream_fail_rounds={0},
    )
    hooks = _ScriptedHooks()
    engine = _make_engine(tmp_path, provider, hooks)

    events = list(engine.stream_call_model("场景C1提问"))

    snap = _snapshot(engine, provider, hooks, engine._journal_dir)
    assert _types(events) == ["error"]
    assert events[0]["error"] == "boom-stream-error"
    # 失败记入 provider outcome（hooks 面），但不产生 hard_interrupt flywheel
    assert snap["hooks_provider"].count('"error"') == 1
    assert snap["flywheel"] == []
    # 会话镜像不写
    assert json.loads(snap["conversation"]) == []


# ---------------------------------------------------------------------------
# 场景 C2：同参工具轮打转 → loop-abort（stream 专属基线）
# ---------------------------------------------------------------------------

def test_golden_c2_stream_identical_tool_rounds_loop_abort(tmp_path) -> None:
    tc_ev = {"type": "tool_call_complete", "id": "call_9", "name": "read",
             "arguments": '{"path": "/tmp/same.txt"}'}
    provider = _FakeProvider(stream_rounds=[
        [{"type": "text_delta", "text": "r1"}, tc_ev],
        [{"type": "text_delta", "text": "r2"}, tc_ev],   # 第 2 次重复 → warn
        [{"type": "text_delta", "text": "r3"}, tc_ev],   # 第 3 次 → abort
    ])
    hooks = _ScriptedHooks()
    engine = _make_engine(tmp_path, provider, hooks)
    engine._execute_tool_with_retry = lambda name, args: "hello"

    events = list(engine.stream_call_model("场景C2提问"))

    snap = _snapshot(engine, provider, hooks, engine._journal_dir)
    # R1/R2 完整轮 + R3 在 round_end 前被打转熔断截断
    assert _types(events) == [
        "text_delta", "tool_call_start", "tool_call_end", "round_end",
        "text_delta", "tool_call_start", "tool_call_end", "round_end",
        "text_delta", "tool_call_start", "tool_call_end",
        "text_delta", "done",
    ]
    done = events[-1]
    # done 文本 = 已完成轮文本累积 + 熔断告知（R3 文本不计入）
    assert done["content"].startswith("r1\nr2")
    assert "循环检测" in done["content"] and "熔断" in done["content"]
    # 关键契约（基线实测）：loop-abort 轮 finalized=False（CLI 层据此走兜底）；
    # done 事件仍携带循环内累计 usage（3 轮 × 10/5），但引擎级 _usage 不汇总
    # （跳过 _finalize_turn）——两个口径在此钉死，防迁移期混淆。
    assert done["finalized"] is False
    assert done["usage"] == {"input_tokens": 30, "output_tokens": 15, "cached_tokens": 0}
    assert snap["usage"] == {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
    # 会话镜像不写（与 finalized=False 契约一致）
    assert json.loads(snap["conversation"]) == []
    # 无 turn_end journal（turn 未正常完成）
    assert "turn_end" not in snap["journal_types"]
    # 每轮 provider 请求都成功记 success
    assert snap["hooks_provider"].count('"success"') == 3


# ---------------------------------------------------------------------------
# 哨兵与回归
# ---------------------------------------------------------------------------

def test_golden_loop_boundary_sentinel():
    """哨兵：循环边界契约未被顺手更改。放宽前先更新契约文档。"""
    assert AGENT_MAX_TOOL_ROUNDS == 40


def test_golden_baseline_runs_both_paths_symmetric(tmp_path) -> None:
    """双路径对称性抽查：同一剧本（工具+文本）在两条路径下 usage/journal 关键面一致。"""
    tc = ToolCall(id="call_1", name="read", arguments='{"path": "/tmp/gm.txt"}')

    p1 = _FakeProvider(rounds=[_mk_resp("q", (tc,)), _mk_resp("a")])
    h1 = _ScriptedHooks()
    e1 = _make_engine(tmp_path / "ns", p1, h1)
    def _process(tcs, messages, content=""):
        messages.append(ModelMessage(role=MessageRole.ASSISTANT, content=content, tool_calls=tcs))
        for t in tcs:
            messages.append(ModelMessage(role=MessageRole.TOOL, content="o",
                                         name=t.name, tool_call_id=t.id))
    e1._tool_call_executor = type("_TCE", (), {"process": staticmethod(_process)})()
    e1._call_model("对称性提问")

    p2 = _FakeProvider(stream_rounds=[
        [{"type": "text_delta", "text": "q"},
         {"type": "tool_call_complete", "id": "call_1", "name": "read",
          "arguments": '{"path": "/tmp/gm.txt"}'}],
        [{"type": "text_delta", "text": "a"}],
    ])
    h2 = _ScriptedHooks()
    e2 = _make_engine(tmp_path / "s", p2, h2)
    e2._execute_tool_with_retry = lambda name, args: "o"
    list(e2.stream_call_model("对称性提问"))

    s1 = _snapshot(e1, p1, h1, e1._journal_dir)
    s2 = _snapshot(e2, p2, h2, e2._journal_dir)
    # usage 累计口径一致
    assert s1["usage"] == s2["usage"] == {"input_tokens": 20, "output_tokens": 10, "cached_tokens": 0}
    # provider 看到的消息序列形状一致（两条路径构建的请求同构）
    assert json.loads(s1["provider_msgs"])[1] == json.loads(s2["provider_msgs"])[1]
    # 基线事实（探针实测）：两条路径的 journal 事件【不】对称——
    # 非 stream 的 tool_result/checkpoint 由 ToolCallExecutor/checkpoint 文件处理，
    # turn_end 为 stream 路径专属。此处钉死该差异，迁移期若试图「顺手统一」
    # 必须先改契约文档评审，不得静默漂移。
    assert "tool_call" in s1["journal_types"] and "tool_call" in s2["journal_types"]
    assert "turn_end" in s2["journal_types"] and "turn_end" not in s1["journal_types"]
