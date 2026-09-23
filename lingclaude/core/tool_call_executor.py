"""工具调用执行器 — T1-3 并行/顺序工具执行（从 query_engine 拆出，瘦身）。

负责三条工具结果路径：
- process: 主入口（顺序 or 并行分流）
- _process_parallel: ThreadPoolExecutor 并行（≤4），写工具冲突降级顺序
- _process_single: 单工具执行（降级路径复用）

依赖注入：构造时传入 QueryEngine 引用（type: Any 避免循环导入），
通过 engine 访问 _behavior/_dementia_detector/_write_lock/
_execute_tool_with_retry/_log_to_flywheel/_is_concurrency_safe。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from lingclaude.core.image_content import extract_image_content, image_tool_text
from lingclaude.core.types import is_tool_error
import time

logger = logging.getLogger(__name__)


class ToolCallExecutor:
    """工具调用执行器 — 顺序/并行分流 + 写冲突降级。"""

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self._flywheel: Any = None  # F6: 惰性缓存，避免每事件重建连接

    def _log_tool_event(self, tool_name: str, success: bool) -> None:
        """F6 (2026-09-23): 双边事件快照——成功侧是 error_log 缺失的半边
        语料，失败侧也写（success=0），回放侧才能拼出完整工具时序。
        吞异常：遥测不得影响工具执行主链路。"""
        try:
            if self._flywheel is None:
                from lingclaude.core.data_flywheel import DataFlywheel, ToolEvent
                self._flywheel = DataFlywheel()
                self._tool_event_cls = ToolEvent
            self._flywheel.log_tool_event(self._tool_event_cls(
                session_id=getattr(self._engine, "session_id", "") or "",
                tool_name=tool_name,
                success=success,
                occurred_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            ))
        except Exception:
            logger.debug("tool_event write failed", exc_info=True)

    def process(self, tool_calls: tuple, messages: list, content: str = "") -> None:
        """执行一批工具调用（主入口）。

        Args:
            tool_calls: 模型返回的 tool call 列表。
            messages: 追加结果消息的目标列表。
            content: assistant 消息文本。
        """
        from lingclaude.core.model_types import ModelMessage, MessageRole

        engine = self._engine
        engine._behavior = engine._behavior.record_tool_calls(count=len(tool_calls))
        messages.append(ModelMessage(
            role=MessageRole.ASSISTANT,
            content=content,
            tool_calls=tool_calls,
        ))

        # T1-3: 并行工具执行 — 全部 is_concurrency_safe 且 >1 个时并行，否则顺序
        if len(tool_calls) > 1 and all(engine._is_concurrency_safe(tc.name) for tc in tool_calls):
            self._process_parallel(tool_calls, messages)
            return

        for tc in tool_calls:
            engine._dementia_detector.record_tool_call(tc.name, tc.arguments)
            tool_output = engine._execute_tool_with_retry(tc.name, tc.arguments)
            if is_tool_error(tool_output):
                engine._behavior = engine._behavior.record_tool_calls(count=0, errors=1)
                engine._log_to_flywheel(
                    pattern_type="tool_error",
                    error_message=tool_output[:200],
                    tool_name=tc.name,
                )
                self._log_tool_event(tc.name, success=False)
            else:
                self._log_tool_event(tc.name, success=True)
            messages.append(ModelMessage(
                role=MessageRole.TOOL,
                content=image_tool_text(tool_output, extract_image_content(tool_output)),
                name=tc.name,
                tool_call_id=tc.id,
                image_content=extract_image_content(tool_output),
            ))

    def _process_parallel(self, tool_calls: tuple, messages: list) -> None:
        """并行执行一批 concurrency-safe 工具，按原顺序收集结果。

        写工具不标记 is_concurrency_safe=True，此处兜底校验：若误标了写工具，
        自动降级为顺序执行并记录警告。
        """
        from lingclaude.core.model_types import ModelMessage, MessageRole
        from lingclaude.core.types import WRITE_SCOPED_TOOLS

        engine = self._engine

        # 检测是否存在并发写冲突
        write_tools = {tc.name for tc in tool_calls if tc.name in WRITE_SCOPED_TOOLS}
        if write_tools:
            logger.warning(
                "T1-3 并行冲突检测: 以下工具不应并发执行（已降级为顺序执行）: %s",
                write_tools,
            )
            # 降级为顺序执行
            for tc in tool_calls:
                self._process_single(tc, messages)
            return

        def _run(tc: Any) -> tuple[Any, str]:
            # 写工具加锁序列化
            with engine._write_lock:
                engine._dementia_detector.record_tool_call(tc.name, tc.arguments)
                return tc, engine._execute_tool_with_retry(tc.name, tc.arguments)

        results: list[tuple[Any, str]] = []
        with ThreadPoolExecutor(max_workers=min(len(tool_calls), 4)) as pool:
            futures = [pool.submit(_run, tc) for tc in tool_calls]
            for fut in futures:
                tc, tool_output = fut.result()
                results.append((tc, tool_output))

        for tc, tool_output in results:
            if is_tool_error(tool_output):
                engine._behavior = engine._behavior.record_tool_calls(count=0, errors=1)
                engine._log_to_flywheel(
                    pattern_type="tool_error",
                    error_message=tool_output[:200],
                    tool_name=tc.name,
                )
                self._log_tool_event(tc.name, success=False)
            else:
                self._log_tool_event(tc.name, success=True)
            messages.append(ModelMessage(
                role=MessageRole.TOOL,
                content=image_tool_text(tool_output, extract_image_content(tool_output)),
                name=tc.name,
                tool_call_id=tc.id,
                image_content=extract_image_content(tool_output),
            ))

    def _process_single(self, tc: Any, messages: list) -> None:
        """执行单个工具调用（供降级路径复用）。"""
        from lingclaude.core.model_types import ModelMessage, MessageRole

        engine = self._engine
        engine._dementia_detector.record_tool_call(tc.name, tc.arguments)
        with engine._write_lock:
            tool_output = engine._execute_tool_with_retry(tc.name, tc.arguments)
        if is_tool_error(tool_output):
            engine._behavior = engine._behavior.record_tool_calls(count=0, errors=1)
            engine._log_to_flywheel(
                pattern_type="tool_error",
                error_message=tool_output[:200],
                tool_name=tc.name,
            )
            self._log_tool_event(tc.name, success=False)
        else:
            self._log_tool_event(tc.name, success=True)
        messages.append(ModelMessage(
            role=MessageRole.TOOL,
            content=image_tool_text(tool_output, extract_image_content(tool_output)),
            name=tc.name,
            tool_call_id=tc.id,
            image_content=extract_image_content(tool_output),
        ))
