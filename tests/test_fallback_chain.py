"""F12f 降级链遍历修复回归（2026-09-28）。

修复前：loop_body.py 写死 cfg_attempt == 0，glm 429 → volcengine 404 后
不再下沉（只切一跳）。修复后：遍历候选链至 _MAX_FALLBACK_ATTEMPTS=6，
每跳仍过探活门禁，熔断 slot 由 resolve 跳过。

用例覆盖：
- 第一跳失败 → 第二跳成功（原有能力，防回归）
- 前两跳失败 → 第三跳成功（本次修复的核心：不再卡死在 cfg_attempt==1）
- 探活门禁拦截时仍 break（不盲切到死节点）
"""
from __future__ import annotations

from typing import Any

from lingclaude.core.model_types import ModelConfig
from lingclaude.engine.loop.loop_body import run_stream_call_model_loop


class _Hooks:
    def journal_append(self, *a, **k) -> None: ...
    def record_provider_outcome(self, *a, **k) -> None: ...
    def should_hallucination_correct(self, *a, **k) -> bool:
        return False
    def log_flywheel(self, *a, **k) -> None: ...
    def switch_target_health(self, *a, **k) -> tuple[bool, str]:
        return True, "探活通过"


class _ScriptedProvider:
    """按剧本逐次出牌：error / success。"""

    def __init__(self, script: list[str]) -> None:
        self._script = list(script)
        self.calls: list[str] = []  # 记录每次实际生效的 model 名

    def stream_complete(self, messages, config=None, tools=None):
        model = config.model if config else "?"
        self.calls.append(model)
        action = self._script.pop(0)
        if action == "error":
            yield {"type": "error", "error": f"HTTP 429/404 from {model}"}
            return
        yield {"type": "text_delta", "text": f"ok-{model}"}
        yield {"type": "finish", "reason": "stop"}

    def count_tokens(self, text: str) -> int:
        return len(text) // 4


class _Engine:
    """最小引擎 stub：_resolve_model_config 按候选链依次返回。"""

    def __init__(self, provider: Any, chain: list[str]) -> None:
        self._provider = provider
        self.hooks = _Hooks()
        self.session_id = "sess-fb"
        self._messages: list[Any] = []
        self._behavior = None
        self._chain = [ModelConfig(model=m, base_url=f"https://{m}.example.com") for m in chain]
        self._resolve_idx = 0

        class _Cfg:
            consecutive_failure_limit = 3
        self.config = _Cfg()

    def _build_messages(self, prompt: str, image_content: tuple[str, str] | None = None) -> list[Any]:
        from lingclaude.model.types import ModelMessage, MessageRole
        self._messages = [ModelMessage(role=MessageRole.USER, content=prompt)]
        return list(self._messages)

    def _build_openai_tools(self, query: str = "") -> tuple[dict, ...]:
        return ()

    def _resolve_model_config(self, prompt: str) -> tuple[Any, Any]:
        cfg = self._chain[min(self._resolve_idx, len(self._chain) - 1)]
        self._resolve_idx += 1
        return cfg, None

    class _TaskRouter:
        """最小 task_router stub：供门禁取 provider 名。"""
        def get_provider_name(self, api_key: str, base_url: str | None) -> str:
            return "stub-provider"

    _task_router = _TaskRouter()

    def _log_model_request(self, **k) -> None: ...
    def _assert_model_visible(self, seq, messages) -> None: ...
    def _pre_send_check(self, seq, messages) -> bool:
        return True
    def _log_to_flywheel(self, *a, **k) -> None: ...
    def _learn_from_turn(self, prompt, response) -> None: ...
    def _save_checkpoint(self, *a, **k) -> None: ...
    def _clear_checkpoint(self) -> None: ...
    def _finalize_turn(self, prompt, content, used_tools, ti, to, cfg, tc, ctx_input_tokens=None):
        return content
    def _append_to_session_history(self, *a, **k) -> None: ...
    def _track_behavior(self, *a, **k) -> None: ...
    def _hard_interrupt_message(self, kind, n) -> str:
        return f"hard_interrupt:{kind}:{n}"


def _collect(engine: Any, prompt: str = "hi") -> list[dict]:
    return list(run_stream_call_model_loop(engine, prompt))


def test_fallback_first_fails_second_succeeds() -> None:
    """一跳失败 → 二跳成功：原有能力防回归。"""
    provider = _ScriptedProvider(["error", "success"])
    engine = _Engine(provider, ["glm", "volcengine"])
    events = _collect(engine)
    assert any(e["type"] == "done" and "ok-volcengine" in e.get("content", "") for e in events)
    assert provider.calls == ["glm", "volcengine"]


def test_fallback_two_fail_third_succeeds() -> None:
    """修复核心：前两跳失败 → 沉到第三跳（修复前会卡在 cfg_attempt==1 直接报错）。"""
    provider = _ScriptedProvider(["error", "error", "success"])
    engine = _Engine(provider, ["glm", "volcengine", "deepseek"])
    events = _collect(engine)
    dones = [e for e in events if e["type"] == "done"]
    assert len(dones) == 1, f"应成功落稿，实际事件流: {[e['type'] for e in events]}"
    assert "ok-deepseek" in dones[0]["content"]
    # 三次尝试都被真实调用（glm、volcengine、deepseek 依次走完）
    assert provider.calls == ["glm", "volcengine", "deepseek"]


def test_fallback_gate_blocked_still_breaks() -> None:
    """探活门禁拦截时仍 break，不盲切（修复未破坏既有安全轨）。"""

    class _BlockingHooks(_Hooks):
        def switch_target_health(self, *a, **k) -> tuple[bool, str]:
            return False, "目标探活 unhealthy"

    provider = _ScriptedProvider(["error", "success"])
    engine = _Engine(provider, ["glm", "volcengine"])
    engine.hooks = _BlockingHooks()
    events = _collect(engine)
    # 第一跳失败 + 门禁拦截 → 直接 error，不走到第二跳
    assert any(e["type"] == "error" for e in events)
    assert provider.calls == ["glm"]  # 第二跳从未被调用


def test_fallback_syncs_current_model_name() -> None:
    """方案 B（2026-09-28）：降级成功时 engine._current_model_name 同步更新，
    toolbar 不再恒显示启动名（frozen ModelProviderConfig 不可写，改走引擎态）。"""
    provider = _ScriptedProvider(["error", "success"])
    engine = _Engine(provider, ["glm", "volcengine"])
    engine._current_model_name = ""  # 模拟 QueryEngine 装配初值
    events = _collect(engine)
    assert any(e["type"] == "done" for e in events)
    assert engine._current_model_name == "volcengine", (
        f"降级后 _current_model_name 应为备选名，实际: {engine._current_model_name!r}"
    )
