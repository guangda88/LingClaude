from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lingclaude.core.config import load_config
from lingclaude.core.query_engine import QueryEngine
from lingclaude.engine.coding import CodingRuntime


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config) if args.config else None)
    engine = QueryEngine.from_config_file(args.config)

    if args.prompt:
        if args.interactive:
            return _interactive_loop(engine, args.prompt)
        print(f"灵克> {args.prompt}")
        result = engine.submit(args.prompt)
        print(result.output)
        if args.verbose:
            print(f"\n--- 会话统计 ---")
            stats = engine.get_stats()
            print(f"轮次: {stats['turns']}, 会话: {stats['session_id']}")
            print(f"用量: {stats['usage']}")
    else:
        print("灵克 v0.2.0 — 开源 AI 编程助手")
        print(f"Config: {args.config or 'default'}")
        print(f"Model: {config.model.provider}/{config.model.model}")
        provider_status = "已连接" if engine._provider else "未配置（回退模式）"
        print(f"Provider: {provider_status}")
        runtime = CodingRuntime(config)
        print(f"Tools: {len(runtime.registry.list_tools())} registered")
    return 0


def _interactive_loop(engine: QueryEngine, first_prompt: str) -> int:
    print(f"灵克 v0.2.0 — 交互模式（输入 'exit' 或 'quit' 退出）")
    print(f"Provider: {'已连接' if engine._provider else '未配置（回退模式）'}")
    print()

    prompt = first_prompt
    while True:
        if not prompt:
            try:
                prompt = input("灵克> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break
        if prompt.lower() in ("exit", "quit", "q"):
            print("再见！")
            break
        if not prompt:
            prompt = ""
            continue

        result = engine.submit(prompt)
        print(f"\n{result.output}\n")

        if result.stop_reason.value != "completed":
            print(f"[会话结束: {result.stop_reason.value}]")
            break

        prompt = ""
        try:
            prompt = input("灵克> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

    stats = engine.get_stats()
    print(f"\n--- 会话统计 ---")
    print(f"轮次: {stats['turns']}, 会话: {stats['session_id']}")
    print(f"用量: {stats['usage']}")
    return 0


def _cmd_optimize(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config) if args.config else None)
    runtime = CodingRuntime(config)

    target = args.target or "."
    goal = args.goal or "structure"
    max_trials = args.trials or 20

    print(f"Optimizing {target} (goal: {goal}, trials: {max_trials})...")
    result = runtime.optimize(target, goal, max_trials)

    if result.get("success"):
        print(f"\nBest score: {result['best_score']:.2f}")
        print(f"Experiments: {result['experiments']}")
        print(f"Duration: {result['duration']:.1f}s")
        print("\nBest params:")
        for k, v in sorted(result["best_params"].items()):
            print(f"  {k}: {v}")

        if args.report:
            report_path = runtime.advisor.save_report(
                result["report"], args.report
            )
            print(f"\nReport saved: {report_path}")
    else:
        print(f"Optimization failed: {result.get('error', 'unknown')}")
        return 1
    return 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config) if args.config else None)
    runtime = CodingRuntime(config)

    target = args.target or "."
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
            for sid in sessions:
                print(f"  {sid}")
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
        stats = kb.get_statistics()
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    elif args.kb_action == "search":
        if not args.keyword:
            print("Error: --keyword required for search")
            return 1
        rules = kb.search_rules(args.keyword)
        for rule in rules:
            print(f"  [{rule.id}] {rule.name} (score: {rule.quality_score:.2f})")
    elif args.kb_action == "list":
        rules = kb.get_all_rules(limit=args.limit or 50)
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
        print(f"自由化框架状态:")
        print(f"  总周期: {state.total_cycles}")
        print(f"  总改进: {state.total_improvements}")
        print(f"  上次优化: {state.last_optimization_time or '从未运行'}")
        if state.cycles:
            last = state.cycles[-1]
            print(f"  最近: score={last['best_score']:.2f} "
                  f"violations={last['violations_before']}→{last['violations_after']}")
    elif args.daemon_action == "run":
        cycle = daemon.run_once()
        if cycle:
            print(f"Cycle #{cycle.cycle_id}: score={cycle.best_score:.2f}")
            print(f"  触发: {cycle.trigger_reason}")
            print(f"  违规: {cycle.violations_before}→{cycle.violations_after}")
            print(f"  耗时: {cycle.duration_seconds}s")
            if cycle.report_path:
                print(f"  报告: {cycle.report_path}")
        else:
            print("无触发条件，无需优化")
    elif args.daemon_action == "watch":
        interval = args.interval or 300
        daemon.run_watch(interval_seconds=interval)
    elif args.daemon_action == "reset":
        daemon.state = DaemonState()
        daemon.state.save(daemon.state_path)
        print("状态已重置")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="lingclaude",
        description="LingClaude — Self-optimizing AI runtime",
    )
    parser.add_argument("--config", "-c", help="Config file path")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    run_parser = subparsers.add_parser("run", help="Run LingClaude")
    run_parser.add_argument("prompt", nargs="?", help="Prompt to process")
    run_parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Show usage stats")
    run_parser.add_argument("--model", "-m", help="Override model name")

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
    else:
        parser.print_help()
        return 0
