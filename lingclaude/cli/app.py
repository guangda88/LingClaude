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
from lingclaude.cli.n5_token_guard import check_token_exhaustion, resolve_max_tokens
from lingclaude.cli.n5_stream_watchdog import StreamWatchdog
from lingclaude.ops.rss_watchdog import check_rss_watchdog, sample_rss_mb
from lingclaude.core.config import lingclaudeConfig, load_config
from lingclaude.core.query_engine import QueryEngine
from lingclaude.engine.coding import CodingRuntime
from lingclaude.self_optimizer.daemon import OptimizationDaemon, DaemonState

warnings.filterwarnings("ignore", category=SyntaxWarning)

_logger = logging.getLogger(__name__)


_behavior_daemon: OptimizationDaemon | None = None



# ---- P4.1 迁移的 REPL 符号兼容 re-export（tests/test_t1_wiring.py 等 import 面）----
from lingclaude.cli.repl import (  # noqa: E402,F401
    _get_version,
    _interactive_loop,
    _is_local_base,
    _provider_status,
)
from lingclaude.cli.repl_io import (  # noqa: E402,F401
    _esc_listen_loop,
    _esc_pressed,
    _flush_stream_line,
    _handle_stream_event,
)
from lingclaude.cli.repl_turn import (  # noqa: E402,F401
    _feed_behavior_to_daemon,
    _maybe_recover_on_startup,
    _maybe_run_daemon_cycle,
    _record_long_task_metrics,
    _single_turn,
)
from lingclaude.cli.repl_io import get_output_format, set_output_format  # noqa: E402,F401
from lingclaude.cli.repl_turn import (  # noqa: E402,F401
    start_bus_responder_background as _start_bus_responder_background,
)










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


def _close_runtime(runtime: "CodingRuntime") -> None:
    """atexit 回调：优雅释放 runtime 资源，避免解释器 shutdown 阶段线程池 join 异常。"""
    try:
        runtime.close()
    except Exception:  # noqa: BLE001 — 退出路径不抛异常
        pass


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

    # --provider 单独指定：保持当前模型名，仅切换 provider（atomcode 风格解耦）
    if getattr(args, "provider", None) and not getattr(args, "model", None):
        prov = args.provider.strip()
        cur_cfg = getattr(engine, "_model_config", None)
        cur_model = getattr(cur_cfg, "model", "") if cur_cfg else ""
        if cur_model:
            sel = f"{cur_model}@{prov}"
            r2 = engine.switch_model(sel)
            if r2.is_ok:
                print(f"[默认模型] 切到 provider {prov}，模型 {cur_model}")
            else:
                print(f"[模型] provider 切换失败: {r2.error}")
        else:
            print("[模型] 无法确定当前模型名，--provider 需与 --model 联用")

    # RFC v0.1 §3 A1-1: 启动后台 BusResponder 监听 LingBus 任务
    # 族长 2026-08-27 决议:默认 = 1(生产开),pytest 环境通过 conftest.py 设为 0
    # 优先级:LINGCLAUDE_BUS_LISTENER 显式 = 0 > 默认开 > 显式 = 1(冗余兼容)
    # 审计#13 修复:仅交互模式启动 — 单轮/横幅进程几秒即退，daemon 线程会在
    # 任务执行中被硬杀，留下「已收到」却无结果的半截对话。
    if args.interactive and os.environ.get("LINGCLAUDE_BUS_LISTENER") != "0":
        from lingclaude.cli.repl_turn import start_bus_responder_background

        _bus_responder_stop = start_bus_responder_background()
        import atexit
        atexit.register(_bus_responder_stop.set)

    # 退出时清理 runtime 资源（LSP 子进程 + BackgroundTaskManager 线程池）。
    # 修复:BackgroundTaskManager.shutdown() 存在但从未被调用 — 解释器 shutdown 阶段
    # _python_exit join 线程池 worker 时被 KeyboardInterrupt 打断 → 退出噪音 traceback。
    import atexit

    atexit.register(_close_runtime, runtime)

    # P0-2: 输出格式 + 会话续接（--continue 最近会话 / --resume <id>）
    set_output_format(getattr(args, "output_format", "plain") or "plain")

    resume_id = getattr(args, "resume", None)
    if resume_id is None and getattr(args, "continue_", False):
        sessions = engine.session_manager.list_sessions()
        if sessions:
            latest = max(sessions, key=lambda s: s.get("created_at", ""))
            resume_id = latest.get("session_id")
            if get_output_format() == "plain":
                print(f"[--continue] 最近会话: {resume_id}")
        elif get_output_format() == "plain":
            print("[--continue] 没有可恢复的历史会话，从新会话开始")
    if resume_id:
        if engine._session_persister.load_session(resume_id):
            if get_output_format() == "plain":
                print(f"[会话已恢复] {resume_id}（{len(engine._conversation)} 轮对话）")
        elif get_output_format() == "plain":
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














# P0-行缓冲:跨事件聚合流式 delta,残行待下次事件或 flush 收尾








def _load_runtime_target(args: argparse.Namespace) -> tuple:
    """加载 config + CodingRuntime，并解析目标路径。

    真重复收敛: _cmd_optimize/_cmd_analyze 初始化段逐字相同，提取单源。
    """
    config = load_config(Path(args.config) if args.config else None)
    runtime = CodingRuntime(config)

    if args.target:
        target = args.target
    else:
        from lingclaude.core.config import find_config_path
        found = find_config_path()
        target = str(found.parent) if found else "."
    return runtime, target


def _cmd_optimize(args: argparse.Namespace) -> int:
    runtime, target = _load_runtime_target(args)

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
    runtime, target = _load_runtime_target(args)

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


def _terminate_procs(*procs: subprocess.Popen | None) -> None:
    """终止非 None 的子进程（忽略终止异常）。webui 启动/清理路径共用。"""
    for p in procs:
        if p is not None:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001 — 清理路径不抛异常
                pass


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
            _terminate_procs(engine_proc)
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
            _terminate_procs(engine_proc)
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
        _terminate_procs(webui_proc, engine_proc)
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
        _terminate_procs(webui_proc, engine_proc)
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
    run_parser.add_argument("--model", "-m",
                            help="Override model: name | model@provider | provider/model (对齐 opencode/crush)")
    run_parser.add_argument("--provider", help="Override provider only (atomcode 风格, 与 --model 解耦)")
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
