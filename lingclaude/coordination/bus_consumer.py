"""LingBus 任务消费者循环（P4: 从 cli/repl_turn.py 抽出，供 CLI/API 进程按需启用）。

原 start_bus_responder_background 内联在 repl_turn.py，只有交互式 CLI 进程消费
LingBus 任务；api.py 引擎进程常驻却不消费（灵通派给灵克的任务在引擎模式下
无人应答）。抽为独立模块后：
  - cli 交互进程（repl_turn 兼容壳转发，见 repl_turn.start_bus_responder_background）
  - api 引擎进程（run_server 时按 LINGCLAUDE_BUS_LISTENER 门控启动）

设计原则（与原 repl_turn 实现一致）:
  - 不注册 SIGINT/SIGTERM（与宿主进程信号处理冲突），用 stop_event 协作停止
  - daemon 线程，主进程退出强制终止，不留孤儿
  - 单次 poll 异常只记录不退出（端口不可达/数据库锁等临时错误可自愈）
"""
from __future__ import annotations

import logging
import threading

from lingclaude.coordination.bus_responder import BusResponder

logger = logging.getLogger(__name__)


def start_bus_consumer_background(
    interval: float = 30.0,
    thread_name: str = "lingclaude-bus-consumer",
) -> threading.Event:
    """后台线程启动 BusResponder 消费者循环。

    :param interval: 轮询间隔（秒）
    :param thread_name: 线程名（CLI/API 多消费者并存时区分）
    :returns: stop_event —— 宿主调用 .set() 协作停止线程
    """
    stop_event = threading.Event()

    def _background_loop() -> None:
        try:
            responder = BusResponder()
        except Exception as e:  # noqa: BLE001 — 初始化失败不能阻塞宿主启动
            logger.error("BusResponder init failed, skip background polling: %s", e)
            return

        logger.info("BusResponder consumer started (interval=%.0fs)", interval)
        while not stop_event.is_set():
            try:
                responder.poll_and_respond()
            except Exception as e:  # noqa: BLE001 — 单次失败不退出线程
                logger.error("BusResponder background poll error: %s", e)
            # 周期 wait + 提前唤醒（stop_event 被 set 时立即退出）
            if stop_event.wait(timeout=interval):
                break
        logger.info("BusResponder consumer stopped")

    thread = threading.Thread(
        target=_background_loop,
        name=thread_name,
        daemon=True,  # 主进程退出时强制终止,避免孤儿线程
    )
    thread.start()
    return stop_event


def stop_bus_consumer(stop_event: threading.Event | None) -> None:
    """协作停止消费者线程（幂等；None 输入 no-op）。"""
    if stop_event is not None:
        stop_event.set()
