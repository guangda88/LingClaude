"""N5b 流内停滞 watchdog（N5a 的补充，防「流中途挂死」观测盲区）。

背景（为什么 N5a 不够）:
  N5a 挂在 _record_long_task_metrics 收尾点——只有流循环正常退出才触发。
  流中途挂死（proxy 卡死 / 429 重试黑洞 / 网络丢包）时 next() 永不返回，
  连 for 循环体内的 interrupt 检查都不执行（它只在事件到达间隙跑），
  收尾点永远走不到，症状与「慢」完全无法区分。

形态: daemon 旁路线程监视「事件心跳」。CLI 流循环每收到一个事件就
touch 一次心跳（时间戳+事件类型），watchdog 线程按窗口语义判定停滞。

三个零事件窗口语义不同（docs/audit/ROUTING_TOPOLOGY_v1.md §4.2）:
  - 首事件前:  模型推理思考期，合法可达分钟级（P0.1 案例 4096 全程 0 delta）
               → 120s WARNING（仅一次，不打扰 LingBus）
  - 工具执行期: 上个事件是 tool_call_start，生成器在工具执行期间零 yield
               （model_call.py:518-530；单轮测试套件 756s 是常态）
               → 跳过计时（工具超时归 tool_executor 管）
  - 事件间隙:  正常间隔毫秒级
               → 60s WARNING → 300s ERROR + LingBus（各一次）

设计约束: 只告警不打断（守卫永不 raise / 不 set interrupt_event——
打断权归用户 Ctrl+C / pump 中断）。所有方法 best-effort，失败只落日志。
"""
from __future__ import annotations

import logging
import threading
import time

from lingclaude.coordination.alert import send_lingbus_alert

_logger = logging.getLogger(__name__)

# 阈值（秒）。首事件前=推理思考期放宽；事件间隙按正常毫秒级收紧。
BEFORE_FIRST_WARN_S = 120.0
GAP_WARN_S = 60.0
GAP_ERROR_S = 300.0
# watchdog 轮询间隔（远小于最严阈值即可）
DEFAULT_POLL_INTERVAL_S = 5.0

_LINGBUS_ALERT_SUBJECT = "[N5b守卫] 流内停滞"

# 推理思考型窗口: 这些事件之后模型进同步推理/重排，零事件合法可达分钟级
# （P0.1 案例 4096 全程 0 delta），按 BEFORE_FIRST_WARN_S 放宽，只 WARNING 一次。
#   ""            = 首事件前（模型首次推理思考期）
#   tool_call_end = 工具结果回传后的模型再推理期（model_call.py 事件流：
#                   tool_call_end -> 重新进 provider.stream_complete -> 下个 delta）
#   status        = 换候选重试 / 幻觉闭环修正的同步推理期（provider 调用零 yield）
_REASONING_LIKE_EVENTS = frozenset({"", "tool_call_end", "status"})


class StreamWatchdog:
    """流内事件心跳监视器。

    用法（CLI 流循环侧）::

        wd = StreamWatchdog()
        wd.start()
        try:
            for event in engine.stream_call_model(prompt):
                wd.touch(str(event.get("type", "")))
                ...
        finally:
            wd.stop()
    """

    def __init__(self, *, poll_interval_s: float = DEFAULT_POLL_INTERVAL_S) -> None:
        self._poll_interval_s = poll_interval_s
        self._lock = threading.Lock()
        self._last_event_at = 0.0
        self._last_event_type = ""
        self._warned = False
        self._errored = False
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- 流循环侧（每事件一次） ----------------------------------------

    def start(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._last_event_at = now
            self._last_event_type = ""
            self._warned = False
            self._errored = False
        self._stopped.clear()
        self._thread = threading.Thread(
            target=self._watch_loop, name="n5b-watchdog", daemon=True,
        )
        try:
            self._thread.start()
        except Exception:
            # 沙箱线程资源耗尽等场景:观测组件绝不破坏流式主路径
            self._thread = None
            self._stopped.set()
            _logger.warning(
                "[N5b] watchdog thread start failed; degrade to no-op",
                exc_info=True,
            )

    def touch(self, event_type: str) -> None:
        """流循环每收到一个事件调用一次。

        tool_call_start 会把窗口切到「工具执行期」（跳过计时）；
        "" / tool_call_end / status 归入推理思考型窗口（120s 放宽）；
        其余事件把窗口切回「事件间隙」（60s → 300s）。
        """
        with self._lock:
            self._last_event_at = time.monotonic()
            self._last_event_type = event_type
            self._warned = False
            self._errored = False

    def stop(self) -> None:
        """停表。不 join——收尾路径多（含异常路径），daemon 线程置位即退。"""
        self._stopped.set()

    # ---- watchdog 线程侧 -------------------------------------------------

    def _watch_loop(self) -> None:
        while not self._stopped.wait(self._poll_interval_s):
            try:
                self._check_once()
            except Exception:  # noqa: BLE001
                _logger.debug("[N5b] watchdog check failed", exc_info=True)

    def _check_once(self) -> None:
        with self._lock:
            since = time.monotonic() - self._last_event_at
            last_type = self._last_event_type

        if last_type == "tool_call_start":
            return  # 工具执行期: 生成器零 yield 是常态，跳过计时

        if last_type in _REASONING_LIKE_EVENTS:
            # 推理思考型窗口（首事件前/再推理期/重试期）: 只 WARNING 一次，不升 ERROR
            if since >= BEFORE_FIRST_WARN_S and not self._warned:
                self._warned = True
                _logger.warning(
                    "[N5b守卫] 流停滞 WARNING: 推理期已 %.0fs 无新事件（上个事件=%s）",
                    since, last_type or "首事件前",
                )
            return

        if since >= GAP_WARN_S and not self._warned:
            self._warned = True
            _logger.warning(
                "[N5b守卫] 流停滞 WARNING: 事件间隙已 %.0fs 无新事件（上个事件=%s）",
                since, last_type,
            )
            return

        if since >= GAP_ERROR_S and not self._errored:
            self._errored = True
            detail = (
                f"流内停滞 ERROR: 上个事件={last_type} 已 {since:.0f}s 无新事件"
                f"（疑似 proxy 卡死/429 黑洞/网络丢包）"
            )
            _logger.error("[N5b守卫] %s", detail)
            send_lingbus_alert(_LINGBUS_ALERT_SUBJECT, detail)
