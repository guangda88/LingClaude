"""REPL 回合执行层（P4.1 从 cli/app.py 拆出）— turn 执行与可观测性收尾。

职责：long-task 指标（N5/N6 守卫挂点）、单轮执行、daemon 行为回喂（R2）、
启动期中断恢复。拆分说明见 repl.py 模块 docstring；函数体自 app.py 原样迁移。
"""

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lingclaude.cli.display import SessionSummary
from lingclaude.cli.render_facade import print_session_summary
from lingclaude.cli.long_task_metrics import append_long_task_metrics
from lingclaude.cli.n5_stream_watchdog import StreamWatchdog
from lingclaude.cli.n5_token_guard import check_token_exhaustion, resolve_max_tokens
from lingclaude.cli.repl_io import _flush_stream_line, _handle_stream_event, _stream_write, get_output_format
from lingclaude.core.config import load_config
from lingclaude.core.query_engine import QueryEngine
from lingclaude.ops.rss_watchdog import check_rss_watchdog, sample_rss_mb
from lingclaude.self_optimizer.daemon import OptimizationDaemon
import json

if TYPE_CHECKING:
    from lingclaude.core.config import lingclaudeConfig

_logger = logging.getLogger(__name__)

_behavior_daemon: OptimizationDaemon | None = None



def _maybe_run_daemon_cycle() -> None:
    """R2（系统论融入）：给断路 5 个月的自优化回路合闸。

    交互会话结束时触发一次 daemon 循环，24h 节流（should_run_cycle）；
    默认 report-only（_apply_params 不设 LINGCLAUDE_DAEMON_APPLY=1 不动
    config.yaml）。LINGCLAUDE_DAEMON_CYCLE=0 关闭（CI/测试用）。

    2026-09-16 退出阻塞修复：周期改为**后台 daemon 线程**执行——原实现
    同步跑在退出路径上，全仓扫描（含 bench 残缺样本）耗时数秒~数十秒，
    用户看到退出后终端长时间挂起只能 Ctrl+C 强杀（实测打断点就在
    evaluator.read_text）。后台化后退出立即返回，报告完成经 [自优化]
    行异步打印（解释器退出前 daemon 线程有机会写完即随进程回收，
    不阻塞、不悬挂）。
    """
    if os.environ.get("LINGCLAUDE_DAEMON_CYCLE") == "0":
        return

    def _run_cycle_bg() -> None:
        global _behavior_daemon
        try:
            daemon = _behavior_daemon
            if daemon is None:
                daemon = OptimizationDaemon(target=".", config=load_config(None))
                _behavior_daemon = daemon
            min_hours = float(os.environ.get("LINGCLAUDE_DAEMON_MIN_INTERVAL_HOURS", "24"))
            if not daemon.should_run_cycle(min_interval_hours=min_hours):
                return
            result = daemon.run_once()
            if result.is_ok and result.data is not None:
                report = getattr(result.data, "report_path", "")
                print(f"[自优化] 循环完成（report-only），报告: {report}")
            elif result.is_error:
                _logger.warning("自优化循环失败: %s", result.error)
        except Exception:
            _logger.warning("自优化循环执行异常", exc_info=True)

    # 2026-09-16 用户告知：后台周期对用户不可见——退出后进程看似已结束，
    # 实际还有自优化在跑（之前用户困惑"退出后哪来的进程"）。节流命中时
    # 也要明说"未到周期"，避免静默。
    min_hours = float(os.environ.get("LINGCLAUDE_DAEMON_MIN_INTERVAL_HOURS", "24"))
    try:
        _probe = _behavior_daemon
        will_run = _probe.should_run_cycle(min_interval_hours=min_hours) if _probe else True
    except Exception:
        will_run = True
    if will_run:
        print("[自优化] 已在后台启动自检周期（report-only，不影响退出；完成后打印报告路径）")
    else:
        print(f"[自优化] 距上次周期不足 {min_hours:.0f}h，本次跳过")
    threading.Thread(target=_run_cycle_bg, name="daemon-cycle-bg", daemon=True).start()


def _feed_behavior_to_daemon(engine: "QueryEngine", config: "lingclaudeConfig | None") -> None:
    # 审计#17 修复:此前每回合新建 OptimizationDaemon（构造含 SessionManager
    # 初始化/快照恢复扫描）——进程内缓存，消掉每轮固定 I/O 开销。
    global _behavior_daemon
    try:
        cfg = config or load_config(None)
        if _behavior_daemon is None:
            _behavior_daemon = OptimizationDaemon(target=".", config=cfg)
        daemon = _behavior_daemon
        metrics = engine.behavior_metrics
        daemon.update_behavior(metrics.to_dict())
        daemon.save_behavior_history({
            "total_turns": metrics.total_turns,
            "frustration_count": metrics.frustration_count,
            "corrections_received": metrics.corrections_received,
            "tool_error_count": metrics.tool_error_count,
        })
    except Exception:
        _logger.warning("行为数据写入守护进程失败", exc_info=True)

    try:
        engine.collect_daily_digest()
    except Exception:
        _logger.warning("情报采集失败", exc_info=True)



def _record_long_task_metrics(
    engine: QueryEngine,
    *,
    event: str,
    outcome: str,
    tool_calls: int = 0,
    tool_errors: int = 0,
    text_deltas: int = 0,
    turn_output_tokens: int | None = None,
    turn_input_delta: int | None = None,
    turn_duration_s: float | None = None,
    error: str | None = None,
) -> bool:
    """Append best-effort long-task observability to project-local JSONL.

    N5 守卫挂点: turn_output_tokens 传入**本轮**(非累计) output token 数时,
    在收尾点执行空响应 token 耗尽检测(0 text_delta + ≥0.95*max_tokens →
    WARNING, 连续 2 次升 ERROR + LingBus 告警)。守卫失败不影响指标写入。

    P1.1 schema 补齐: turn_output_tokens / turn_input_delta(本轮 usage
    快照差值) / turn_duration_s(流循环耗时) 为 **turn 级**字段;
    "usage" 沿用 engine 累计计数器 (历史语义不变)。turn 级字段在
    non-stream/事件型路径为 None — 审计读取时务必区分累计 vs 逐轮。
    """
    checkpoint_dir = Path(
        getattr(engine.session_store, "_checkpoint_dir", Path(".lingclaude/checkpoints"))
    )
    checkpoint_path = checkpoint_dir / f"{engine.session_id}.json"
    journal_path = Path(".lingclaude/journals") / f"{engine.session_id}.jsonl"
    stats = engine.get_stats()
    rss_mb = sample_rss_mb()  # N6: 每轮采样落盘, 供 RSS 曲线分析(告警之外的连续数据源)
    ok = append_long_task_metrics({
        "event": event,
        "outcome": outcome,
        "session_id": stats["session_id"],
        "turns": stats["turns"],
        "tool_calls": tool_calls,
        "tool_errors": tool_errors,
        "text_deltas": text_deltas,
        "turn_output_tokens": turn_output_tokens,
        "turn_input_delta": turn_input_delta,
        "turn_duration_s": turn_duration_s,
        "rss_mb": rss_mb,
        "journal_size_bytes": journal_path.stat().st_size if journal_path.exists() else 0,
        "checkpoint_exists": checkpoint_path.exists(),
        "checkpoint_size_bytes": checkpoint_path.stat().st_size if checkpoint_path.exists() else 0,
        "usage": stats.get("usage", {}),
        "error": error,
    })
    # N5 守卫: 指标已落盘, 检测失败也只吞掉（可观测性永不破坏主流程）
    if turn_output_tokens is not None:
        try:
            check_token_exhaustion(
                session_id=str(stats["session_id"]),
                text_deltas=text_deltas,
                turn_output_tokens=turn_output_tokens,
                max_tokens=resolve_max_tokens(engine),
                event=event,
            )
        except Exception:  # noqa: BLE001
            _logger.debug("N5 guard failed", exc_info=True)
    # N6 守卫: RSS 增长/硬限检测（同收尾点每轮采样, 基线语义见模块 docstring）
    try:
        # 告警日志已按级别(WARNING/ERROR)在 rss_watchdog 模块内输出, 此处只触发
        check_rss_watchdog(str(stats["session_id"]), event=event)
    except Exception:  # noqa: BLE001
        _logger.debug("N6 guard failed", exc_info=True)
    return ok



def _headless_turn(engine: QueryEngine, prompt: str, *, as_json: bool = False) -> int:
    """P1-4 headless（2026-09-21, opencode `run --print` / codex app-server 对齐）。

    与 `_single_turn` 的差异：
    - 无「思考中...」UI 装饰、无 watchdog 噪音、无交互；stdout 只出最终结果。
    - `as_json=False`：stdout 打印最终答案纯文本（一行/多行，CI 管道友好）。
    - `as_json=True`：stdout 打印单个 JSON 对象 `{content, usage, ok}`（机读）。

    复用引擎驱动逻辑（stream_call_model 事件流），但砍掉所有 UI 装饰——
    这是 headless 与交互模式共享同一循环实例（第 0 步 seam 接口化的直接收益）。
    """

    response_content = ""
    stream_error = False
    usage: dict[str, int] = {}
    for event in engine.stream_call_model(prompt):
        etype = event.get("type")
        if etype == "text_delta":
            response_content += event.get("text", "")
        elif etype == "done":
            response_content = event.get("content", response_content)
            usage = event.get("usage") or {}
        elif etype == "error":
            stream_error = True

    # 双写修复：正常 done 时 engine 已写 _messages，此处不重复
    if response_content:
        engine._compact_if_needed()

    if as_json:
        _json.dump({
            "ok": not stream_error,
            "content": response_content,
            "usage": usage,
        }, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(response_content)
        if not response_content.endswith("\n"):
            sys.stdout.write("\n")
    return 0


def _single_turn(engine: QueryEngine, prompt: str, verbose: bool = False) -> int:
    if engine._provider:
        sys.stdout.write("思考中...\r")
        sys.stdout.flush()
        response_content = ""
        got_first_token = False
        observed_tool_calls = 0
        observed_tool_errors = 0
        observed_text_deltas = 0
        observed_stream_error = False
        turn_output_tokens = 0  # N5: 本轮(非累计) output token, done 事件携带
        turn_t0 = time.monotonic()  # P1.1: turn 级耗时计时起点
        # 2026-09-17 双写修复: 流未到 done 即结束时（打断/异常）按 engine 未写处理
        turn_finalized = False
        usage_t0 = dict(engine.get_stats().get("usage") or {})  # P1.1: delta 基线
        # N5b: 流内停滞 watchdog — 旁路线程监视事件心跳，只告警不打断（详见模块 docstring）
        # 修复B（2026-09-21）: notify 注入 owned 通道，告警不再裸写 stderr
        _wd = StreamWatchdog(notify=_stream_write)
        _wd.start()
        try:
            for event in engine.stream_call_model(prompt):
                _wd.touch(str(event.get("type", "")))
                if not got_first_token and event.get("type") in ("text_delta", "error"):
                    got_first_token = True
                    sys.stdout.write(" " * 40 + "\r")  # UI 对齐:清 40 列
                    sys.stdout.flush()
                _handle_stream_event(event)
                if event.get("type") == "tool_call_start":
                    observed_tool_calls += 1
                if event.get("type") == "tool_call_end" and event.get("is_error"):
                    observed_tool_errors += 1
                if event.get("type") == "text_delta":
                    observed_text_deltas += 1
                    response_content += event.get("text", "")
                elif event.get("type") == "done":
                    response_content = event.get("content", response_content)
                    # 2026-09-17 双写修复: 记录 engine 是否已写 _messages 镜像
                    turn_finalized = bool(event.get("finalized", False))
                    turn_output_tokens = int(
                        (event.get("usage") or {}).get("output_tokens", 0) or 0
                    )
                if event.get("type") == "error":
                    observed_stream_error = True
        finally:
            _wd.stop()
        _flush_stream_line()  # P0:打断/异常退出时补冲残行,防污染下一轮
        if response_content:
            # 2026-09-17 双写修复: 正常 done 时 engine._finalize_turn 已写入
            # _messages 镜像（done.finalized=True），仅 engine 未写时兜底。
            if not turn_finalized:
                engine._messages.append(prompt)
                engine._messages.append(response_content)
            engine._compact_if_needed()
            # R5-fix: history 由 engine.stream_call_model 的 done 分支写入
            # (model_call.py)，CLI 层不重复调用 _append_to_session_history
        _record_long_task_metrics(
            engine,
            event="turn_complete",
            outcome="error" if observed_stream_error else "ok",
            tool_calls=observed_tool_calls,
            tool_errors=observed_tool_errors,
            text_deltas=observed_text_deltas,
            turn_output_tokens=turn_output_tokens,
            turn_input_delta=max(
                0,
                int((engine.get_stats().get("usage") or {}).get("input_tokens", 0) or 0)
                - int(usage_t0.get("input_tokens", 0) or 0),
            ),
            turn_duration_s=round(time.monotonic() - turn_t0, 3),
        )
        # P0-FOUNDATION: L5 审计接入主路径（streaming 路径原本完全绕过）
        # 非阻断：audit 方法 catch 一切异常，失败只打 warning 不影响返回值。
        # L5 内部调用链：should_trigger → run_l5_audit_full/_apply_l5_audit
        #   → T0 behavior check + T1 fact checker (orchestrator) + T3 entity conflict
        #   + L1/L2 handover/restart 检查，与 submission.py submit() 保持一致。
        try:
            from lingclaude.core.query_engine import _get_l5_auditor
            auditor = _get_l5_auditor(engine)
            if auditor._engine._l5_loop.should_trigger(prompt):
                auditor.run_l5_audit_full(prompt, response_content)
            else:
                auditor.apply_l5_audit(prompt, response_content)
            auditor.check_entity_conflict(prompt, response_content)
            auditor.check_l1_handover()
            auditor.check_l2_restart()
        except Exception:  # noqa: BLE001
            pass  # L5 失败不阻塞主流程，与 submission.py submit() 保持一致
    else:
        result = engine.submit(prompt)
        print(result.output)
    _feed_behavior_to_daemon(engine, None)
    if verbose:
        stats = engine.get_stats()
        bm = engine.behavior_metrics.to_dict()
        print_session_summary(SessionSummary(
            turns=stats["turns"],
            session_id=stats["session_id"],
            usage=stats["usage"],
            behavior=bm,
        ))
    return 0



def _maybe_recover_on_startup(engine: "QueryEngine", args: Any) -> None:
    """Surface an interrupted tool round at startup.

    `--recover` is explicit automation. `--continue` only announces the
    checkpoint because resume can execute more model/tool side effects.
    """
    if not getattr(args, "recover", False) and not getattr(args, "continue_", False):
        return
    if not engine.has_checkpoint:
        if getattr(args, "recover", False):
            _record_long_task_metrics(
                engine,
                event="startup_recover",
                outcome="no_checkpoint",
            )
            print("[无可恢复任务] 当前会话没有中断 checkpoint")
        return

    if getattr(args, "recover", False):
        result = engine.resume_interrupted()
        _record_long_task_metrics(
            engine,
            event="startup_recover",
            outcome="ok" if result.is_ok else "error",
            error=None if result.is_ok else result.error,
        )
        if result.is_ok:
            print(f"[已自动恢复中断任务] {result.data}")
        else:
            print(f"[自动恢复失败] {result.error}")
    elif get_output_format() == "plain":
        print("[检测到未完成工具轮] 输入 /recover 继续；下次启动可用 --recover 自动恢复")



def start_bus_responder_background(interval: float = 30.0) -> threading.Event:
    """RFC v0.1 §3 A1-1: 后台线程启动 BusResponder 监听 LingBus 任务。

    P4: 消费者循环已抽至 coordination/bus_consumer.py（供 CLI/API 复用），
    本函数保留为兼容壳转发，行为不变（线程名沿用原 lingclaude-bus-responder）。

    设计原则（沿用原实现）:
    - **不**用 BusResponder.run_loop()(它注册 SIGINT/SIGTERM,与主交互循环冲突)
    - **自定义 stop_event**,主进程退出前 .set() 触发线程停止
    - **catch 一切异常**,线程不能因为单次 poll 失败就退出
    """
    from lingclaude.coordination.bus_consumer import start_bus_consumer_background

    return start_bus_consumer_background(
        interval=interval,
        thread_name="lingclaude-bus-responder",
    )
