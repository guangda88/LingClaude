from __future__ import annotations

import argparse
import json
import logging
import sys
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

    if args.prompt:
        if args.interactive:
            return _interactive_loop(engine, args.prompt)
        return _single_turn(engine, args.prompt, args.verbose)
    elif args.interactive:
        return _interactive_loop(engine, None)
    else:
        version = _get_version()
        provider_status = "已连接" if engine._provider else "未配置（回退模式）"
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
                sys.stdout.write("            \r")
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
        sys.stdout.write(f"\n[错误] {event.get('error', '')}\n")
        sys.stdout.write("提示: 请检查网络连接，或在 config.yaml 中确认 model.api_key 已设置\n")
        sys.stdout.flush()


def _interactive_loop(engine: QueryEngine, first_prompt: str | None) -> int:
    version = _get_version()
    print(f"灵克 v{version} — 交互模式（输入 'exit' 或 'quit' 退出）")
    print(f"Provider: {'已连接' if engine._provider else '未配置（回退模式）'}")
    print()

    def _read_input() -> str:
        try:
            return input("灵克> ")
        except UnicodeDecodeError:
            sys.stdin.buffer.readline()
            print("[输入编码错误，请检查终端编码设置]")
            return ""

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

        if engine._provider:
            sys.stdout.write("思考中...\r")
            sys.stdout.flush()
            response_content = ""
            got_first_token = False
            for event in engine.stream_call_model(prompt):
                if not got_first_token and event.get("type") in ("text_delta", "error"):
                    got_first_token = True
                    sys.stdout.write("            \r")
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

    proposals_path = Path(args.proposals_file or "/home/ai/lingflow/discussion_hall/proposals.json")
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
    else:
        parser.print_help()
        return 0
