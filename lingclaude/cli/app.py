from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lingclaude.core.config import load_config
from lingclaude.engine.coding import CodingRuntime


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config) if args.config else None)
    runtime = CodingRuntime(config)

    if args.prompt:
        print(f"灵克> {args.prompt}")
        result = runtime.execute_tool("bash", command=f"echo 'Prompt received: {args.prompt}'")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("灵克 v0.1.0 — 开源 AI 编程助手")
        print(f"Config: {args.config or 'default'}")
        print(f"Tools: {len(runtime.registry.list_tools())} registered")
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


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="lingclaude",
        description="LingClaude — Self-optimizing AI runtime",
    )
    parser.add_argument("--config", "-c", help="Config file path")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    run_parser = subparsers.add_parser("run", help="Run LingClaude")
    run_parser.add_argument("prompt", nargs="?", help="Prompt to process")

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
    else:
        parser.print_help()
        return 0
