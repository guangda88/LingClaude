from __future__ import annotations

import logging
import threading
from typing import Any

from lingclaude.core.types import Result
from lingclaude.model.types import (
    ModelConfig,
    ModelMessage,
    ModelProvider,
    ModelResponse,
    ModelUsage,
)

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_PATH = "/home/ai/models/lingai-merged"


class LocalModelProvider(ModelProvider):
    """Local Qwen2-1.5B inference using transformers.

    Loads the lingai-merged model (fine-tuned Qwen2-1.5B) onto GPU.
    Designed for simple coding tasks: code completion, search queries,
    simple analysis, boilerplate generation.

    Token counting uses the model tokenizer directly — zero API cost.
    """

    def __init__(self, model_path: str = _DEFAULT_MODEL_PATH) -> None:
        self._model_path = model_path
        self._model = None
        self._tokenizer = None
        self._lock = threading.Lock()
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            try:
                from transformers import AutoModelForCausalLM, AutoTokenizer
                import torch

                logger.info("Loading local model from %s", self._model_path)
                self._tokenizer = AutoTokenizer.from_pretrained(  # nosec B615 — 本地模型加载，revision 由 _model_path 指定
                    self._model_path, trust_remote_code=True,
                )
                self._model = AutoModelForCausalLM.from_pretrained(  # nosec B615 — 本地模型加载
                    self._model_path,
                    dtype=torch.float16,
                    device_map="auto",
                    trust_remote_code=True,
                )
                self._model.eval()
                self._loaded = True
                logger.info("Local model loaded successfully (device_map=auto)")
            except Exception as e:
                logger.error("Failed to load local model: %s", e)
                raise

    def complete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        try:
            self._ensure_loaded()
        except Exception as e:
            return Result.fail(f"Local model load failed: {e}", code="LOCAL_LOAD_ERROR")

        prompt_text = self._messages_to_prompt(messages)
        encoded = self._tokenizer(prompt_text, return_tensors="pt", padding=True)
        input_ids = encoded["input_ids"].to(self._model.device)
        attention_mask = encoded["attention_mask"].to(self._model.device)
        input_len = input_ids.shape[1]

        max_new = (config.max_tokens if config else 512) or 512
        temperature = config.temperature if config else 0.7

        try:
            import torch
            with torch.no_grad():
                output_ids = self._model.generate(
                    input_ids,
                    attention_mask=attention_mask,
                    max_new_tokens=min(max_new, 1024),
                    temperature=temperature if temperature > 0 else 0.1,
                    do_sample=temperature > 0,
                    pad_token_id=self._tokenizer.eos_token_id,
                )
        except Exception as e:
            return Result.fail(f"Local inference failed: {e}", code="LOCAL_INFERENCE_ERROR")

        generated = output_ids[0][input_len:]
        text = self._tokenizer.decode(generated, skip_special_tokens=True)
        output_len = len(generated)

        return Result.ok(ModelResponse(
            content=text.strip(),
            model="lingai-local-qwen2-1.5b",
            usage=ModelUsage(input_tokens=input_len, output_tokens=output_len),
        ))

    async def acomplete(
        self,
        messages: tuple[ModelMessage, ...],
        config: ModelConfig | None = None,
        tools: tuple[dict[str, Any], ...] | None = None,
    ) -> Result[ModelResponse]:
        return self.complete(messages, config, tools)

    def count_tokens(self, text: str) -> int:
        try:
            self._ensure_loaded()
            return len(self._tokenizer.encode(text))
        except Exception:
            return len(text) // 4

    def _messages_to_prompt(self, messages: tuple[ModelMessage, ...]) -> str:
        parts: list[str] = []
        for msg in messages:
            if msg.role.value == "system":
                parts.append(f"<|im_start|>system\n{msg.content}<|im_end|>")
            elif msg.role.value == "user":
                parts.append(f"<|im_start|>user\n{msg.content}<|im_end|>")
            elif msg.role.value == "assistant":
                parts.append(f"<|im_start|>assistant\n{msg.content}<|im_end|>")
            elif msg.role.value == "tool":
                parts.append(f"<|im_start|>tool\n{msg.content}<|im_end|>")
        parts.append("<|im_start|>assistant\n")
        return "\n".join(parts)

    @property
    def is_loaded(self) -> bool:
        return self._loaded
