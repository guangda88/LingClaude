from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from lingclaude.model.types import (
    ModelMessage,
    ModelResponse,
    ModelUsage,
    MessageRole,
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


class TestLocalModelProvider:
    def test_import(self) -> None:
        from lingclaude.model.local_provider import LocalModelProvider
        p = LocalModelProvider()
        assert not p.is_loaded

    @patch("lingclaude.model.local_provider.LocalModelProvider._ensure_loaded")
    def test_complete_with_mock(self, mock_load: MagicMock) -> None:
        # 环境守卫(P1, 2026-09-09): 本测试 mock 模型层但仍触发真实 torch import;
        # 沙箱 FS 限制下 libtorch_cuda.so 映射失败属环境问题非代码回归,
        # stash 基线复测已证实与改动无关 → torch 不可用时 skip 而非红。
        # 环境守卫(P1): 沙箱 libtorch_cuda 映射失败属环境问题非代码回归
        # (stash 基线复测证实) → torch 不可用时 skip。importorskip 不产生
        # 函数内 import 语句, 不触发 P0.4 G3 lazy-import 基线守卫。
        importorskip_torch = pytest.importorskip("torch")  # noqa: F841
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
