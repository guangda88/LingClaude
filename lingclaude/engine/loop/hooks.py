# lingclaude/core/loop_seam.py
"""第 0 步（2026-09-21，全 15 家精读 §3.2）：循环体治理钩子接口化。

背景（docs/research/20260921_coding_agent_expansion.md §3.1）：
- `_call_model`/`stream_call_model` 循环体本身是 for 轮次 + 局部栈变量，可重入；
- 但它们引用 20/19 个 `self._*` 槽位，其中「治理钩子」类（journal / behavior /
  路由 slot / 幻觉闭环 / flywheel 遥测）让循环与引擎状态纠缠，无法被注入不同
  实现驱动（测试要全离线回归、headless 要无 TUI 跑循环，都卡在这）。

本模块把「治理钩子」抽成可注入的 seam 接口（Protocol + 默认实现），纯函数核心
= `for round_idx: provider.complete → tool_call_executor → check_stop`。
默认实现 `DefaultLoopHooks` 逐项复现 `ModelCallMixin` 现有调用，**行为零变化**；
测试 / headless / 未来热更注入 fake 钩子即可全离线驱动同一循环体。

边界纪律（铁律 2 停层声明）：
- 内核 = `LoopHooks` Protocol（注入面，纯接口无状态）
- 接缝 = `QueryEngine.hooks` 属性（装配点，默认 DefaultLoopHooks(self)）
- 实现 = `DefaultLoopHooks`（绑定 self，转发到原有 _journal_append/_behavior/
  _task_router/_hallucination_correction/_log_to_flywheel，逐点保真）

不做的事（避免与 P0 本体越界）：
- 不改 `_save_checkpoint`/`resume_interrupted`（已可重建，§3.1 反证）；
- 不改 provider / tool_call_executor（它们是「被调用的能力」，非「治理钩子」）；
- 不把 20 槽位全接口化——只接「治理钩子」5 类，其余（provider/tools/config
  解析/usage 累加/checkpoint）保留为能力依赖，循环纯化（P2-13）再处理。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class LoopHooks(Protocol):
    """循环体治理钩子注入面（第 0 步纯函数化的唯一新增接口）。

    五个方法对应循环体原本散落的 5 类 `self._*` 治理调用。默认实现逐项
    转发到引擎现有方法，行为零变化；测试注入 fake 即可全离线回归。
    任何方法都不得抛出异常影响主输出（fail-soft 语义与原实现一致）。
    """

    def journal_append(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """R5 journal append（best-effort，原 ModelCallMixin._journal_append）。"""
        ...

    def record_provider_outcome(
        self, cfg: Any, kind: str, error: str | None = None,
    ) -> str | None:
        """记录 provider 成败到 TaskRouter 熔断统计（原 _record_provider_outcome）。

        kind: "success" / "error"。返回 pname（找不到时 None）。
        """
        ...

    def switch_target_health(self, provider_name: str) -> tuple[bool, str]:
        """降级链健康度门禁（原 task_router.check_switch_target_health）。

        返回 (allowed, reason)。门禁自身故障应返回 (True, 原因) 放行，不放大失败。
        """
        ...

    def should_hallucination_correct(
        self, prompt: str, used_tools: bool, messages: list,
    ) -> bool:
        """是否触发幻觉闭环修正（原 _should_hallucination_correct）。"""
        ...

    def hallucination_correction(
        self, messages: list, content: str, tools: Any, cfg: Any,
    ) -> str | None:
        """执行幻觉修正，返回修正后内容或 None（原 _hallucination_correction）。"""
        ...

    def log_flywheel(self, **kwargs: Any) -> None:
        """flywheel 遥测（原 _log_to_flywheel）。kwargs 透传 pattern_type 等。"""
        ...


class DefaultLoopHooks:
    """默认实现：绑定 QueryEngine（self），逐项转发到现有方法，行为零变化。

    装配点：`QueryEngine.hooks`（wiring 新增槽位，默认 DefaultLoopHooks(self)）。
    构造时传入 engine，保留引用以调用其治理方法。
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    # ── 逐点转发到 ModelCallMixin 现有方法（调用面 1:1，不改任何语义） ──

    def journal_append(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        try:
            self._engine._journal_append(event_type, data)
        except Exception:
            logger.debug("LoopHooks.journal_append failed (non-blocking)", exc_info=True)

    def record_provider_outcome(
        self, cfg: Any, kind: str, error: str | None = None,
    ) -> str | None:
        try:
            return self._engine._record_provider_outcome(cfg, kind, error)
        except Exception:
            logger.debug("LoopHooks.record_provider_outcome failed (non-blocking)", exc_info=True)
            return None

    def switch_target_health(self, provider_name: str) -> tuple[bool, str]:
        try:
            return self._engine._task_router.check_switch_target_health(provider_name)
        except Exception as e:  # 门禁自身故障不放大队失败（原 try/except 语义）
            logger.debug("switch health gate error: %s", e)
            return True, f"门禁异常放行: {e}"

    def should_hallucination_correct(
        self, prompt: str, used_tools: bool, messages: list,
    ) -> bool:
        try:
            return self._engine._should_hallucination_correct(prompt, used_tools, messages)
        except Exception:
            logger.debug("LoopHooks.should_hallucination_correct failed", exc_info=True)
            return False

    def hallucination_correction(
        self, messages: list, content: str, tools: Any, cfg: Any,
    ) -> str | None:
        try:
            return self._engine._hallucination_correction(messages, content, tools, cfg)
        except Exception:
            logger.debug("LoopHooks.hallucination_correction failed", exc_info=True)
            return None

    def log_flywheel(self, **kwargs: Any) -> None:
        try:
            self._engine._log_to_flywheel(**kwargs)
        except Exception:
            logger.debug("LoopHooks.log_flywheel failed (non-blocking)", exc_info=True)


def default_hooks_for(engine: Any) -> "DefaultLoopHooks":
    """装配助手：为引擎构造默认钩子（wiring 槽位 factory 用）。"""
    return DefaultLoopHooks(engine)
