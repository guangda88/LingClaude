from __future__ import annotations

from unittest.mock import MagicMock, patch

from lingclaude.model.types import (
    ModelConfig,
    ModelMessage,
    ModelResponse,
    ModelUsage,
    MessageRole,
)
from lingclaude.model.hybrid_router import HybridRouterProvider
from lingclaude.model.intelligent_router import (
    IntelligentRouter,
    RoutingDecision,
    TaskComplexity,
    TaskType,
    GLMModel,
)


class _FakeProvider:
    def __init__(self, response: ModelResponse | None = None):
        self._response = response or ModelResponse(
            content="test", model="fake", usage=ModelUsage(),
        )
        self.calls: list[tuple] = []

    def complete(self, messages, config=None, tools=None):
        self.calls.append(("complete", messages, config, tools))
        from lingclaude.core.types import Result
        return Result.ok(self._response)

    async def acomplete(self, messages, config=None, tools=None):
        from lingclaude.core.types import Result
        return Result.ok(self._response)

    def count_tokens(self, text: str) -> int:
        return len(text) // 4


class _FakeRouter:
    def __init__(self, complexity: TaskComplexity, task_type: TaskType = TaskType.OTHER):
        self._complexity = complexity
        self._task_type = task_type

    def route(self, query: str) -> RoutingDecision:
        return RoutingDecision(
            model=GLMModel.GLM_4_7,
            complexity=self._complexity,
            task_type=self._task_type,
            reason="test",
            confidence=0.9,
        )


class TestLocalModelProvider:
    def test_import(self) -> None:
        from lingclaude.model.local_provider import LocalModelProvider
        p = LocalModelProvider()
        assert not p.is_loaded

    @patch("lingclaude.model.local_provider.LocalModelProvider._ensure_loaded")
    def test_complete_with_mock(self, mock_load: MagicMock) -> None:
        from lingclaude.model.local_provider import LocalModelProvider
        p = LocalModelProvider()
        p._loaded = True
        p._model = MagicMock()
        p._tokenizer = MagicMock()

        mock_tokens = MagicMock()
        mock_tokens.shape = [1, 10]
        p._tokenizer.encode.return_value = mock_tokens
        p._tokenizer.return_value = {"input_ids": mock_tokens, "attention_mask": mock_tokens}

        mock_output = MagicMock()
        mock_output.__getitem__ = lambda self, idx: [list(range(10)) + [100, 101, 102]]
        p._model.device = "cpu"
        p._model.generate.return_value = mock_output
        p._tokenizer.decode.return_value = "generated text"
        p._tokenizer.eos_token_id = 0

        msgs = (ModelMessage(role=MessageRole.USER, content="hello"),)
        result = p.complete(msgs)
        assert result.is_ok
        assert result.data.model == "lingai-local-qwen2-1.5b"

    def test_messages_to_prompt(self) -> None:
        from lingclaude.model.local_provider import LocalModelProvider
        p = LocalModelProvider()
        msgs = (
            ModelMessage(role=MessageRole.SYSTEM, content="sys"),
            ModelMessage(role=MessageRole.USER, content="hi"),
        )
        prompt = p._messages_to_prompt(msgs)
        assert "<|im_start|>system" in prompt
        assert "<|im_start|>user" in prompt
        assert "<|im_start|>assistant" in prompt


class TestHybridRouter:
    def test_simple_task_goes_local(self) -> None:
        local = _FakeProvider()
        api = _FakeProvider()
        router = _FakeRouter(TaskComplexity.SIMPLE)
        hybrid = HybridRouterProvider(local, api, router)

        msgs = (ModelMessage(role=MessageRole.USER, content="简单问题"),)
        result = hybrid.complete(msgs)
        assert result.is_ok
        assert len(local.calls) == 1
        assert len(api.calls) == 0

    def test_complex_task_goes_api(self) -> None:
        local = _FakeProvider()
        api = _FakeProvider()
        router = _FakeRouter(TaskComplexity.COMPLEX)
        hybrid = HybridRouterProvider(local, api, router)

        msgs = (ModelMessage(role=MessageRole.USER, content="复杂推理"),)
        result = hybrid.complete(msgs)
        assert result.is_ok
        assert len(local.calls) == 0
        assert len(api.calls) == 1

    def test_fallback_on_local_failure(self) -> None:
        from lingclaude.core.types import Result

        failing_local = MagicMock()
        failing_local.complete.return_value = Result.fail("OOM", code="ERR")
        api = _FakeProvider()
        router = _FakeRouter(TaskComplexity.SIMPLE)
        hybrid = HybridRouterProvider(failing_local, api, router)

        msgs = (ModelMessage(role=MessageRole.USER, content="test"),)
        result = hybrid.complete(msgs)
        assert result.is_ok
        assert len(api.calls) == 1

    def test_search_goes_local(self) -> None:
        local = _FakeProvider()
        api = _FakeProvider()
        router = _FakeRouter(TaskComplexity.COMPLEX, TaskType.SEARCH)
        hybrid = HybridRouterProvider(local, api, router)

        msgs = (ModelMessage(role=MessageRole.USER, content="搜索文件"),)
        result = hybrid.complete(msgs)
        assert result.is_ok
        assert len(local.calls) == 1

    def test_stats(self) -> None:
        local = _FakeProvider()
        api = _FakeProvider()
        router = _FakeRouter(TaskComplexity.SIMPLE)
        hybrid = HybridRouterProvider(local, api, router)

        msgs = (ModelMessage(role=MessageRole.USER, content="test"),)
        hybrid.complete(msgs)
        stats = hybrid.get_stats()
        assert stats["local_requests"] == 1
        assert stats["api_requests"] == 0
        assert stats["savings_ratio"] == 1.0

    def test_extract_query(self) -> None:
        local = _FakeProvider()
        api = _FakeProvider()
        hybrid = HybridRouterProvider(local, api)
        msgs = (
            ModelMessage(role=MessageRole.SYSTEM, content="sys"),
            ModelMessage(role=MessageRole.USER, content="user query here"),
        )
        assert hybrid._extract_query(msgs) == "user query here"


class TestCreateHybridProvider:
    def test_factory(self) -> None:
        from lingclaude.model import create_hybrid_provider
        api = _FakeProvider()
        hybrid = create_hybrid_provider(api)
        assert isinstance(hybrid, HybridRouterProvider)
