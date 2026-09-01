from __future__ import annotations

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
from lingclaude.cli.interface import create_session, PromptSessionInterface
from lingclaude.core.config import lingclaudeConfig, load_config
from lingclaude.core.query_engine import QueryEngine
from lingclaude.engine.coding import CodingRuntime
from lingclaude.self_optimizer.daemon import OptimizationDaemon, DaemonState

warnings.filterwarnings("ignore", category=SyntaxWarning)

_logger = logging.getLogger(__name__)


def _feed_behavior_to_daemon(engine: QueryEngine, config: lingclaudeConfig | None) -> None:
    try:
        cfg = config or load_config(None)
        daemon = OptimizationDaemon(target=".", config=cfg)
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
    try:
        version_file = Path(__file__).resolve().parent.parent / "VERSION"
        if version_file.exists():
            return version_file.read_text().strip()
    except Exception as e:
        _logger.debug("version file read failed: %s", e)
    return "0.2.1"


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
    engine_result = QueryEngine.from_config_file(args.config)
    if engine_result.is_error:
        print(f"错误: {engine_result.error}")
        return 1
    engine = engine_result.data
    runtime = CodingRuntime(config)
    engine.set_runtime(runtime)

    if args.bash_executor:
        from dataclasses import replace
        config = replace(config, engine=replace(config.engine, bash_executor_type=args.bash_executor))

    # RFC v0.1 §3 A1-1: 启动后台 BusResponder 监听 LingBus 任务
    # 族长 2026-08-27 决议:默认 = 1(生产开),pytest 环境通过 conftest.py 设为 0
    # 优先级:LINGCLAUDE_BUS_LISTENER 显式 = 0 > 默认开 > 显式 = 1(冗余兼容)
    import os as _os
    if _os.environ.get("LINGCLAUDE_BUS_LISTENER") != "0":
        _bus_responder_stop = _start_bus_responder_background()
        import atexit
        atexit.register(_bus_responder_stop.set)

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
        for event in engine.stream_call_model(prompt):
            if not got_first_token and event.get("type") in ("text_delta", "error"):
                got_first_token = True
                sys.stdout.write(" " * 40 + "\r")  # UI 对齐:清 40 列
                sys.stdout.flush()
            _handle_stream_event(event)
            if event.get("type") == "text_delta":
                response_content += event.get("text", "")
            elif event.get("type") == "done":
                response_content = event.get("content", response_content)
        if response_content:
            engine._messages.append(prompt)
            engine._messages.append(response_content)
            engine._compact_if_needed()
            engine._append_to_session_history(prompt, response_content)
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
        import tty

        if not sys.stdin.isatty():
            return False
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            readable, _, _ = select.select([fd], [], [], 0.05)
            if readable:
                ch = sys.stdin.read(1)
                return ch == "\x1b"
            return False
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:  # noqa: BLE001 — 非 tty/无 termios 时静默禁用 Esc 打断
        return False


def _esc_listen_loop(session: PromptSessionInterface) -> None:
    """Step 4: 后台线程监听 Esc → set interrupt_event（生成态 stdin 空闲）。"""
    while not session.interrupt_event().is_set():
        if _esc_pressed():
            session.interrupt_event().set()
            break


def _handle_stream_event(event: dict[str, Any]) -> None:
    etype = event.get("type")
    if etype == "text_delta":
        sys.stdout.write(event["text"])
        sys.stdout.flush()
    elif etype == "tool_call_start":
        name = event.get("name", "?")
        args = event.get("arguments", "")
        try:
            parsed = json.loads(args)
            args_preview = " ".join(f"{k}={v}" for k, v in list(parsed.items())[:3])
        except (json.JSONDecodeError, TypeError):
            args_preview = args[:60] if args else ""
        sys.stdout.write(f"\n  [{name}] {args_preview} ... ")
        sys.stdout.flush()
    elif etype == "tool_call_end":
        is_error = event.get("is_error", False)
        preview = event.get("output_preview", "")
        mark = "❌" if is_error else "✅"
        if preview and not is_error:
            preview = preview[:80].replace("\n", " ")
            sys.stdout.write(f"{mark} ({len(preview)} chars)\n")
        else:
            sys.stdout.write(f"{mark}\n")
        sys.stdout.flush()
    elif etype == "status":
        sys.stdout.write(f"\n  [{event.get('message', '')}] ")
        sys.stdout.flush()
    elif etype == "done":
        sys.stdout.write("\n\n")
        sys.stdout.flush()
    elif etype == "error":
        # UI 对齐修复:前后各留空行,与 rich stderr 日志/下一提示符隔离。
        sys.stdout.write(f"\n\n[错误] {event.get('error', '')}\n")
        sys.stdout.write("提示: 请检查网络连接，或在 config.yaml 中确认 model.api_key 已设置\n\n")
        sys.stdout.flush()


def _interactive_loop(engine: QueryEngine, first_prompt: str | None) -> int:
    version = _get_version()
    print(f"灵克 v{version} — 交互模式（输入 'exit' 或 'quit' 退出）")
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
            ["/help", "/clear", "/compact", "/model", "/schedule", "/quit"],
            ignore_case=True,
        )
    except ImportError:
        _completer = None
    session: PromptSessionInterface = create_session(completer=_completer)

    def _read_input() -> str:
        try:
            text = session.prompt("灵克> ")
            if text.strip():
                session.push_to_history(text)
            return text
        except UnicodeDecodeError:
            sys.stdin.buffer.readline()
            print("[输入编码错误，请检查终端编码设置]")
            return ""

    def _handle_slash_command(cmd: str) -> bool:
        """T1-7: 斜杠命令 — /help /clear /compact /model。处理返回 True 表示已消费。"""
        parts = cmd.strip().split(maxsplit=1)
        if not parts or not parts[0].startswith("/"):
            return False
        name = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        if name in ("/help", "/?"):
            print("[斜杠命令] /help 帮助 | /clear 清空会话 | /compact 立即压缩 | /model 当前模型")
            return True
        if name == "/clear":
            engine._messages.clear()
            engine._conversation.clear()
            print("[会话已清空]")
            return True
        if name == "/compact":
            engine._compact_if_needed()
            print("[已触发压缩]")
            return True
        if name == "/model":
            # P1-4: /model 显示当前；/model <name> 会话中途切换（保留上下文）
            if arg:
                result = engine.switch_model(arg.strip())
                if result.is_ok:
                    # F12h:切换后显示端点 — 暴露「模型名换了但端点没换」的假切换
                    prov_cfg = getattr(engine._provider, "_config", None) if engine._provider else None
                    base = getattr(prov_cfg, "base_url", "?") or "?"
                    print(f"[模型已切换] {result.data} @ {base}")
                    print("[提示] 默认模型已切换；实际每条消息仍按任务动态路由(TaskRouter)")
                else:
                    print(f"[切换失败] {result.error}")
            else:
                # /model 显示修复:engine.config 是 QueryEngineConfig(无 .model 字段,
                # 此前恒显示 unknown)。真实默认模型在 provider._config。
                prov_cfg = getattr(engine._provider, "_config", None) if engine._provider else None
                model_name = getattr(prov_cfg, "model", "") or "?"
                base = getattr(prov_cfg, "base_url", "") or "?"
                print(f"[当前默认模型] {model_name} @ {base}")
                print("[提示] 实际模型按任务动态路由(TaskRouter)，每条消息可能不同；"
                      "/model <name> 切换默认模型")
            return True
        # T2-2/案 4: /schedule 命令 — 定时任务注册/列出/取消
        if name == "/schedule":
            from lingclaude.core.scheduler import get_schedule_manager, ScheduleType

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
                    print("[用法] /schedule @daily|@hourly|@weekly|interval:N \"任务内容\"")
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
                parts = rest.split(maxsplit=1)
                if len(parts) < 1:
                    print("[用法] /lsp add <lang> --command <cmd>")
                else:
                    lang = parts[0].strip()
                    cmd_part = parts[1] if len(parts) > 1 else ""
                    command = ""
                    args: list[str] = []
                    if "--command" in cmd_part:
                        after = cmd_part.split("--command", 1)[1].strip()
                        if "--args" in after:
                            cmd_str, args_str = after.split("--args", 1)
                            command = cmd_str.strip().split()[0] if cmd_str.strip() else ""
                            args = args_str.strip().split()
                        else:
                            command = after.strip().split()[0] if after.strip() else ""
                            args = after.strip().split()[1:]
                    if not command:
                        print("[用法] /lsp add <lang> --command <cmd> [--args 'a b']")
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
        return False

    prompt = first_prompt or ""
    while True:
        if not prompt:
            try:
                prompt = _read_input().strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break
        if prompt.lower() in ("exit", "quit", "q"):
            print("再见！")
            break
        if not prompt:
            continue
        # T1-7: 斜杠命令优先消费
        if _handle_slash_command(prompt):
            prompt = ""
            continue

        if engine._provider:
            # Step 4: 流式 spinner + Esc 真打断（后台线程 → interrupt_event）
            response_content = ""
            got_first_token = False
            interrupted = False
            # 后台线程监听 Esc（生成态 stdin 空闲），set 后中断生成
            _esc_thread = threading.Thread(
                target=_esc_listen_loop, args=(session,), daemon=True,
            )
            _esc_thread.start()
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
                if event.get("type") == "text_delta":
                    response_content += event.get("text", "")
                elif event.get("type") == "done":
                    response_content = event.get("content", response_content)
            session.interrupt_event().clear()
            if response_content and not interrupted:
                engine._messages.append(prompt)
                engine._messages.append(response_content)
                engine._compact_if_needed()
                engine._append_to_session_history(prompt, response_content)
        else:
            result = engine.submit(prompt)
            print(f"\n{result.output}\n")
            if result.stop_reason.value == "max_turns_reached":
                print(f"[会话结束: {result.stop_reason.value}]")
        _feed_behavior_to_daemon(engine, None)

        prompt = ""
        try:
            prompt = _read_input().strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

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
        print(f"Deleted session: {args.session_id}")
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
                        "--claim-substring", args.claim_substring]
        else:
            unknowns_parser.print_help()
            return 0
        return _unknowns.main(sub_argv)
    else:
        parser.print_help()
        return 0
