"""LINGKERNEL_v1 D1 - ModelAdapter 测试。"""

from __future__ import annotations

from dataclasses import dataclass, field

from lingclaude.core.model_adapter import ModelAdapter, ModelCallResult, resolve_model_config
from lingclaude.core.types import Result


@dataclass
class FakeResponse:
    content: str = "hi"
    tool_calls: tuple = ()
    finish_reason: str = "stop"


class FakeProvider:
    name = "fake"

    def complete(self, messages, tools=None):
        return Result.ok(FakeResponse(content="ok", tool_calls=()))

    def stream(self, messages, tools=None):
        yield {"type": "chunk", "text": "a"}
        yield {"type": "done"}


class FailingProvider:
    def complete(self, messages, tools=None):
        return Result.fail("upstream 500", code="UPSTREAM")


class ThrowingProvider:
    def complete(self, messages, tools=None):
        raise RuntimeError("boom")


# ---- call ----

def test_call_no_provider():
    adapter = ModelAdapter()
    r = adapter.call(())
    assert not r.is_ok


def test_call_success():
    adapter = ModelAdapter(FakeProvider())
    r = adapter.call(("m1",))
    assert r.is_ok
    assert r.data.content == "ok"
    assert r.data.provider_name == "FakeProvider"


def test_call_provider_error():
    adapter = ModelAdapter(FailingProvider())
    r = adapter.call(())
    assert not r.is_ok


def test_call_exception_caught():
    adapter = ModelAdapter(ThrowingProvider())
    r = adapter.call(())
    assert not r.is_ok
    assert "boom" in str(r.error)


def test_set_provider():
    adapter = ModelAdapter()
    assert adapter.provider is None
    adapter.set_provider(FakeProvider())
    assert adapter.provider is not None
    assert adapter.call(()).is_ok


# ---- stream_call ----

def test_stream_call_no_provider():
    adapter = ModelAdapter()
    assert adapter.stream_call(()) is None


def test_stream_call_success():
    adapter = ModelAdapter(FakeProvider())
    gen = adapter.stream_call(())
    chunks = list(gen)
    assert chunks[0] == {"type": "chunk", "text": "a"}
    assert chunks[-1] == {"type": "done"}


def test_stream_call_no_stream_method():
    adapter = ModelAdapter(FailingProvider())  # 无 stream 方法
    assert adapter.stream_call(()) is None


# ---- resolve_model_config ----

@dataclass
class Cfg:
    model: str = "default-model"
    strong: str | None = None


def test_resolve_default():
    cfg = Cfg(model="m1")
    chosen, reason = resolve_model_config("p", cfg)
    assert chosen is cfg
    assert reason == "default"


def test_resolve_high_hallucination_uses_strong():
    cfg = Cfg(model="m1", strong="strong-model")
    chosen, reason = resolve_model_config("p", cfg, behavior_hallucination_risk=0.7)
    assert chosen == "strong-model"
    assert "strong" in reason


def test_resolve_high_hallucination_no_strong():
    cfg = Cfg(model="m1")
    chosen, reason = resolve_model_config("p", cfg, behavior_hallucination_risk=0.9)
    assert chosen is cfg
    assert reason == "default"


def test_resolve_low_hallucination_default():
    cfg = Cfg(model="m1", strong="strong-model")
    chosen, _ = resolve_model_config("p", cfg, behavior_hallucination_risk=0.3)
    assert chosen is cfg


def test_model_call_result_fields():
    r = ModelCallResult(content="x", tool_calls=(1,), finish_reason="stop",
                        used_fallback=True, provider_name="p")
    assert r.used_fallback is True
    assert r.provider_name == "p"