from __future__ import annotations

# H18 环境修复: 必须在 argparse/subprocess 使用前导入 — /dev/null 只读
# 沙箱下自动重写 os.devnull（subprocess.DEVNULL 等运行时动态读取）。
import lingclaude.core.devnull_compat  # noqa: F401  # noqa: E402

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import warnings
from pathlib import Path
from typing import Any

from lingclaude.cli.display import (
    QualityReport,
    SessionSummary,
    print_error,
    print_header,
    print_info,
    print_kv,
    print_metrics_stats,
    print_quality_report,
    print_session_summary,
    print_success,
    print_trend,
    print_warning,
    print_welcome,
)
from lingclaude.cli.interface import (
    create_session,
    FallbackSession,
    PromptSessionInterface,
    PromptToolkitSession,
)
from lingclaude.cli.long_task_metrics import append_long_task_metrics
from lingclaude.core.config import lingclaudeConfig, load_config
from lingclaude.core.query_engine import QueryEngine
from lingclaude.engine.coding import CodingRuntime
from lingclaude.self_optimizer.daemon import OptimizationDaemon, DaemonState

warnings.filterwarnings("ignore", category=SyntaxWarning)

_logger = logging.getLogger(__name__)


_behavior_daemon: OptimizationDaemon | None = None


def _maybe_run_daemon_cycle() -> None:
    """R2（系统论融入）：给断路 5 个月的自优化回路合闸。

    交互会话结束时触发一次 daemon 循环，24h 节流（should_run_cycle）；
    默认 report-only（_apply_params 不设 LINGCLAUDE_DAEMON_APPLY=1 不动
    config.yaml）。LINGCLAUDE_DAEMON_CYCLE=0 关闭（CI/测试用）。
    """
    if os.environ.get("LINGCLAUDE_DAEMON_CYCLE") == "0":
        return
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


def _feed_behavior_to_daemon(engine: QueryEngine, config: lingclaudeConfig | None) -> None:
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


def _get_version() -> str:
    # 修复(2026-09-05):此前只查包目录 VERSION(不存在)→ 恒显示硬编码 0.2.1,
    # 仓库根 VERSION 已升 0.5.0 却不生效。现两级查找:包目录 → 仓库根。
    candidates = (
        Path(__file__).resolve().parent.parent / "VERSION",
        Path(__file__).resolve().parents[2] / "VERSION",
    )
    for version_file in candidates:
        try:
            if version_file.exists():
                return version_file.read_text().strip()
        except OSError as e:
            _logger.debug("version file read failed: %s", e)
    return "0.5.0"


def _is_local_base(base_url: str) -> bool:
    """本地服务不需要 api_key — 与 task_router._is_local_base 同语义(F12a)。"""
    if not base_url:
        return False
    try:
        from urllib.parse import urlparse
        host = urlparse(base_url).hostname or ""
        return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
    except Exception:  # noqa: BLE001 — 解析失败按非本地处理
        return False


def _provider_status(engine: QueryEngine) -> str:
    """F12a:启动横幅诚实化 — 不再对缺 key 的云端 provider 谎报「已连接」。"""
    if engine._provider is None:
        return "未配置（回退模式）"
    cfg = getattr(engine._provider, "_config", None)
    api_key = str(getattr(cfg, "api_key", "") or "")
    base = str(getattr(cfg, "base_url", "") or "")
    if not api_key and not _is_local_base(base):
        return "已配置但缺 API key（云端调用将失败，请检查 config.yaml model.api_key）"
    return "已连接"


def _load_keys_env() -> None:
    """F12c:加载 ~/.ling_keys.env — proxy3 同源的 key 单点存放处。

    只在变量未设时注入,不覆盖用户 shell 已 export 的值。
    """
    path = Path.home() / ".ling_keys.env"
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        name, sep, value = line.partition("=")
        if not sep:
            continue
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and value and name not in os.environ:
            os.environ[name] = value


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config) if args.config else None)
    # 审计#4 修复:--bash-executor 必须在 CodingRuntime 创建前生效
    # （此前先建 runtime 再替换 config 且替换结果没人用 — 参数完全无效）。
    if args.bash_executor:
        from dataclasses import replace
        config = replace(config, engine=replace(config.engine, bash_executor_type=args.bash_executor))
    engine_result = QueryEngine.from_config_file(args.config)
    if engine_result.is_error:
        print(f"错误: {engine_result.error}")
        return 1
    engine = engine_result.data
    runtime = CodingRuntime(config)
    engine.set_runtime(runtime)

    # 审计#12 修复:--model 此前注册后零使用 — 接线为默认模型切换（不等价于
    # TaskRouter 的逐消息路由，只改默认 provider，见 /model 提示语）。
    if getattr(args, "model", None):
        result = engine.switch_model(args.model)
        if result.is_ok:
            print(f"[默认模型] {result.data}")
        else:
            print(f"[模型] 切换失败: {result.error}（继续用配置默认）")

    # RFC v0.1 §3 A1-1: 启动后台 BusResponder 监听 LingBus 任务
    # 族长 2026-08-27 决议:默认 = 1(生产开),pytest 环境通过 conftest.py 设为 0
    # 优先级:LINGCLAUDE_BUS_LISTENER 显式 = 0 > 默认开 > 显式 = 1(冗余兼容)
    # 审计#13 修复:仅交互模式启动 — 单轮/横幅进程几秒即退，daemon 线程会在
    # 任务执行中被硬杀，留下「已收到」却无结果的半截对话。
    if args.interactive and os.environ.get("LINGCLAUDE_BUS_LISTENER") != "0":
        _bus_responder_stop = _start_bus_responder_background()
        import atexit
        atexit.register(_bus_responder_stop.set)

    # P0-2: 输出格式 + 会话续接（--continue 最近会话 / --resume <id>）
    global _OUTPUT_FORMAT
    _OUTPUT_FORMAT = getattr(args, "output_format", "plain") or "plain"

    resume_id = getattr(args, "resume", None)
    if resume_id is None and getattr(args, "continue_", False):
        sessions = engine.session_manager.list_sessions()
        if sessions:
            latest = max(sessions, key=lambda s: s.get("created_at", ""))
            resume_id = latest.get("session_id")
            if _OUTPUT_FORMAT == "plain":
                print(f"[--continue] 最近会话: {resume_id}")
        elif _OUTPUT_FORMAT == "plain":
            print("[--continue] 没有可恢复的历史会话，从新会话开始")
    if resume_id:
        if engine._session_persister.load_session(resume_id):
            if _OUTPUT_FORMAT == "plain":
                print(f"[会话已恢复] {resume_id}（{len(engine._conversation)} 轮对话）")
        elif _OUTPUT_FORMAT == "plain":
            print(f"[会话恢复失败] {resume_id} 不存在或已损坏，从新会话开始")

    _maybe_recover_on_startup(engine, args)

    if args.prompt:
        if args.interactive:
            return _interactive_loop(engine, args.prompt)
        return _single_turn(engine, args.prompt, args.verbose)
    elif args.interactive:
        return _interactive_loop(engine, None)
    else:
        version = _get_version()
        provider_status = _provider_status(engine)
        print_welcome(version, provider_status, f"{config.model.provider}/{config.model.model}", len(runtime.registry.list_tools()))
    return 0


def _single_turn(engine: QueryEngine, prompt: str, verbose: bool = False) -> int:
    print(f"灵克> {prompt}")
    if engine._provider:
        sys.stdout.write("思考中...\r")
        sys.stdout.flush()
        response_content = ""
        got_first_token = False
        observed_tool_calls = 0
        observed_tool_errors = 0
        observed_text_deltas = 0
        observed_stream_error = False
        for event in engine.stream_call_model(prompt):
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
            if event.get("type") == "error":
                observed_stream_error = True
        _flush_stream_line()  # P0:打断/异常退出时补冲残行,防污染下一轮
        if response_content:
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
        )
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


def _record_long_task_metrics(
    engine: QueryEngine,
    *,
    event: str,
    outcome: str,
    tool_calls: int = 0,
    tool_errors: int = 0,
    text_deltas: int = 0,
    error: str | None = None,
) -> bool:
    """Append best-effort long-task observability to project-local JSONL."""
    checkpoint_dir = Path(
        getattr(engine.session_store, "_checkpoint_dir", Path(".lingclaude/checkpoints"))
    )
    checkpoint_path = checkpoint_dir / f"{engine.session_id}.json"
    journal_path = Path(".lingclaude/journals") / f"{engine.session_id}.jsonl"
    stats = engine.get_stats()
    return append_long_task_metrics({
        "event": event,
        "outcome": outcome,
        "session_id": stats["session_id"],
        "turns": stats["turns"],
        "tool_calls": tool_calls,
        "tool_errors": tool_errors,
        "text_deltas": text_deltas,
        "journal_size_bytes": journal_path.stat().st_size if journal_path.exists() else 0,
        "checkpoint_exists": checkpoint_path.exists(),
        "checkpoint_size_bytes": checkpoint_path.stat().st_size if checkpoint_path.exists() else 0,
        "usage": stats.get("usage", {}),
        "error": error,
    })


def _maybe_recover_on_startup(engine: QueryEngine, args: argparse.Namespace) -> None:
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
    elif _OUTPUT_FORMAT == "plain":
        print("[检测到未完成工具轮] 输入 /recover 继续；下次启动可用 --recover 自动恢复")


def _start_bus_responder_background(interval: float = 30.0) -> threading.Event:
    """RFC v0.1 §3 A1-1: 后台线程启动 BusResponder 监听 LingBus 任务。

    设计原则:
    - **不**用 BusResponder.run_loop()(它注册 SIGINT/SIGTERM,与主交互循环冲突)
    - **自定义 stop_event**,主进程退出前 .set() 触发线程停止
    - **catch 一切异常**,线程不能因为单次 poll 失败就退出
    - 端口不可达/数据库锁 等临时性错误,记录后继续轮询
    """
    from lingclaude.coordination.bus_responder import BusResponder

    stop_event = threading.Event()

    def _background_loop() -> None:
        try:
            responder = BusResponder()
        except Exception as e:  # noqa: BLE001 — 初始化失败不能阻塞主循环
            _logger.error("BusResponder init failed, skip background polling: %s", e)
            return

        _logger.info("BusResponder background thread started (interval=%.0fs)", interval)
        while not stop_event.is_set():
            try:
                responder.poll_and_respond()
            except Exception as e:  # noqa: BLE001 — 单次失败不退出线程
                _logger.error("BusResponder background poll error: %s", e)
            # 周期 wait + 提前唤醒(可选)
            if stop_event.wait(timeout=interval):
                break
        _logger.info("BusResponder background thread stopped")

    thread = threading.Thread(
        target=_background_loop,
        name="lingclaude-bus-responder",
        daemon=True,  # 主进程退出时强制终止,避免孤儿线程
    )
    thread.start()
    return stop_event


def _esc_pressed() -> bool:
    """T1-7: 非阻塞检测 Esc(0x1b) 按键。POSIX select + tty 原始模式，超时 0.05s。"""
    try:
        import select
        import termios

        if not sys.stdin.isatty():
            return False
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            # 修复:此前 tty.setraw 会清除 OPOST(输出后处理),而本线程生成期间
            # 每 50ms 循环进出 raw 模式 → 流式输出大多落在 OPOST 关闭窗口,
            # 终端收到裸 LF 不回车 → "空格逐行累加"阶梯缩进。
            # 只关 ICANON/ECHO(非阻塞读单字节所需),保留 OPOST/ISIG:
            # \n 仍被内核转 \r\n,Ctrl+C 中断语义不变。
            new = [list(x) if isinstance(x, list) else x for x in old]
            new[3] &= ~(termios.ICANON | termios.ECHO)  # lflag
            new[6][termios.VMIN] = 0
            new[6][termios.VTIME] = 0
            termios.tcsetattr(fd, termios.TCSANOW, new)
            readable, _, _ = select.select([fd], [], [], 0.05)
            if readable:
                import os

                ch = os.read(fd, 1)
                return ch == b"\x1b"
            return False
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:  # noqa: BLE001 — 非 tty/无 termios 时静默禁用 Esc 打断
        return False


def _esc_listen_loop(session: PromptSessionInterface, stop: threading.Event) -> None:
    """Step 4: 后台线程监听 Esc → set interrupt_event（生成态 stdin 空闲）。

    审计#6 修复:此前退出条件只有 interrupt_event — 流结束后上层立刻 clear()
    把它"复活"，没按过 Esc 的线程永不退出 → 线程逐轮堆积且持续抢 stdin
    （setraw/read 吞掉 prompt_toolkit 正在读的键入字符）。增加 per-turn
    stop 事件，回合结束必退。
    """
    while not stop.is_set() and not session.interrupt_event().is_set():
        if _esc_pressed():
            session.interrupt_event().set()
            break


# P0-行缓冲:跨事件聚合流式 delta,残行待下次事件或 flush 收尾
_stream_line_buf: list[str] = []
_stream_lines_emitted = 0  # 本轮已输出行数(done 时用于 ANSI 擦除重渲染)
_OUTPUT_FORMAT = "plain"  # P0-2: plain | json | jsonl
_json_event_buffer: list[dict[str, Any]] = []  # json 模式事件缓冲


def _flush_stream_line() -> None:
    """P0-行缓冲:输出聚合中的不完整行(补换行),清空缓冲。"""
    if _stream_line_buf:
        text = "".join(_stream_line_buf)
        _stream_line_buf.clear()
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
        globals()["_stream_lines_emitted"] += 1


def _handle_stream_event(event: dict[str, Any]) -> None:
    # P0-行缓冲:流式 delta 按行聚合,只在行边界输出 — 根治半截表格行与
    # 多写入者交错导致的"空格逐行累加"错位(对齐 atomcode UiLine 语义行)。
    etype = event.get("type")
    if _OUTPUT_FORMAT in ("json", "jsonl"):
        # P0-2: 机器可读输出。jsonl = 每事件一行；json = 缓冲,done 时汇总单对象。
        payload = {k: v for k, v in event.items() if k != "content"} if _OUTPUT_FORMAT == "jsonl" else None
        if _OUTPUT_FORMAT == "jsonl":
            print(json.dumps({"type": etype, **({"text": event["text"]} if "text" in event else {}), **(payload or {})},
                             ensure_ascii=False), flush=True)
            return
        _json_event_buffer.append(event)
        if etype == "done":
            print(json.dumps({
                "type": "done",
                "content": event.get("content", ""),
                "events": [{"type": e.get("type"), **({"text": e["text"]} if "text" in e else {})}
                           for e in _json_event_buffer],
            }, ensure_ascii=False), flush=True)
            _json_event_buffer.clear()
        elif etype == "error":
            _json_event_buffer.clear()
        return
    if etype == "text_delta":
        _stream_line_buf.append(event["text"])
        # 聚合后一次性切行:完整行立即输出,残行留缓冲
        pending = "".join(_stream_line_buf)
        _stream_line_buf.clear()
        while "\n" in pending:
            line, _, pending = pending.partition("\n")
            sys.stdout.write(line + "\n")
            globals()["_stream_lines_emitted"] += 1
        if pending:
            _stream_line_buf.append(pending)
        sys.stdout.flush()
    elif etype == "tool_call_start":
        _flush_stream_line()
        name = event.get("name", "?")
        args = event.get("arguments", "")
        try:
            parsed = json.loads(args)
            if isinstance(parsed, dict):
                args_preview = " ".join(f"{k}={v}" for k, v in list(parsed.items())[:3])
            else:
                args_preview = str(parsed)[:60]
        except (json.JSONDecodeError, TypeError, AttributeError):
            args_preview = args[:60] if isinstance(args, str) else str(args)[:60]
        sys.stdout.write(f"\n  [{name}] {args_preview} ... ")
        sys.stdout.flush()
    elif etype == "tool_call_end":
        is_error = event.get("is_error", False)
        preview = event.get("output_preview", "")
        mark = "❌" if is_error else "✅"
        if preview and not is_error:
            # 终端宽度动态截断（替代固定 80 字符）——窄终端不低于 60，
            # 宽终端用 columns-8 留余白；非 TTY 回落 80
            try:
                cols = max(60, (shutil.get_terminal_size().columns or 80) - 8) if sys.stdout.isatty() else 80
            except Exception:  # noqa: BLE001
                cols = 80
            preview = preview[:cols].replace("\n", " ")
            sys.stdout.write(f"{mark} ({len(preview)} chars)\n")
        else:
            sys.stdout.write(f"{mark}\n")
        sys.stdout.flush()
    elif etype == "status":
        _flush_stream_line()
        sys.stdout.write(f"\n  [{event.get('message', '')}] ")
        sys.stdout.flush()
    elif etype == "done":
        _flush_stream_line()
        # P0-完成渲染:TTY 下擦除裸文本行,用 rich Markdown 重渲染正式版
        # 2026-09-06 修复:之前用 `\x1b[{n}A` 上移 N 行 + 清屏重渲染,但 prompt_toolkit
        # 同步维护自己的 bottom_toolbar(N 不含 toolbar 行)→ cursor 操作覆盖了 toolbar
        # 文字("灵克[..]ude │ 上下文 0%"被截成 ude)、产生位移。
        # 新策略:TTY 下不再用 ANSI cursor 操作(保留 raw stream 输出),改用 Rich 的
        # `erase + replace` 在底部追加正式版,而非覆盖——避免与 PT 的 toolbar 控制权冲突。
        content = event.get("content", "")
        if content and sys.stdout.isatty():
            try:
                from lingclaude.cli.display import print_markdown

                # 用 ANSI 光标下移一行(到达 stream 输出末尾之下),再向上滚回渲染
                # ——比上移 N 行覆盖安全(N 不必精确)
                sys.stdout.write("\x1b[1B\n")  # 下移 1 行落到 stream 末尾
                print_markdown(content)
                sys.stdout.write("\n")
            except Exception:  # noqa: BLE001 — Markdown 渲染失败时保底输出纯文本
                sys.stdout.write("\n" + content + "\n\n")
            globals()["_stream_lines_emitted"] = 0  # 复位：P0 完成行已就位
        else:
            sys.stdout.write("\n\n")
        sys.stdout.flush()
    elif etype == "error":
        _flush_stream_line()
        # UI 对齐修复:前后各留空行,与 rich stderr 日志/下一提示符隔离。
        sys.stdout.write(f"\n\n[错误] {event.get('error', '')}\n")
        sys.stdout.write("提示: 请检查网络连接，或在 config.yaml 中确认 model.api_key 已设置\n\n")
        sys.stdout.flush()


def _interactive_loop(engine: QueryEngine, first_prompt: str | None) -> int:
    version = _get_version()
    if _OUTPUT_FORMAT == "plain":
        print(f"灵克 v{version} — 交互模式（'exit'/'quit'/Ctrl+D 退出，Ctrl+C 清行）")
        print(f"Provider: {_provider_status(engine)}")
        print()

    # F12e:TTY 终端状态保护 — 进入时保存 termios,退出时恢复。
    # 现场症状:prompt_toolkit 在不支持 CPR 的终端上退出后 termios/VT 状态残留
    # → Ctrl+C 被回显为 ^C 字符且输出错位。退出路径显式恢复兜底。
    _saved_termios: Any = None
    if sys.stdin.isatty():
        try:
            import termios
            _saved_termios = termios.tcgetattr(sys.stdin.fileno())
        except Exception:  # noqa: BLE001 — 无 termios 平台静默跳过
            _saved_termios = None

    def _restore_tty() -> None:
        if _saved_termios is None:
            return
        try:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _saved_termios)
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:  # noqa: BLE001 — fd 已关等场景静默
            pass

    # RFC §3.3: I/O 抽象层 — LINGCLAUDE_CLI_MODE=plain 或非 TTY → FallbackSession
    # Step 3: Tab 补全 7 斜杠命令（prompt_toolkit WordCompleter）
    _completer: Any = None
    try:
        from prompt_toolkit.completion import WordCompleter

        _completer = WordCompleter(
            # F2 修复:删 /undo 出补全 — handler 缺失,留 completer 会让用户以为已实现。
            # 会话恢复:新增 /resume /continue（交互中途恢复历史会话）；
            # 顺手补上帮助文本里有、补全里却漏掉的 /lsp。
            ["/help", "/clear", "/compact", "/model", "/schedule", "/lsp", "/resume", "/continue", "/checkpoint", "/recover", "/quit"],
            ignore_case=True,
        )
    except ImportError:
        _completer = None
    session: PromptSessionInterface = create_session(completer=_completer)

    # H17-TUI: 状态栏 + 挂起队列接线 — 设计文档 docs/cli/TUI_BOTTOM_INPUT_DESIGN.md
    # 组件（status.py/input_queue.py/interface.py）此前已就绪但从未被接线。
    # 三件套在此构造；仅 TTY+plain 生效，json/jsonl/Fallback 自动降级。
    from lingclaude.cli.input_queue import InputPump, InputQueue
    from lingclaude.cli.status import StatusModel, toolbar_fragments

    status = StatusModel()
    status.refresh_cwd()
    _prov_cfg0 = getattr(engine._provider, "_config", None) if engine._provider else None
    status.set_model(str(getattr(_prov_cfg0, "model", "") or "?"))
    try:
        from lingclaude.core.tool_executor import _estimate_message_tokens

        status.set_ctx(
            _estimate_message_tokens(engine._messages),
            int(getattr(engine.config, "context_window_tokens", None)
                or getattr(engine.config, "max_budget_tokens", 0) or 0),
        )
    except Exception:  # noqa: BLE001 — token 估算失败不阻塞交互启动
        pass
    input_queue = InputQueue()

    def _status_prompt() -> str:
        """prompt 渲染回调：plain 模式显示简短的"灵克>"（保留可读性），
        状态信息走 bottom_toolbar（已有 toolbar_fragments 实现）。

        为什么不在 prompt 里塞 [model|ctx|task|q]？实测发现：
        1) prompt 文本变长后 PT 计算光标位置会偏移 → 输入位置错位
        2) 多余方括号与 | 字符 → 显示多余空格 + 换行错位
        3) 上下文 window=0 时 ctx 总是 ? → 失去常驻感
        把状态信息下沉到底部 toolbar 后,prompt 简短可读,光标稳定。
        """
        if _OUTPUT_FORMAT != "plain":
            return "灵克> "
        # 即便 prompt 简短,仍要刷新 ctx_tokens（每次 session.prompt 都重算）——
        # 否则状态栏比例永远停在上次 _blocks 触发时的旧值
        try:
            from lingclaude.core.tool_executor import _estimate_message_tokens

            status.set_ctx(
                _estimate_message_tokens(engine._messages),
                int(getattr(engine.config, "context_window_tokens", None)
                    or getattr(engine.config, "max_budget_tokens", 0) or 0),
            )
        except Exception:  # noqa: BLE001
            pass
        status.refresh_cwd()
        status.set_pending(input_queue.pending())
        return "灵克> "

    input_pump = InputPump(session, input_queue, prompt_text=_status_prompt)

    def _read_input() -> str:
        try:
            text = session.prompt(_status_prompt())
            # 审计#11 修复:prompt_toolkit 的 PromptSession 已自动写 FileHistory,
            # 这里再 push 一次导致 ~/.lingclaude/history 每条重复。
            # push_to_history 语义改为「仅兜底实现需要手动记」→ 见 interface.py。
            if text.strip():
                session.push_to_history(text)
            return text
        except UnicodeDecodeError:
            sys.stdin.buffer.readline()
            print("[输入编码错误，请检查终端编码设置]")
            return ""

    def _drain_pending_notice() -> None:
        """H17-TUI:退出前清空挂起队列并列出被丢弃项（半成品不执行、不落盘）。"""
        dropped = input_queue.drain()
        if dropped:
            print(f"[退出] 丢弃 {len(dropped)} 条挂起输入：")
            for d in dropped[:5]:
                print(f"  - {d[:60]}")
            if len(dropped) > 5:
                print(f"  … 等共 {len(dropped)} 条")

    quit_requested = False
    # H17-TUI 架构定稿: stdin 读者唯一化 — pump 会话级独占 session.prompt()，
    # 主循环只从队列取输入。此前「逐轮启停」模型下 stop() 无法中断阻塞在
    # prompt() 的 pump 线程，主循环再入 prompt() → 同一 PT Application 并发
    # 运行 → AssertionError: Application is already running（2026-09-08 事故）。
    _pump_mode = False

    def _next_input() -> str:
        """pump 模式取输入：唯一来源是队列（EOF 哨兵转 EOFError）。
        pump 线程死亡时永久降级为阻塞直读。"""
        while True:
            if input_pump.dead or not _pump_mode:
                return _read_input()
            item = input_queue.get(timeout=0.3)
            if item is None:
                # 队列空且 pump 线程已退出（未置 dead）→ EOF 已入队/线程异常退出
                if not input_pump.is_alive():
                    item = input_queue.get(timeout=0.5)
                    if item is None:
                        raise EOFError
                continue
            if InputQueue.is_eof(item):
                raise EOFError
            return item

    # H17-TUI 步骤2: 安装状态栏（PT 实现生效；Fallback no-op 自动降级）。
    # TUI_ONLY_SNAPSHOT: json/jsonl 输出模式禁用（状态栏与流式 stdout 无关但
    # 保持机器输出纯净）；同时每轮 refresh_cwd 对齐 /cd 场景。
    _status_bar_active = False
    if _OUTPUT_FORMAT == "plain":
        def _status_refresh() -> None:
            status.refresh_cwd()
            _prov_cfg = getattr(engine._provider, "_config", None) if engine._provider else None
            if _prov_cfg is not None:
                m = getattr(_prov_cfg, "model", "")
                if m:
                    status.set_model(str(m))
            # Update pinned status
            pinned = engine.get_pinned_model_name()
            if pinned:
                status.set_model(f"{pinned} [PINNED]")
                status.set_pinned(True)
            else:
                status.set_pinned(False)
            try:
                from lingclaude.core.tool_executor import _estimate_message_tokens

                status.set_ctx(
                    _estimate_message_tokens(engine._messages),
                    int(getattr(engine.config, "context_window_tokens", None)
                        or getattr(engine.config, "max_budget_tokens", 0) or 0),
                )
            except Exception:  # noqa: BLE001
                pass
            status.set_pending(input_queue.pending())

        try:
            session.install_bottom_toolbar(lambda: toolbar_fragments(status.snapshot()))
            _status_bar_active = not isinstance(session, FallbackSession)
        except Exception:  # noqa: BLE001 — 状态栏安装失败不阻塞交互
            _status_bar_active = False

        # H17-TUI: pump 会话级启动 — 唯一 stdin 读者（架构见 _next_input 注释）。
        # 仅 PT 会话 + TTY + plain 生效；json/jsonl/Fallback 自动降级阻塞直读。
        if (
            _status_bar_active
            and isinstance(session, PromptToolkitSession)
            and sys.stdin.isatty()
        ):
            _pump_mode = True
            input_pump.start()

    def _handle_slash_command(cmd: str) -> bool:
        """T1-7: 斜杠命令。返回 True 表示已消费；/quit /exit 置 quit_requested。"""
        nonlocal quit_requested
        parts = cmd.strip().split(maxsplit=1)
        if not parts or parts[0][:1] != "/":
            return False
        name = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        # 审计#3 修复:/quit /exit 此前无 handler → 落成用户消息发给 LLM，
        # 且补全列表还在引导用户输入（F2 删 /undo 时漏掉的同类问题）。
        if name in ("/quit", "/exit"):
            quit_requested = True
            return True
        if name in ("/help", "/?"):
            print("[斜杠命令]")
            print("  /help                  本帮助")
            print("  /clear                 清空会话上下文")
            print("  /compact               手动压缩（未达阈值时明确提示）")
            print("  /model [名称]          查看/钉住模型（--unpin 解除；--ttl N 秒后自动恢复路由）")
            print("  /schedule [表达式]      定时任务注册/列出/取消")
            print("  /lsp add|remove [参数]  LSP 服务器注册/删除（不带参数列出）")
            print("  /checkpoint             手动保存 checkpoint（R5 阶段1）")
            print("  /recover                恢复最近中断的工具轮 checkpoint")
            print("  /resume [ID]           恢复指定会话（不带 ID 列出全部；ID 支持短前缀）")
            print("  /continue              恢复最近一次会话（等价启动参数 --continue）")
            print("  /quit、/exit           退出")
            return True
        if name == "/clear":
            engine._messages.clear()
            engine._conversation.clear()
            print("[会话已清空]")
            return True
        if name == "/compact":
            # 审计#9 修复:此前无论是否达阈值都谎报「已触发压缩」— 实际多数
            # 时候 _compact_if_needed 内部条件不满足、什么都没做。
            from lingclaude.core.tool_executor import _estimate_message_tokens

            msgs = engine._messages
            msg_limit = engine.config.compact_after_turns * 2
            token_threshold = int(engine.config.max_budget_tokens * 0.8)
            est_tokens = _estimate_message_tokens(msgs)
            if len(msgs) > msg_limit or est_tokens > token_threshold:
                engine._compact_if_needed()
                print(f"[已压缩] {len(msgs)} 条消息（阈值 {msg_limit} 条 / {token_threshold} tok）")
            else:
                print(f"[未压缩] 未达阈值：{len(msgs)}/{msg_limit} 条消息，~{est_tokens}/{token_threshold} tokens")
                print("[提示] 达到阈值后回合结束自动压缩；/compact 仅用于手动提前触发")
            return True
        if name == "/model":
            # /model: 显示当前/钉住模型；/model <name> 钉住模型（绕过 TaskRouter）；/model --unpin 解除钉住
            parts = arg.strip().split() if arg else []
            # /model --unpin
            if parts and parts[0] == "--unpin":
                result = engine.unpin_model()
                if result.is_ok:
                    print(f"[已解除钉住] 恢复 TaskRouter 动态路由（原钉住: {result.data}）")
                    status.set_pinned(False)
                else:
                    print(f"[解除失败] {result.error}")
                return True
            # /model <name> [--ttl N]
            if parts:
                model_name = parts[0]
                ttl = 0
                if "--ttl" in parts:
                    try:
                        ttl_idx = parts.index("--ttl")
                        ttl = int(parts[ttl_idx + 1])
                    except (IndexError, ValueError):
                        print("[用法] /model <name> [--ttl 秒数]")
                        return True
                result = engine.pin_model(model_name, ttl_seconds=ttl)
                if result.is_ok:
                    pinned_cfg = engine._pinned_model_config
                    base = getattr(pinned_cfg, "base_url", "?") or "?"
                    ttl_str = f" (TTL {ttl}s)" if ttl > 0 else " (会话级永久)"
                    print(f"[已钉住模型] {result.data} @ {base}{ttl_str}")
                    print("[提示] 后续请求将强制使用此模型，忽略 TaskRouter 路由表；用 /model --unpin 解除")
                    status.set_pinned(True)
                else:
                    print(f"[钉住失败] {result.error}")
                return True
            # /model (无参数): 显示当前/钉住状态
            pinned = engine.get_pinned_model_name()
            if pinned:
                pinned_cfg = engine._pinned_model_config
                base = getattr(pinned_cfg, "base_url", "?") if pinned_cfg else "?"
                import time
                ttl_remain = int(engine._pinned_model_expires - time.time()) if engine._pinned_model_expires != float('inf') else -1
                ttl_str = f" (剩余 {ttl_remain}s)" if ttl_remain > 0 else (" (会话级)" if ttl_remain < 0 else " (已过期，自动解除)")
                print(f"[当前钉住模型] {pinned} @ {base}{ttl_str}")
                print("[提示] 使用 /model --unpin 解除钉住，恢复动态路由")
            else:
                prov_cfg = getattr(engine._provider, "_config", None) if engine._provider else None
                model_name = getattr(prov_cfg, "model", "") or "?"
                base = getattr(prov_cfg, "base_url", "") or "?"
                print(f"[当前默认模型] {model_name} @ {base}")
                print("[提示] /model <name> 钉住模型（绕过 TaskRouter）；/model --unpin 解除")
                router = getattr(engine, "_task_router", None)
                if router is not None and router._providers:
                    print("[可用模型] 按 provider 分组：")
                    for pname, pinfo in router._providers.items():
                        if not pinfo.models:
                            continue
                        key_mark = "✓" if pinfo.api_key else "✗(无key)"
                        print(f"  {pname} [{key_mark}] ({pinfo.base_url}):")
                        for m in pinfo.models:
                            default_tag = " ←默认" if m == pinfo.default_model else ""
                            print(f"    {m}{default_tag}")
                else:
                    print("[可用模型] TaskRouter 未加载或无 provider")
            return True
        # T2-2/案 4: /schedule 命令 — 定时任务注册/列出/取消
        if name == "/schedule":
            from lingclaude.core.scheduler import ScheduleType, get_schedule_manager

            mgr = get_schedule_manager()
            if not arg:
                # 列出任务
                tasks = mgr.list_tasks()
                if not tasks:
                    print("[定时任务] 无任务")
                else:
                    print(f"[定时任务] 共 {len(tasks)} 个：")
                    for t in tasks:
                        print(f"  - {t.task_id[:8]} | {t.cron} | {t.query[:40]}")
            elif arg.startswith("cancel "):
                # 取消任务
                task_id = arg[7:].strip()
                if mgr.cancel(task_id):
                    print(f"[已取消] {task_id}")
                else:
                    print(f"[未找到] {task_id}")
            else:
                # 注册任务：/schedule @daily "查询内容"
                parts = arg.split(maxsplit=1)
                if len(parts) < 2:
                    # 审计#10 修复:补上 scheduler 实际支持的 after:N / at:HH:MM
                    # 接线门修复: ScheduleType 枚举作为帮助文本单一事实来源，
                    # 避免文档与 _compute_next_run 解析器漂移
                    predefs = "|".join(
                        t.value for t in ScheduleType if t is not ScheduleType.INTERVAL
                    )
                    print(f'[用法] /schedule {predefs}|{ScheduleType.INTERVAL.value}:N|after:N|at:HH:MM "任务内容"')
                    print("       /schedule cancel <task_id>")
                    print("       /schedule  (列出任务)")
                else:
                    cron, query = parts
                    try:
                        task_id = mgr.register(cron, query)
                        print(f"[已注册] {task_id[:8]} | {cron} | {query[:40]}")
                    except ValueError as e:
                        print(f"[错误] {e}")
            return True
        if name == "/lsp":
            # lsp add/list/remove — 注册 LSP server 配置（对标 Crush `lsp add`）
            from lingclaude.engine.lsp_registry import list_servers, register, remove

            if not arg:
                print("[LSP 服务器] 内置 + 自定义：")
                for s in list_servers():
                    mark = "内置" if s.get("default") else "自定义"
                    print(f"  - {s['lang']:<12} {s['command']} {(' '.join(s.get('args', [])))} [{mark}]")
                print("用法: /lsp add <lang> --command <cmd> [--args 'a b'] | /lsp remove <lang>")
            elif arg.startswith("add "):
                rest = arg[4:].strip()
                # 审计#8 修复:手册示例用 Crush 风格 `/lsp add rust rust-analyzer`，
                # 旧实现只认 --command 语法 → 示例全部失效。两种语法都支持；
                # --args 引号改用 shlex 解析（此前 .split() 会留下残引号）。
                command = ""
                args: list[str] = []
                if "--command" in rest:
                    import shlex

                    lang_part, _, after = rest.partition("--command")
                    lang = lang_part.strip()
                    after = after.strip()
                    if "--args" in after:
                        cmd_str, _, args_str = after.partition("--args")
                        cmd_tokens = shlex.split(cmd_str.strip())
                        command = cmd_tokens[0] if cmd_tokens else ""
                        args = shlex.split(args_str.strip())
                    else:
                        cmd_tokens = shlex.split(after)
                        if cmd_tokens:
                            command, args = cmd_tokens[0], cmd_tokens[1:]
                else:
                    import shlex

                    tokens = shlex.split(rest)
                    if len(tokens) >= 2:
                        lang, command, args = tokens[0], tokens[1], tokens[2:]
                    else:
                        lang = tokens[0] if tokens else ""
                if not command or not lang:
                    print('[用法] /lsp add <lang> <command> [args...]（Crush 风格）')
                    print("       /lsp add <lang> --command <cmd> [--args 'a b']")
                else:
                    try:
                        reg = register(lang, command, args)
                        print(f"[已注册] {reg['lang']} → {reg['command']} {' '.join(reg['args'])}")
                    except ValueError as e:
                        print(f"[错误] {e}")
            elif arg.startswith("remove "):
                lang = arg[7:].strip()
                if remove(lang):
                    print(f"[已删除] {lang}")
                else:
                    print(f"[无法删除] {lang}（内置默认不可删，或不存在）")
            else:
                print("[用法] /lsp add <lang> --command <cmd> | /lsp remove <lang> | /lsp 列出")
            return True
        # R5 阶段1: /checkpoint 手动保存当前会话 checkpoint + journal
        if name == "/checkpoint":
            persist_result = engine._session_persister.persist_session()
            if persist_result.is_ok:
                engine._get_journal().append("checkpoint", {
                    "manual": True,
                    "messages_count": len(engine._messages),
                })
                print(f"[checkpoint] 已保存: {persist_result.data}")
            else:
                print(f"[checkpoint] 保存失败: {persist_result.error}")
            return True

        # R5 阶段1 的核心 API 此前没有 CLI 入口：进程被杀/中断后，
        # /resume 只能恢复历史会话，不能从中断工具轮继续。
        if name == "/recover":
            status.set_task("恢复中")
            try:
                result = engine.resume_interrupted()
            finally:
                status.set_task("空闲")
            _record_long_task_metrics(
                engine,
                event="slash_recover",
                outcome="ok" if result.is_ok else ("no_checkpoint" if result.code == "NO_CHECKPOINT" else "error"),
                error=None if result.is_ok else result.error,
            )
            if result.is_ok:
                print(f"[已恢复中断任务] {result.data}")
            elif result.code == "NO_CHECKPOINT":
                print("[无可恢复任务] 当前会话没有中断 checkpoint")
            else:
                print(f"[恢复失败] {result.error}")
            return True

        # 会话恢复:/resume [ID] 恢复指定会话、/continue 恢复最近一次。
        # 复用启动参数 --resume/--continue 的同一套持久化接口（load_session），
        # 语义与启动时一致：恢复 = 替换当前上下文。
        if name in ("/resume", "/continue"):
            sessions = engine.session_manager.list_sessions()
            if name == "/continue":
                # /continue = 无参恢复最近一次（对齐启动参数 --continue）
                if not sessions:
                    print("[恢复失败] 没有可恢复的历史会话")
                    return True
                latest = max(sessions, key=lambda s: s.get("created_at", ""))
                target_id = str(latest.get("session_id", ""))
                print(f"[目标] 最近会话: {target_id}")
            elif not arg:
                # /resume 无参 = 列出全部（对齐帮助文本承诺；这是会话 ID 的发现入口）
                if not sessions:
                    print("[会话列表] 无历史会话")
                    return True
                print(f"[会话列表] 共 {len(sessions)} 个（最近 10 个）：")
                for s in sorted(sessions, key=lambda x: x.get("created_at", ""), reverse=True)[:10]:
                    print(f"  - {s.get('session_id')} | {s.get('created_at', '')}")
                return True
            else:
                target_id = arg.strip()
                # 短前缀匹配（对齐 /schedule cancel 用 8 位短 ID 的习惯）
                matches = [s for s in sessions if str(s.get("session_id", "")).startswith(target_id)]
                if len(matches) == 1:
                    target_id = str(matches[0]["session_id"])
                elif len(matches) > 1:
                    print(f"[歧义] 有 {len(matches)} 个会话以 {target_id} 开头，请用更长的 ID：")
                    for s in matches[:5]:
                        print(f"  - {s.get('session_id')} | {s.get('created_at', '')}")
                    return True
            if engine._session_persister.load_session(target_id):
                print(f"[会话已恢复] {target_id}（{len(engine._conversation)} 轮对话；当前上下文已被替换）")
            else:
                print(f"[会话恢复失败] {target_id} 不存在或已损坏（当前上下文未受影响）")
            return True
        return False

    prompt = first_prompt or ""
    while True:
        if not prompt:
            try:
                prompt = _next_input().strip()
            except (EOFError, KeyboardInterrupt):
                _drain_pending_notice()
                print("\n再见！")
                break
        if prompt.lower() in ("exit", "quit", "q"):
            _drain_pending_notice()
            print("再见！")
            break
        if not prompt:
            continue
        # T1-7: 斜杠命令优先消费
        if _handle_slash_command(prompt):
            if quit_requested:
                _drain_pending_notice()
                print("再见！")
                break
            prompt = ""
            continue

        if engine._provider:
            # Step 4: 流式 spinner + Esc 真打断（后台线程 → interrupt_event）
            response_content = ""
            got_first_token = False
            interrupted = False
            observed_tool_calls = 0
            observed_tool_errors = 0
            observed_text_deltas = 0
            observed_stream_error = False
            status.set_task("生成中")
            # 后台线程监听 Esc（生成态 stdin 空闲），set 后中断生成；
            # 仅 TTY 启动，且用 _esc_stop 保证回合结束线程必退（审计#6）
            _esc_stop = threading.Event()
            _esc_thread = None
            # H17-TUI 架构定稿: pump 会话级运行（循环前已 start），生成期继续
            # 收文本入队，中断由 Ctrl+C 承担（pump 线程内 KeyboardInterrupt 清行）。
            # 仅当 pump 不可用（非 PT/非 TTY/pump 已死）时降级 Esc 监听线程。
            if (_pump_mode and input_pump.dead or not _pump_mode) and sys.stdin.isatty():
                _esc_thread = threading.Thread(
                    target=_esc_listen_loop, args=(session, _esc_stop), daemon=True,
                )
                _esc_thread.start()
            try:
                for event in engine.stream_call_model(prompt):
                    if session.interrupt_event().is_set():
                        interrupted = True
                        print("\n[已打断]")
                        break
                    if not got_first_token and event.get("type") in ("text_delta", "error"):
                        got_first_token = True
                        sys.stdout.write(" " * 40 + "\r")  # UI 对齐:清 40 列
                        sys.stdout.flush()
                    _handle_stream_event(event)
                    if event.get("type") == "tool_call_start":
                        observed_tool_calls += 1
                        status.set_task(f"工具:{event.get('name', '?')}")
                    if event.get("type") == "tool_call_end" and event.get("is_error"):
                        observed_tool_errors += 1
                    if event.get("type") == "text_delta":
                        observed_text_deltas += 1
                        response_content += event.get("text", "")
                    if event.get("type") == "error":
                        observed_stream_error = True
                    elif event.get("type") == "done":
                        response_content = event.get("content", response_content)
            except KeyboardInterrupt:
                # pump 模式下 Ctrl+C 承担中断语义（Esc 让位给输入框）
                interrupted = True
                print("\n[已打断]")
            finally:
                # H17-TUI 修复: 清理入 finally — 流中非 KeyboardInterrupt 异常（网络错等）
                # 也不泄漏监听线程。审计#6: 先停线程再清 interrupt。
                # pump 会话级运行，此处不再 stop（唯一 stdin 读者地位不变）。
                _flush_stream_line()
                if _esc_thread is not None:
                    _esc_stop.set()
                session.interrupt_event().clear()
                if _status_bar_active:
                    status.bump_turns()
                    status.set_task("空闲")
            if response_content and not interrupted:
                engine._messages.append(prompt)
                engine._messages.append(response_content)
                engine._compact_if_needed()
                # R5-fix: history 由 engine.stream_call_model 的 done 分支写入
                # (model_call.py)，CLI 层不重复调用 _append_to_session_history
                # P0-2+: 逐轮落盘（crush 式增量持久化）— 中途被杀不再丢会话。
                # 此前仅退出时 persist，57109 被 kill 即全丢（2026-09-06 事故）。
                persist_result = engine._session_persister.persist_session()
                if persist_result.is_error and _OUTPUT_FORMAT == "plain":
                    print(f"[警告] 会话逐轮落盘失败: {persist_result.error}")
            _record_long_task_metrics(
                engine,
                event="interrupted_turn" if interrupted else "turn_complete",
                outcome=(
                    "interrupted" if interrupted
                    else "error" if observed_stream_error
                    else "ok"
                ),
                tool_calls=observed_tool_calls,
                tool_errors=observed_tool_errors,
                text_deltas=observed_text_deltas,
            )
        else:
            result = engine.submit(prompt)
            print(f"\n{result.output}\n")
            if result.stop_reason.value == "max_turns_reached":
                print(f"[会话结束: {result.stop_reason.value}]")
            _record_long_task_metrics(
                engine,
                event="turn_complete",
                outcome=result.stop_reason.value,
            )

        # H17-TUI: 消费生成期挂起队列（斜杠命令即时执行；首个文本成为下一轮输入，
        # 余下重新排队保持顺序；EOF/quit 视为退出请求）
        # 修复: 余项不可边 drain 边回填同一队列 — get 永远取回回填项，循环永不
        # break（2026-09-08 死循环事故）。先收集，循环结束后再回填。
        _queued_next = None
        _extras: list[str] = []
        # 修复: 不再以 not input_pump.dead 为循环条件 — pump 死亡时挂起队列
        # 里的输入仍须消费（2026-09-09: pump 静默死亡 → 6 条挂起全部滞留丢弃）。
        # 队列空时 get 超时返回 None 自然 break。
        while True:
            item = input_queue.get(timeout=0.2)
            if item is None:
                break
            if InputQueue.is_eof(item):
                quit_requested = True
                continue
            if _handle_slash_command(item):
                if quit_requested:
                    break
                continue
            if _queued_next is None:
                _queued_next = item
            else:
                _extras.append(item)
        for _x in reversed(_extras):
            input_queue.put(_x)
        if quit_requested:
            _drain_pending_notice()
            print("\n再见！")
            break

        _feed_behavior_to_daemon(engine, None)

        if _queued_next is not None:
            prompt = _queued_next
            next_prompt_hint = f"[排队执行] {prompt[:40]}"
            if _OUTPUT_FORMAT == "plain":
                print(next_prompt_hint)
        else:
            prompt = ""
            try:
                prompt = _next_input().strip()
            except (EOFError, KeyboardInterrupt):
                _drain_pending_notice()
                print("\n再见！")
                break

    def _shutdown_pump() -> None:
        """会话收尾：优雅退出 pump（唯一 stdin 读者）。

        事故背景（2026-09-09）：pump 是 daemon 线程且阻塞在 prompt() 里，
        解释器 finalize 阶段 daemon 线程持有 stdout 缓冲锁 →
        「Fatal Python error: _enter_buffered_busy」核心转储。
        此处通过 PT 的 app.exit(EOFError) 让阻塞中的 prompt() 抛 EOF 返回，
        线程干净退出后再 join；优雅失败兜底 os._exit 跳过 finalize。
        """
        if not _pump_mode:
            return
        input_pump.stop()
        try:
            app = getattr(session._session, "app", None)  # noqa: SLF001
            if app is not None and getattr(app, "is_running", False):
                app.exit(exception=EOFError)
        except Exception:  # noqa: BLE001 — PT 版本差异时不阻塞退出
            pass
        t = input_pump._thread  # noqa: SLF001
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
            if t.is_alive():
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(0)

    _shutdown_pump()

    # P0-2: 退出时持久化会话（--continue/--resume 的数据来源）。
    # 此前从未调用 — 会话只存内存,进程退出即失,续接功能形同虚设。
    if engine._conversation and _OUTPUT_FORMAT == "plain":
        result = engine._session_persister.persist_session()
        if result.is_ok:
            print(f"[会话已保存] {result.data}")

    stats = engine.get_stats()
    # F12e:先恢复终端状态,再打统计 — 统计输出落在干净的行首。
    _restore_tty()
    print()
    print_session_summary(SessionSummary(
        turns=stats["turns"],
        session_id=stats["session_id"],
        usage=stats["usage"],
        behavior={},
    ))
    _maybe_run_daemon_cycle()
    return 0


def _cmd_optimize(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config) if args.config else None)
    runtime = CodingRuntime(config)

    if args.target:
        target = args.target
    else:
        from lingclaude.core.config import find_config_path
        found = find_config_path()
        target = str(found.parent) if found else "."

    goal = args.goal or "structure"
    max_trials = args.trials or 20

    print(f"Optimizing {target} (goal: {goal}, trials: {max_trials})...")
    result = runtime.optimize(target, goal, max_trials)

    if result.get("success"):
        print_success(f"Best score: {result['best_score']:.2f}")
        print_kv("Experiments", result['experiments'])
        print_kv("Duration", f"{result['duration']:.1f}s")
        print_header("Best Params")
        for k, v in sorted(result["best_params"].items()):
            print_kv(k, v)

        if args.report:
            report_path = runtime.advisor.save_report(
                result["report"], args.report
            )
            print_success(f"Report saved: {report_path}")
    else:
        print_error(f"Optimization failed: {result.get('error', 'unknown')}")
        return 1
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config) if args.config else None)
    runtime = CodingRuntime(config)

    if args.target:
        target = args.target
    else:
        from lingclaude.core.config import find_config_path
        found = find_config_path()
        target = str(found.parent) if found else "."

    metrics = runtime.analyze(target)

    print(f"Structure analysis: {target}")
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    return 0


def _cmd_session(args: argparse.Namespace) -> int:
    from lingclaude.core.session import SessionManager

    config = load_config(Path(args.config) if args.config else None)
    manager = SessionManager(Path(config.session.save_dir))

    if args.session_action == "list":
        sessions = manager.list_sessions()
        if not sessions:
            print("No sessions found.")
        else:
            for info in sessions:
                sid = info["session_id"]
                proj = info.get("project_name", "")
                created = info.get("created_at", "")
                print(f"  {sid}  [{proj}] {created}")
    elif args.session_action == "delete":
        if not args.session_id:
            print("Error: session ID required for delete")
            return 1
        manager.delete(args.session_id)
        print(f"Removed session: {args.session_id}")
    else:
        print(f"Unknown session action: {args.session_action}")
        return 1
    return 0


def _cmd_knowledge(args: argparse.Namespace) -> int:
    from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase

    kb = KnowledgeBase()

    if args.kb_action == "stats":
        stats_result = kb.get_statistics()
        if stats_result.is_ok:
            print(json.dumps(stats_result.data, indent=2, ensure_ascii=False))
        else:
            print(f"Error: {stats_result.error}")
    elif args.kb_action == "search":
        if not args.keyword:
            print("Error: --keyword required for search")
            return 1
        rules_result = kb.search_rules(args.keyword)
        rules = rules_result.data if rules_result.is_ok else ()
        for rule in rules:
            print(f"  [{rule.id}] {rule.name} (score: {rule.quality_score:.2f})")
    elif args.kb_action == "list":
        rules_result = kb.get_all_rules(limit=args.limit or 50)
        rules = rules_result.data if rules_result.is_ok else ()
        for rule in rules:
            print(f"  [{rule.id}] {rule.name} (score: {rule.quality_score:.2f}, status: {rule.status})")
    else:
        print(f"Unknown knowledge action: {args.kb_action}")
        return 1

    kb.close()
    return 0


def _cmd_daemon(args: argparse.Namespace) -> int:
    from lingclaude.self_optimizer.daemon import OptimizationDaemon

    config = load_config(Path(args.config) if args.config else None)
    target = args.target or "."
    daemon = OptimizationDaemon(target=target, config=config)

    if args.daemon_action == "status":
        state = daemon.state
        print_header("自由化框架状态")
        print_kv("总周期", state.total_cycles)
        print_kv("总改进", state.total_improvements)
        print_kv("上次优化", state.last_optimization_time or "从未运行")
        if state.cycles:
            last = state.cycles[-1]
            print_kv("最近", f"score={last['best_score']:.2f} "
                  f"violations={last['violations_before']}→{last['violations_after']}")
    elif args.daemon_action == "run":
        cycle = daemon.run_once()
        if cycle:
            print_success(f"Cycle #{cycle.cycle_id}: score={cycle.best_score:.2f}")
            print_kv("触发", cycle.trigger_reason)
            print_kv("违规", f"{cycle.violations_before}→{cycle.violations_after}")
            print_kv("耗时", f"{cycle.duration_seconds}s")
            if cycle.report_path:
                print_kv("报告", cycle.report_path)
        else:
            print_info("无触发条件，无需优化")
    elif args.daemon_action == "watch":
        interval = args.interval or 300
        daemon.run_watch(interval_seconds=interval)
    elif args.daemon_action == "reset":
        daemon.state = DaemonState()
        daemon.state.save(daemon.state_path)
        print_success("状态已重置")
    return 0


def _cmd_metrics(args: argparse.Namespace) -> int:
    from lingclaude.core.metrics import MetricsStore, QualityScorer

    config = load_config(Path(args.config) if args.config else None)
    db_path = Path(config.session.save_dir).parent / "metrics.db"
    store = MetricsStore(db_path)

    if args.metrics_action == "stats":
        stats_result = store.get_statistics()
        if stats_result.is_ok:
            print_metrics_stats(stats_result.data)
        else:
            print_error(stats_result.error)
    elif args.metrics_action == "trend":
        if not args.category or not args.name:
            print_error("--category and --name required for trend")
            store.close()
            return 1
        trend_result = store.get_trend(args.category, args.name, window=args.window or 10)
        if trend_result.is_ok:
            t = trend_result.data
            if t.points:
                print_trend(t.name, t.direction, t.delta, t.moving_avg)
            else:
                print_warning("无数据")
        else:
            print_error(trend_result.error)
    elif args.metrics_action == "quality":
        scorer = QualityScorer(store)
        from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
        kb = KnowledgeBase()
        kb_stats_result = kb.get_statistics()
        kb.close()
        knowledge_stats = kb_stats_result.data if kb_stats_result.is_ok else {}
        score = scorer.compute_overall(knowledge=knowledge_stats)
        print_quality_report(QualityReport(
            overall=score.overall,
            safety=score.safety,
            structure=score.structure,
            behavior=score.behavior,
            knowledge=score.knowledge,
        ))
    elif args.metrics_action == "prune":
        if not args.before:
            print_error("--before date required for prune (ISO format)")
            store.close()
            return 1
        prune_result = store.prune(args.before)
        if prune_result.is_ok:
            print_success(f"已清理 {prune_result.data} 条数据")
        else:
            print_error(prune_result.error)
    elif args.metrics_action == "categories":
        cats_result = store.get_categories()
        if cats_result.is_ok:
            if cats_result.data:
                for cat in cats_result.data:
                    print_info(cat)
            else:
                print_warning("无分类")
        else:
            print_error(cats_result.error)
    else:
        print_error(f"Unknown metrics action: {args.metrics_action}")
        store.close()
        return 1

    store.close()
    return 0


def _cmd_governance_audit(args: argparse.Namespace) -> int:
    from lingclaude.core.governance_verifier import GovernanceVerifier

    # F3 修复:优先级 --proposals-file > LINGCLAUDE_PROPOSALS_FILE > config 同目录 > 旧默认
    candidates: list[Path] = []
    if args.proposals_file:
        candidates.append(Path(args.proposals_file))
    env_path = os.environ.get("LINGCLAUDE_PROPOSALS_FILE")
    if env_path:
        candidates.append(Path(env_path))
    try:
        from lingclaude.core.config import find_config_path
        cfg = find_config_path()
        if cfg:
            candidates.append(cfg.parent / "proposals.json")
    except Exception:
        pass
    proposals_path = next((c for c in candidates if c.exists()), None)
    if proposals_path is None:
        proposals_path = candidates[0] if candidates else Path("/home/ai/lingflow/discussion_hall/proposals.json")

    verifier = GovernanceVerifier()
    result = verifier.audit_proposals_file(proposals_path)

    if "error" in result:
        print_error(result["error"])
        return 1

    print_header("治理审计报告")
    print_kv("审计时间", result["audit_time"])
    print_kv("提案总数", result["total_proposals"])
    print_kv("有效投票", result["total_valid_votes"])
    print_kv("无效投票", result["total_filtered_votes"])
    print()

    for prop in result["proposals"]:
        pid = prop["proposal_id"]
        status = "✓" if prop["filtered_votes"] == 0 else "⚠"
        if prop["batch_patterns"]:
            status = "✗"
        print(f"  {status} {pid}: {prop['valid_votes']}有效 / {prop['total_votes']}总票")
        if prop["batch_patterns"]:
            for bp in prop["batch_patterns"]:
                print(f"      批量模式: {bp['evidence']}")
        for fv in prop["filtered"]:
            issues = ", ".join(fv.get("validation", {}).get("issues", []))
            if issues:
                print(f"      过滤: {fv.get('voter','?')} — {issues}")

    log_dir = verifier.log_dir
    print_success(f"完整报告: {log_dir}/audit_*.json")
    return 0


def _find_webui_binary() -> Path | None:
    """定位 lingclaude-webui 二进制：优先 webui-server/target/release，其次 PATH。"""
    root = Path(__file__).resolve().parent.parent.parent
    candidates = [
        root / "webui-server" / "target" / "release" / "lingclaude-webui",
        root / "target" / "release" / "lingclaude-webui",
        Path.cwd() / "webui-server" / "target" / "release" / "lingclaude-webui",
    ]
    for c in candidates:
        if c.is_file():
            return c
    in_path = shutil.which("lingclaude-webui")
    if in_path:
        return Path(in_path)
    return None


def _wait_for_http(url: str, timeout: float = 15.0) -> bool:
    """轮询直到 HTTP 端点可访问。任何 HTTP 响应（含 4xx 鉴权错误）都视为可达。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status < 500:
                    return True
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _cmd_doctor(args: argparse.Namespace) -> int:
    """R4: 环境自检 — /dev/null、git/pytest smoke、存储目录、MCP、Provider。"""
    import shutil

    checks: list[tuple[str, str, str]] = []

    # /dev/null 可写性
    try:
        with open("/dev/null", "wb") as f:
            f.write(b"\x00")
        checks.append(("devnull", "ok", "writable"))
    except OSError as e:
        checks.append(("devnull", "fail", f"not writable: {e}"))

    # git smoke
    git_path = shutil.which("git")
    if git_path:
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"], capture_output=True, timeout=5, text=True,
            )
            if result.returncode == 0:
                checks.append(("git", "ok", "repository accessible"))
            else:
                checks.append(("git", "warn", f"status rc={result.returncode}"))
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            checks.append(("git", "warn", f"smoke failed: {e}"))
    else:
        checks.append(("git", "warn", "not found in PATH"))

    # pytest smoke
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True, timeout=10, text=True,
        )
        if result.returncode == 0:
            ver = result.stdout.strip().split("\n")[0]
            checks.append(("pytest", "ok", ver))
        else:
            checks.append(("pytest", "warn", f"rc={result.returncode}"))
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        checks.append(("pytest", "warn", f"not runnable: {e}"))

    # 存储目录可写性
    storage_dirs = [
        ("journal_dir", Path(".lingclaude/journals")),
        ("checkpoint_dir", Path(".lingclaude/checkpoints")),
        ("session_dir", Path(".lingclaude/sessions")),
    ]
    for name, dir_path in storage_dirs:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            test_file = dir_path / ".doctor_probe"
            test_file.write_text("probe")
            test_file.unlink()
            checks.append((name, "ok", str(dir_path)))
        except OSError as e:
            checks.append((name, "fail", f"not writable: {e}"))

    # MCP 模块可导入性
    try:
        import lingclaude.mcp.server  # noqa: F401
        checks.append(("mcp", "ok", "module importable"))
    except ImportError as e:
        checks.append(("mcp", "warn", f"import failed: {e}"))

    # Provider 配置
    try:
        config = load_config(Path(args.config) if args.config else None)
        has_key = bool(config.model.api_key) or _is_local_base(config.model.base_url)
        if has_key:
            checks.append(("provider", "ok", f"{config.model.provider}/{config.model.model}"))
        else:
            checks.append(("provider", "warn", "no api_key (cloud provider may fail)"))
    except Exception as e:
        checks.append(("provider", "warn", f"config load failed: {e}"))

    # 输出
    has_fail = False
    print("lingclaude doctor — 环境自检")
    print("=" * 50)
    for name, status, detail in checks:
        icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}[status]
        if status == "fail":
            has_fail = True
        print(f"  {icon} {name:20s} {status:5s} {detail}")
    print("=" * 50)
    fail_count = sum(1 for _, s, _ in checks if s == "fail")
    warn_count = sum(1 for _, s, _ in checks if s == "warn")
    ok_count = sum(1 for _, s, _ in checks if s == "ok")
    print(f"  {ok_count} ok / {warn_count} warn / {fail_count} fail")
    return 1 if has_fail else 0


def _cmd_webui(args: argparse.Namespace) -> int:
    """启动 WebUI：webui-server(Rust, 前端) + 可选引擎(api.py :8700)。"""
    port = args.port
    engine_port = args.engine_port

    # 1. 引擎侧：默认假定 8700 已运行；--with-engine 则自动拉起 api.py
    engine_proc: subprocess.Popen | None = None
    engine_url = f"http://127.0.0.1:{engine_port}"
    if args.with_engine:
        api_file = Path(__file__).resolve().parent.parent / "api.py"
        print_info(f"启动引擎 {api_file} (端口 {engine_port})")
        engine_proc = subprocess.Popen(
            [sys.executable, str(api_file), "--port", str(engine_port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not _wait_for_http(f"{engine_url}/status", timeout=20.0):
            print_error(f"引擎 {engine_url}/status 超时未就绪")
            if engine_proc:
                engine_proc.terminate()
            return 1
    elif not _wait_for_http(f"{engine_url}/status", timeout=3.0):
        print_warning(f"引擎 {engine_url}/status 不可达，聊天将不可用（可用 --with-engine 自动拉起）")

    # 2. webui-server 二进制
    binary = _find_webui_binary()
    if binary is None:
        print_error(
            "未找到 lingclaude-webui 二进制。请先构建:\n"
            "  cd webui-server && cargo build --release\n"
            "  (或确保 webui/dist 已构建: cd webui && npm run build)"
        )
        if engine_proc:
            engine_proc.terminate()
        return 1

    # 3. 启动 webui-server（守护：转 background，输出重定向）
    log_path = Path(f"/tmp/lingclaude-webui-{port}.log")
    env = os.environ.copy()
    env.setdefault("LINGCLAUDE_BASE", engine_url)
    with open(log_path, "w") as logf:
        webui_proc = subprocess.Popen(
            [str(binary), str(port)],
            stdout=logf,
            stderr=subprocess.STDOUT,
            env=env,
        )
    print_info(f"webui-server 已启动 (pid {webui_proc.pid})，日志 {log_path}")

    # 4. 就绪探测
    if not _wait_for_http(f"http://127.0.0.1:{port}/status", timeout=15.0):
        print_error(f"webui-server 端口 {port} 未就绪，查看日志 {log_path}")
        webui_proc.terminate()
        if engine_proc:
            engine_proc.terminate()
        return 1

    # 5. /mint 拿带 token 的 URL
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/mint", timeout=3.0) as resp:
            url = resp.read().decode().strip()
    except Exception as e:
        print_error(f"/mint 失败: {e}")
        url = f"http://127.0.0.1:{port}"

    print_header("WebUI 已就绪")
    print_kv("URL", url)
    print_kv("webui-server", f"pid {webui_proc.pid} port {port}")
    if engine_proc:
        print_kv("engine(api.py)", f"pid {engine_proc.pid} port {engine_port}")

    # 6. 打开浏览器（尽力而为）
    if args.open and not args.no_open:
        for opener in ("xdg-open", "open"):
            if shutil.which(opener):
                try:
                    subprocess.Popen([opener, url])
                    break
                except Exception:
                    pass

    # 7. 守护直到 Ctrl+C（webui-server 退出则引擎一起停）
    try:
        while webui_proc.poll() is None:
            time.sleep(1.0)
        print_warning(f"webui-server 已退出 (rc={webui_proc.returncode})，日志 {log_path}")
    except KeyboardInterrupt:
        print_info("收到 Ctrl+C，停止服务")
    finally:
        webui_proc.terminate()
        if engine_proc:
            engine_proc.terminate()
    return 0


def main() -> int:
    # 审计#5 修复:F12c 的 key 单点文件此前定义了但零调用 — 整条 key 兜底链
    # （task_router 注释明言「key 实际单点存放于 ~/.ling_keys.env」）建立在该
    # 文件已加载的假设上。在任何子命令分派前注入，不覆盖 shell 已 export 的值。
    _load_keys_env()
    parser = argparse.ArgumentParser(
        prog="lingclaude",
        description="lingclaude — Self-optimizing AI runtime",
    )
    parser.add_argument("--config", "-c", help="Config file path")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    run_parser = subparsers.add_parser("run", help="Run lingclaude")
    run_parser.add_argument("prompt", nargs="?", help="Prompt to process")
    run_parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Show usage stats")
    run_parser.add_argument("--model", "-m", help="Override model name")
    run_parser.add_argument("--bash-executor", choices=["native", "lingxi"], help="Bash executor type (native or lingxi)")
    # P0-2: 会话续接 + 机器可读输出
    run_parser.add_argument("--continue", dest="continue_", action="store_true",
                            help="Resume the most recent session")
    run_parser.add_argument("--resume", metavar="SESSION_ID", help="Resume a specific session by id")
    run_parser.add_argument("--recover", action="store_true",
                            help="Resume the latest interrupted tool-round checkpoint")
    run_parser.add_argument("--output-format", choices=["plain", "json", "jsonl"], default="plain",
                            help="Output format (jsonl = one JSON per stream event)")

    opt_parser = subparsers.add_parser("optimize", help="Run self-optimization")
    opt_parser.add_argument("--target", "-t", help="Target path")
    opt_parser.add_argument("--goal", "-g", help="Optimization goal")
    opt_parser.add_argument("--trials", "-n", type=int, help="Max trials")
    opt_parser.add_argument("--report", "-r", help="Save report to file")

    analyze_parser = subparsers.add_parser("analyze", help="Analyze code structure")
    analyze_parser.add_argument("target", nargs="?", help="Target path")

    session_parser = subparsers.add_parser("session", help="Manage sessions")
    session_parser.add_argument("session_action", choices=["list", "delete"])
    session_parser.add_argument("session_id", nargs="?", help="Session ID")

    kb_parser = subparsers.add_parser("knowledge", help="Manage knowledge base")
    kb_parser.add_argument("kb_action", choices=["stats", "search", "list"])
    kb_parser.add_argument("--keyword", "-k", help="Search keyword")
    kb_parser.add_argument("--limit", "-l", type=int, help="Result limit")

    daemon_parser = subparsers.add_parser("daemon", help="Self-optimization daemon")
    daemon_parser.add_argument(
        "daemon_action",
        choices=["status", "run", "watch", "reset"],
        help="Daemon action",
    )
    daemon_parser.add_argument("--target", "-t", help="Target path")
    daemon_parser.add_argument(
        "--interval", "-i", type=int, default=300, help="Watch interval (seconds)"
    )

    metrics_parser = subparsers.add_parser("metrics", help="Query metrics and quality scores")
    metrics_parser.add_argument(
        "metrics_action",
        choices=["stats", "trend", "quality", "prune", "categories"],
        help="Metrics action",
    )
    metrics_parser.add_argument("--category", "-C", help="Metric category")
    metrics_parser.add_argument("--name", "-N", help="Metric name")
    metrics_parser.add_argument("--window", "-w", type=int, default=10, help="Trend window size")
    metrics_parser.add_argument("--before", "-b", help="Prune before date (ISO format)")

    gov_parser = subparsers.add_parser("governance-audit", help="Audit governance votes")
    gov_parser.add_argument("--proposals-file", "-p", help="Path to proposals.json")

    subparsers.add_parser("doctor", help="Environment health check")

    webui_parser = subparsers.add_parser("webui", help="Start the WebUI server (webui-server + optional engine)")
    webui_parser.add_argument("--port", type=int, default=13458, help="WebUI server port (default 13458)")
    webui_parser.add_argument(
        "--engine-port", type=int, default=8700, help="lingclaude engine api.py port (default 8700)"
    )
    webui_parser.add_argument(
        "--with-engine", action="store_true", help="Auto-start the engine (api.py) on --engine-port"
    )
    webui_parser.add_argument("--no-open", action="store_true", help="Do not open the browser")
    webui_parser.add_argument("--open", action="store_true", help="Open browser (also default when TTY)")

    # F1 修复:注册 unknowns 子命令(Wieman C13,详见 docs/audit/CLI_WEBUI_AUDIT_REPORT.md)
    from lingclaude.cli import unknowns as _unknowns
    unknowns_parser = subparsers.add_parser(
        "unknowns",
        help="List / add / resolve known unknowns in LACP plugin manifests",
    )
    _unknowns_sub = unknowns_parser.add_subparsers(dest="unknowns_command")
    _unknowns_p_list = _unknowns_sub.add_parser("list", help="List known unknowns")
    _unknowns_p_list.add_argument("--plugin", help="filter to a single plugin")
    _unknowns_p_list.add_argument("--severity", choices=["info", "warn", "block"])
    _unknowns_p_list.add_argument("--category", help="filter by category")
    _unknowns_p_list.add_argument("--json", action="store_true", help="output as JSON")
    _unknowns_p_list.add_argument(
        "--manifest-dir", type=Path, default=Path(".lacp/plugins"),
        help="directory to scan (default: .lacp/plugins)",
    )
    _unknowns_p_add = _unknowns_sub.add_parser("add", help="Add a known unknown (interactive)")
    _unknowns_p_add.add_argument("--plugin", required=True)
    _unknowns_p_add.add_argument("--claim", required=True)
    _unknowns_p_add.add_argument("--severity", choices=["info", "warn", "block"], default="info")
    _unknowns_p_add.add_argument("--category", default="general")
    _unknowns_p_add.add_argument("--owner", default="")
    _unknowns_p_resolve = _unknowns_sub.add_parser("resolve", help="Mark a known unknown as resolved")
    _unknowns_p_resolve.add_argument("--plugin", required=True)
    _unknowns_p_resolve.add_argument("--claim-substring", required=True)
    _unknowns_p_resolve.add_argument(
        "--manifest-dir", type=Path, default=Path(".lacp/plugins"),
        help="directory to scan (default: .lacp/plugins)",
    )

    args = parser.parse_args()

    if args.command == "run":
        return _cmd_run(args)
    elif args.command == "optimize":
        return _cmd_optimize(args)
    elif args.command == "analyze":
        return _cmd_analyze(args)
    elif args.command == "session":
        return _cmd_session(args)
    elif args.command == "knowledge":
        return _cmd_knowledge(args)
    elif args.command == "daemon":
        return _cmd_daemon(args)
    elif args.command == "metrics":
        return _cmd_metrics(args)
    elif args.command == "governance-audit":
        return _cmd_governance_audit(args)
    elif args.command == "doctor":
        return _cmd_doctor(args)
    elif args.command == "webui":
        return _cmd_webui(args)
    elif args.command == "unknowns":
        if not getattr(args, "unknowns_command", None):
            unknowns_parser.print_help()
            return 0
        if args.unknowns_command == "list":
            sub_argv = ["list"]
            for opt, val in (
                ("--plugin", args.plugin),
                ("--severity", args.severity),
                ("--category", args.category),
                ("--json", args.json),
                ("--manifest-dir", str(args.manifest_dir)),
            ):
                if val is not None and val is not False:
                    if isinstance(val, bool):
                        sub_argv.append(opt)
                    else:
                        sub_argv.extend([opt, str(val)])
        elif args.unknowns_command == "add":
            sub_argv = ["add", "--plugin", args.plugin, "--claim", args.claim,
                        "--severity", args.severity, "--category", args.category,
                        "--owner", args.owner]
        elif args.unknowns_command == "resolve":
            sub_argv = ["resolve", "--plugin", args.plugin,
                        "--claim-substring", args.claim_substring,
                        "--manifest-dir", str(args.manifest_dir)]
        else:
            unknowns_parser.print_help()
            return 0
        return _unknowns.main(sub_argv)
    else:
        parser.print_help()
        return 0
