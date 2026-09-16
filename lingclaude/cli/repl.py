"""交互主循环（P4.1 从 cli/app.py 拆出）— REPL 装配 + turn 调度 + 优雅退出。

原实现是 app.py 内 713 行巨函数 `_interactive_loop`（radon F(69)，嵌套闭包
复杂度聚合进宿主）。拆为四模块：
  repl.py        本模块（装配+调度；嵌套闭包外提为 ctx 传参的模块级函数）
  commands.py    SlashCommandProcessor（斜杠命令，nonlocal→实例属性）
  repl_turn.py   回合执行与可观测性收尾（N5/N6 守卫挂点）
  repl_io.py     流式渲染 + Esc 监听
app.py 保留 argparse 门面 + 历史符号 re-export（测试兼容）。
所有函数体自 app.py 原样机械迁移（2026-09-11），闭包变量经 _ReplCtx 显式传递。
"""

import os
import sys
import logging
import threading
import time
from pathlib import Path
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from lingclaude.cli.commands import SLASH_COMPLETER_WORDS, SlashCommandProcessor
from lingclaude.cli.display import SessionSummary
from lingclaude.cli.input_queue import InputQueue
from lingclaude.cli.interface import (
    create_session,
    FallbackSession,
    PromptSessionInterface,
    PromptToolkitSession,
)
from lingclaude.cli.render_facade import print_session_summary
from lingclaude.cli.status import toolbar_fragments
from lingclaude.cli.n5_stream_watchdog import StreamWatchdog
from lingclaude.cli.repl_io import (
    _esc_listen_loop,
    _flush_stream_line,
    _handle_stream_event,
    get_output_format,
)
from lingclaude.cli.full_tui import FullTuiSession
from lingclaude.cli.repl_turn import (
    _feed_behavior_to_daemon,
    _maybe_run_daemon_cycle,
    _record_long_task_metrics,
)

if TYPE_CHECKING:
    from lingclaude.core.query_engine import QueryEngine

_logger = logging.getLogger(__name__)

try:  # F12e: termios 平台兼容 — 模块级 try-import 不入 G3 函数内计数
    import termios
except ImportError:  # pragma: no cover
    termios = None  # type: ignore[assignment]

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



def _provider_status(engine: "QueryEngine") -> str:
    """F12a:启动横幅诚实化 — 不再对缺 key 的云端 provider 谎报「已连接」。"""
    if engine._provider is None:
        return "未配置（回退模式）"
    cfg = getattr(engine._provider, "_config", None)
    api_key = str(getattr(cfg, "api_key", "") or "")
    base = str(getattr(cfg, "base_url", "") or "")
    if not api_key and not _is_local_base(base):
        return "已配置但缺 API key（云端调用将失败，请检查 config.yaml model.api_key）"
    return "已连接"



# H17-TUI post-turn 队列消费的退出哨兵（EOF/quit 视为退出请求）
_TURN_QUIT = "\x00__TURN_QUIT__\x00"


@dataclass
class _ReplCtx:
    """交互会话运行时上下文（原 _interactive_loop 闭包捕获变量的显式化）。"""

    engine: "QueryEngine"
    status: Any
    session: PromptSessionInterface
    input_queue: Any = None
    input_pump: Any = None
    processor: SlashCommandProcessor | None = None
    pump_mode: bool = False
    status_bar_active: bool = False
    saved_termios: Any = None
    queued_next: str | None = None
    # 2026-09-15（会话问题重构 P0-1）: 心跳超长停滞强制重建的冷却计数。
    # 重建后若仍无心跳（重建无效——"强制重建也没用"的真实场景），第二次
    # 直接置 dead + fallback_read 永久降级裸 input()，避免反复 churn。
    stall_rebuilds: int = 0


def _restore_tty(ctx: _ReplCtx) -> None:
    _saved_termios = ctx.saved_termios
    if _saved_termios is None:
        return
    try:
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _saved_termios)
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:  # noqa: BLE001 — fd 已关等场景静默
        pass


def _reset_tty_now(ctx: _ReplCtx) -> None:
    """重建输入泵前硬重置 tty（2026-09-15 tty 行规程损坏事故修复）。

    与 _restore_tty 的差别：TCSAFLUSH 会同时丢弃输入队列中滞留的坏字节
    （ICRNL 失效时 \r 不转 \n 留下的半截行），而 TCSADRAIN 只等输出排空。
    重建后新线程面对干净的终端模式，否则换线程照样饿死。
    """
    # getattr 防御：测试 _make_ctx 为 SimpleNamespace 无此字段，非 TTY 场景
    # 也从未写入 → 一律按「无 known-good 基线」处理（不重置，等同跳过）。
    _saved_termios = getattr(ctx, "saved_termios", None)
    if _saved_termios is None:
        return
    try:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSAFLUSH, _saved_termios)
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:  # noqa: BLE001 — fd 已关等场景静默
        pass


def _status_prompt(ctx: _ReplCtx) -> str:
    """prompt 渲染回调：plain 模式显示简短的"灵克>"（保留可读性），
    状态信息走 bottom_toolbar（已有 toolbar_fragments 实现）。

    为什么不在 prompt 里塞 [model|ctx|task|q]？实测发现：
    1) prompt 文本变长后 PT 计算光标位置会偏移 → 输入位置错位
    2) 多余方括号与 | 字符 → 显示多余空格 + 换行错位
    3) 上下文 window=0 时 ctx 总是 ? → 失去常驻感
    把状态信息下沉到底部 toolbar 后,prompt 简短可读,光标稳定。
    """
    status = ctx.status
    input_queue = ctx.input_queue
    if get_output_format() != "plain":
        return "灵克> "
    _refresh_ctx_tokens(ctx)
    status.refresh_cwd()
    status.set_pending(input_queue.pending())
    return "灵克> "


def _toolbar_snapshot(ctx: _ReplCtx) -> Any:
    """工具栏回调的状态快照（2026-09-16 常驻全屏 TUI 配套）。

    原实现快照在 _status_prompt（即 pump 每次调 prompt 时）刷新，全屏形态下
    状态栏由 PT 每帧渲染回调 toolbar_fragments —— 快照必须在此刷新，否则
    cwd/ctx/挂起数永远停在启动值。全屏挂起数取内部提交队列（InputQueue 只
    反映 pump 已搬运的），两项相加去重不必要：提交队列被 prompt 取走后才进
    InputQueue，是先后两级缓冲。

    节流：PT refresh_interval=0.2s 每帧回调，token 估算（遍历全部消息）与
    cwd 探测是重操作 —— 限频 1s；挂起数（两个 deque/queue size）每帧刷新。
    """
    status = ctx.status
    try:
        now = time.monotonic()
        if now - getattr(_toolbar_snapshot, "_last_heavy", 0.0) >= 1.0:
            _toolbar_snapshot._last_heavy = now  # type: ignore[attr-defined]
            _refresh_ctx_tokens(ctx)
            status.refresh_cwd()
        pending = ctx.input_queue.pending() if ctx.input_queue is not None else 0
        full_tui = getattr(ctx.session, "pending_submissions", None)
        if callable(full_tui):
            pending += full_tui()
        status.set_pending(pending)
    except Exception:  # noqa: BLE001 — 状态刷新失败不阻塞渲染
        pass
    return status.snapshot()


# 模块级节流游标（函数属性在多 repl 实例间共享无碍：只是限频，不影响正确性）
_toolbar_snapshot._last_heavy = 0.0  # type: ignore[attr-defined]


def _refresh_ctx_tokens(ctx: _ReplCtx) -> None:
    """刷新底部状态栏的 ctx tokens（_status_prompt 与 _status_refresh 共用）。

    收敛: 原两处逐字相同的 try 导入 + set_ctx + context_window_tokens 解析块
    （维护点 2→1）。
    """
    engine = ctx.engine
    status = ctx.status
    try:
        from lingclaude.core.tool_executor import _estimate_message_tokens

        status.set_ctx(
            _estimate_message_tokens(engine._messages),
            int(getattr(engine.config, "context_window_tokens", None)
                or getattr(engine.config, "max_budget_tokens", 0) or 0),
        )
    except Exception:  # noqa: BLE001
        pass


def _is_full_tui_session(session: Any) -> bool:
    """是否为 P2 全屏 TUI 会话（Q4）。

    全屏 Application 独占 stdin，Esc 归输入框编辑；调用方据此跳过
    Esc 监听线程 / pump（避免 termios 抢占与双读者冲突）。
    """
    return isinstance(session, FullTuiSession)


def _full_tui_output_source(engine: Any) -> Callable[[], list[str]]:
    """构造全屏 TUI 输出窗内容源（会话历史行，用户/助手前缀）。

    从 engine._messages 取用户/助手消息文本；无角色标签时按原样追加。
    闭包内 getattr 防御：消息可能是对象（.role/.content）或 dict。
    """
    def _source() -> list[str]:
        lines: list[str] = []
        for msg in getattr(engine, "_messages", []) or []:
            role = str(getattr(msg, "role", "") or (msg.get("role") if isinstance(msg, dict) else ""))
            text = str(getattr(msg, "content", "") or (msg.get("content") if isinstance(msg, dict) else ""))
            if not text:
                continue
            if role == "user":
                lines.append(f"🧑 用户: {text}")
            elif role == "assistant":
                lines.append(f"🤖 灵克: {text}")
            else:
                lines.append(text)
        return lines

    return _source




def _drain_stdin_buffer() -> None:
    """H17-输入泵修复:清空 stdin 缓冲区（最多 4KB 兜底）。

    multiline 模式下 Ctrl+C 等特殊序列跨多字节触发 UnicodeDecodeError，
    只读一行不足以清空残留字节 → 同一条消息无限循环报错。
    这里用非阻塞 read(4096) 一次性清空缓冲区（TTY 下 safe）。
    H19:粘贴段感知 — 本片含未闭合的 \\x1b[200~（bracketed paste 开标记）
    时立即停手，后续字节留在缓冲区由正常输入路径消费到 \\x1b[201~ 闭合；
    否则粘贴正文会被本函数当"残留字节"整段吞掉。
    """
    paste_start = b"\x1b[200~"
    try:
        if sys.stdin.isatty():
            import select

            fd = sys.stdin.fileno()
            while True:
                r, _, _ = select.select([fd], [], [], 0.01)
                if not r:
                    break
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                if paste_start in chunk and not chunk.endswith(b"\x1b[201~"):
                    # 未闭合粘贴段:立即停手,正文留给正常输入路径
                    break
    except Exception:  # noqa: BLE001 — 非 TTY/无 termios 时静默跳过
        pass

def _read_input(ctx: _ReplCtx) -> str:
    # P1（2026-09-15 tty 行规程损坏事故）:失活降级时若复用同一个 PT session，
    # 其底层 asyncio Application 可能已损坏（pump 线程卡死/断言）→ 降级直读
    # 同样失效。_next_input 在 pump 失活降级前置 ctx.fallback_read=True，这里
    # 改用独立裸 input()（无 PT 事件循环依赖），彻底绕开损坏的 session ——
    # 隔离是 P0 tty 重置的互补层。
    if getattr(ctx, "fallback_read", False):
        try:
            _line = input(_status_prompt(ctx) if get_output_format() == "plain" else "灵克> ")
            if _line.strip():
                ctx.session.push_to_history(_line)
            return _line
        except EOFError:
            raise
        except KeyboardInterrupt:
            return ""
        except UnicodeDecodeError:
            # H17-输入泵修复:multiline 模式下 Ctrl+C 等特殊序列跨多字节，
            # 残留字节会触发循环报错导致用户无法继续输入。
            # 清空缓冲区（最多 4KB）后重试一次 prompt，再失败才放弃。
            _drain_stdin_buffer()
            try:
                return ctx.session.prompt(_status_prompt(ctx))
            except UnicodeDecodeError:
                print("[输入编码错误，请检查终端编码设置]")
                return ""
    session = ctx.session
    try:
        text = ctx.session.prompt(_status_prompt(ctx))
        # 审计#11 修复:prompt_toolkit 的 PromptSession 已自动写 FileHistory,
        # 这里再 push 一次导致 ~/.lingclaude/history 每条重复。
        # push_to_history 语义改为「仅兜底实现需要手动记」→ 见 interface.py。
        if text.strip():
            session.push_to_history(text)
        return text
    except UnicodeDecodeError:
        # H17-输入泵修复:同上，清全部缓冲区后重试，再失败才放弃。
        _drain_stdin_buffer()
        try:
            text = ctx.session.prompt(_status_prompt(ctx))
            if text.strip():
                session.push_to_history(text)
            return text
        except UnicodeDecodeError:
            print("[输入编码错误，请检查终端编码设置]")
            return ""


def _drain_pending_notice(ctx: _ReplCtx) -> None:
    """H17-TUI:退出前清空挂起队列并列出被丢弃项（半成品不执行、不落盘）。"""
    input_queue = ctx.input_queue
    dropped = input_queue.drain()
    if dropped:
        print(f"[退出] 丢弃 {len(dropped)} 条挂起输入：")
        for d in dropped[:5]:
            print(f"  - {d[:60]}")
        if len(dropped) > 5:
            print(f"  … 等共 {len(dropped)} 条")


def _bye(ctx: _ReplCtx, newline_first: bool = False) -> None:
    """统一退出致意（2026-09-16 常驻全屏 TUI 配套）。

    全屏会话必须先 close()：退出全屏 alt-screen + 还原 stdout 代理，
    否则「再见！/丢弃清单」会被写进输出窗随全屏一起消失（用户看不到退出）。
    非全屏会话（无 close 方法）保持原 _drain + print 序列不变。
    """
    try:
        close = getattr(ctx.session, "close", None)
        if callable(close) and getattr(ctx.session, "_running", False):
            close()
    except Exception:  # noqa: BLE001 — 退出路径不因收尾失败而挂死
        pass
    _drain_pending_notice(ctx)
    print("\n再见！" if newline_first else "再见！")


def _stdin_readable(timeout: float = 0.0) -> bool:
    """stdin 是否有数据可读（非阻塞探测）。

    select 在非 TTY（管道/文件重定向）下对常规文件 fd 恒报可读 ——
    但 pump 模式只会在 TTY 下启用，这里仅作兜底；探测失败一律视为
    「不可读」（宁可误判空闲，不可误判卡死打断正常输入）。
    """
    try:
        import select

        r, _, _ = select.select([sys.stdin], [], [], timeout)
        return bool(r)
    except Exception:  # noqa: BLE001 — 平台差异/无效 fd 兜底
        return False


def _maybe_stall_escape(ctx: _ReplCtx, idle_loops: int, last_check_t: float) -> int:
    """pump 失活复合判定 + 逃生门（2026-09-12 事故:输入假死/Ctrl+D 失效）。

    核心难题：pump「正常等输入」与「病态卡死」两者 is_alive() 皆 True、
    队列皆空，原逻辑无法区分 → 主循环无限空转。突破口是 stdin 可读性：

      空闲正常：stdin 不可读（无输入）→ 心跳停滞属正常，不干预
      用户打字 : stdin 可读 → 正常 pump 瞬间消费；若连续多轮仍可读
                 且队列空、心跳无推进 → pump 未消费，判定失活
      （pump 仅 TTY 启用，管道 EOF 场景由 is_alive()==False 分支覆盖）

    判定为失活后：set interrupt_event 尝试唤醒 prompt_toolkit 阻塞读；
    然后 stop() 停掉 pump 线程（若被唤醒则干净退出），确保主线程成为
    唯一 stdin 读者后再降级为阻塞直读 —— 逃生门（此前这类假死只能
    kill -9，会话靠逐轮落盘兜底）。绝不在 pump 线程仍存活时直接直读，
    否则构成双读者（PT 并发 AssertionError，2026-09-08 事故）。
    """
    input_pump = ctx.input_pump
    # 心跳停滞时长（prompt 返回即打拍，卡死则停滞）
    beat_idle = time.monotonic() - input_pump.last_beat()
    readable = _stdin_readable(0.0)
    if beat_idle >= 8.0 and readable:
        # 连续两轮（间隔约 1s）确认，避免瞬时误判
        if last_check_t < 0 or time.monotonic() - last_check_t >= 1.0:
            if idle_loops >= 1:
                input_pump.dead = True
                input_pump.death_reason = "失活:stdin 可读但心跳停滞(输入泵卡死)"
                print("\n[输入泵失活] 已降级为阻塞输入模式（可继续使用；Ctrl+D 退出）", file=sys.stderr)
                try:
                    ctx.session.interrupt_event().set()
                except Exception:  # noqa: BLE001
                    pass
                # 关键：停掉 pump 线程（唤醒则干净退出），主线程独占 stdin
                # 后再降级 —— 避免双读者（PT 并发 AssertionError 事故）。
                try:
                    input_pump.stop()
                except Exception:  # noqa: BLE001
                    pass
                return 0
            return time.monotonic()
    # 2026-09-13（灵安审计 P0-③，第 5/6 次假死根因）：超长心跳停滞兜底。
    # 现象：无孤儿进程抢占 stdin、fd0 正常指向 pts，但 input-pump 线程卡在
    # prompt_toolkit.prompt() 的 select()（selectors.py:468）永不返回——select
    # 状态损坏时 _stdin_readable(0.0) 恒 False，上方「readable」逃生门永不触发，
    # 主线程永久等 input_queue.get()，只能人肉 kill 重启。
    # 兜底：即便 stdin 判为不可读，心跳若超长停滞（select 失效/上游输入断的
    # 强信号）也强制重建 pump。触发前记录死因留痕。input_pump.start() 已有
    # 单读者防重入 + 旧线程 join(2s) 兜底（H17-TUI 修复），可安全重建。
    #
    # 2026-09-15（会话问题重构 P0-1）：阈值 1800s → 60s + 重建冷却 + 二次降级。
    # 1800s 意味着 select 状态损坏时用户被冻死 30 分钟才重建（把「等 30 分钟」
    # 当作可接受，与"假死"体感一致）。60s 是安全上限：正常等输入态用户 1 分钟
    # 不打字（读输出/思考）不会被误伤；select 失效的假死能在 1 分钟内自愈。
    # 重建后设冷却：重建本身会打拍，若 30s 内再触发说明重建无效（tty 损坏/
    # PT session 已不可救——"强制重建也没用"），第二次直接 dead + fallback_read
    # 永久降级裸 input()（绕开损坏的 PT session，见 _read_input 的隔离读），
    # 不再反复 churn。
    if beat_idle >= 60:
        # 二次触发：上次重建后仍无心跳 → 重建无效，永久降级（不再次重建）
        if ctx.stall_rebuilds >= 1:
            input_pump.dead = True
            input_pump.death_reason = "失活:重建后心跳仍停滞(PT session 不可救,永久降级)"
            print("\n[输入泵失活] 重建无效，已永久降级为阻塞输入模式（可继续使用）", file=sys.stderr)
            try:
                ctx.session.interrupt_event().set()
            except Exception:  # noqa: BLE001
                pass
            # 2026-09-16: 永久降级前停掉 pump 线程（唤醒则干净退出），
            # 确保主线程成为唯一 stdin 读者 —— 与 _next_input dead 分支
            # 同款双读者防护（PT 并发 AssertionError，2026-09-08 事故）。
            try:
                input_pump.stop()
            except Exception:  # noqa: BLE001
                pass
            ctx.fallback_read = True  # 永久裸 input() 隔离读（_read_input 路径）
            return 0
        input_pump.dead = True
        input_pump.death_reason = "失活:心跳超长停滞(select 疑似失效或上游输入断)"
        print("\n[输入泵失活] 心跳超长停滞，强制重建输入泵（可继续使用）", file=sys.stderr)
        # 2026-09-15（tty 行规程损坏事故）:重建不能只换线程 —— 假死根因之一
        # 是 tty 模式损坏（ICRNL 失效 → \r 不转 \n → read 永不返回），新线程面对
        # 同一个坏 tty 照样饿死。重建前先恢复启动时保存的 known-good termios，
        # 让新线程面对干净的终端模式（与 _restore_tty 同语义，TCSAFLUSH 清滞
        # 留的坏字节；saved_termios 为 None 时静默跳过）。
        _reset_tty_now(ctx)
        try:
            input_pump.stop()
            # 重建：dead 复位由 start() 内部处理（新线程独立 _stop 事件）；
            # start() 单读者防重入，旧线程未退时 join(2s) 后放弃。
            input_pump.dead = False
            input_pump.start()
            ctx.stall_rebuilds += 1  # 冷却：记录本次重建，供二次触发判定
        except Exception as _rebuild_err:  # noqa: BLE001 — 重建失败不致命，主循环仍降级直读
            # P2: 重建失败必须留痕 —— 此前 except: pass 吞掉失败原因，无从诊断。
            _logger.warning(
                "输入泵重建失败（死因=%s，err=%s）",
                input_pump.death_reason,
                _rebuild_err,
                exc_info=True,
            )
            # 重建失败（异常）→ 也走永久降级，避免下次再撞
            ctx.fallback_read = True
        return 0
    # 未触发:打点重置探测窗口
    return last_check_t if last_check_t >= 0 and beat_idle >= 8.0 else -1.0


def _next_input(ctx: _ReplCtx) -> str:
    """pump 模式取输入：唯一来源是队列（EOF 哨兵转 EOFError）。
    pump 线程死亡/失活时永久降级为阻塞直读。"""
    input_pump = ctx.input_pump
    _pump_mode = ctx.pump_mode
    input_queue = ctx.input_queue
    # 失活探测游标（2026-09-12）:小于 0 表示未进入疑区
    _stall_check_t = -1.0
    _idle_loops = 0
    _seen_degraded = False  # P1-Interrupt 残留修复:避免重复 stop() 自残
    while True:
        if input_pump.dead or not _pump_mode:
            # 失活/死亡降级为阻塞直读前，必须先 stop() 停掉 pump 线程
            # （set _stop + interrupt_event 唤醒阻塞 read + join 2s 兜底）。
            # 此前只「等 1s 退出窗口」不唤醒不 stop —— pump 卡 select 时线程
            # 仍存活，主线程裸 session.prompt() 直读构成双读者（PT 并发
            # AssertionError，2026-09-08 事故）。stop() 不重置 dead，
            # 下方 fallback_read 判定不受影响。
            if input_pump.dead:
                try:
                    input_pump.stop()
                except Exception:  # noqa: BLE001 — 极端卡死时 stop 失败不致命
                    pass
                # stop() 的 join(2s) 唤醒失败时线程仍存活 —— 再给 1s 兜底
                # 窗口；仍不退说明彻底卡死（死等=用户无法输入），直接降级直读。
                _t0 = time.monotonic()
                while input_pump.is_alive() and time.monotonic() - _t0 < 1.0:
                    time.sleep(0.05)
            # P1（2026-09-15）:pump 失活/死亡说明 PT session 可能已损坏（线程卡
            # select / Application 断言），复用同一 session 直读照样失效 → 置
            # ctx.fallback_read 让 _read_input 走裸 input() 隔离读。仅 pump 模式
            # （PT 后台线程）失活时需要隔离；非 pump 模式 session 本就是
            # FallbackSession/主线程独占，走正常 prompt()。仅首次降级前重置 tty。
            if input_pump.dead and not _seen_degraded:
                _reset_tty_now(ctx)
                _seen_degraded = True
            ctx.fallback_read = bool(input_pump.dead and _pump_mode)
            return _read_input(ctx)
        item = input_queue.get(timeout=0.3)
        if item is None:
            # 队列空且 pump 线程已退出（未置 dead）→ EOF 已入队/线程异常退出
            if not input_pump.is_alive():
                item = input_queue.get(timeout=0.5)
                if item is None:
                    raise EOFError
            elif not _is_full_tui_session(ctx.session):
                # pump 活着但队列空:区分「正常等输入」与「病态卡死」。
                # 常驻全屏形态跳过失活探测（2026-09-16）:pump 阻塞在内部提交
                # 队列（Condition 轮询，不碰 stdin），不可能楔死；而探测依赖
                # 「心跳停滞 + stdin 可读」——用户合法发呆 8s 即被误判失活，
                # 触发 stop() + 降级直读，直接与 PT 事件循环构成双读者。
                _stall_check_t = _maybe_stall_escape(ctx, _idle_loops, _stall_check_t)
                if _stall_check_t > 0:
                    _idle_loops += 1
                elif _stall_check_t == 0:
                    _stall_check_t = -1.0
                    _idle_loops = 0
                    continue
            continue
        # 有输入:清除疑区游标
        _stall_check_t = -1.0
        _idle_loops = 0
        if InputQueue.is_eof(item):
            raise EOFError
        return item


def _status_refresh(ctx: _ReplCtx) -> None:
    status = ctx.status
    engine = ctx.engine
    input_queue = ctx.input_queue
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
    _refresh_ctx_tokens(ctx)
    status.set_pending(input_queue.pending())


def _shutdown_pump(ctx: _ReplCtx) -> None:
    """会话收尾：优雅退出 pump（唯一 stdin 读者）。

    事故背景（2026-09-09）：pump 是 daemon 线程且阻塞在 prompt() 里，
    解释器 finalize 阶段 daemon 线程持有 stdout 缓冲锁 →
    「Fatal Python error: _enter_buffered_busy」核心转储。
    此处通过 PT 的 app.exit(EOFError) 让阻塞中的 prompt() 抛 EOF 返回，
    线程干净退出后再 join；优雅失败兜底 os._exit 跳过 finalize。
    """
    _pump_mode = ctx.pump_mode
    input_pump = ctx.input_pump
    session = ctx.session
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


def _run_stream_turn(ctx: _ReplCtx, prompt: str) -> str:
    """执行一轮流式 turn（Esc 打断/watchdog/逐轮落盘/指标收尾）。

    返回 _TURN_QUIT（退出请求）或 ""（继续主循环）。原 _interactive_loop
    if engine._provider: 分支原样迁移。"""
    engine = ctx.engine
    status = ctx.status
    session = ctx.session
    _pump_mode = ctx.pump_mode
    input_pump = ctx.input_pump
    _status_bar_active = ctx.status_bar_active
    # Step 4: 流式 spinner + Esc 真打断（后台线程 → interrupt_event）
    response_content = ""
    got_first_token = False
    interrupted = False
    observed_tool_calls = 0
    observed_tool_errors = 0
    observed_text_deltas = 0
    observed_stream_error = False
    turn_output_tokens = 0  # N5: 本轮(非累计) output token, done 事件携带
    turn_t0 = time.monotonic()  # P1.1: turn 级耗时计时起点
    usage_t0 = dict(engine.get_stats().get("usage") or {})  # P1.1: delta 基线
    status.set_task("生成中")
    # 后台线程监听 Esc（生成态 stdin 空闲），set 后中断生成；
    # 仅 TTY 启动，且用 _esc_stop 保证回合结束线程必退（审计#6）
    _esc_stop = threading.Event()
    _esc_thread = None
    # H17-TUI 架构定稿: pump 会话级运行（循环前已 start），生成期继续
    # 收文本入队，中断由 Ctrl+C 承担（pump 线程内 KeyboardInterrupt 清行）。
    # 仅当 pump 不可用（非 PT/非 TTY/pump 已死）时降级 Esc 监听线程。
    # P2 全屏 TUI（Q4）：全屏 Application 独占 stdin，Esc 归输入框编辑，
    # 中断由 Ctrl+C 承担 —— 不启动 Esc 监听（避免 termios 抢占冲突）。
    _is_full_tui = _is_full_tui_session(session)
    if not _is_full_tui and (_pump_mode and input_pump.dead or not _pump_mode) and sys.stdin.isatty():
        _esc_thread = threading.Thread(
            target=_esc_listen_loop, args=(session, _esc_stop), daemon=True,
        )
        _esc_thread.start()
    # N5b: 流内停滞 watchdog — 旁路线程监视事件心跳，只告警不打断（详见模块 docstring）
    _wd = StreamWatchdog()
    _wd.start()
    try:
        # P0-Interrupt 残留修复（2026-09-16）: 失活重建路径 set 的 interrupt
        # 本意是唤醒阻塞中的 PT prompt，降级裸 input() 后没人清它，永久残留
        # → 每条输入进流循环即被判打断。此处清空确保流启动时状态干净。
        session.interrupt_event().clear()
        # 2026-09-16（TUI 输入泵问题修复）: streaming 期间 prompt() 不阻塞，
        # pump 线程异步收集用户输入（方向键等），主线程不被 session.prompt()
        # 卡死，防止 pump 线程 + 主线程双阻塞导致假死。
        session.set_streaming(True)
        for event in engine.stream_call_model(prompt):
            _wd.touch(str(event.get("type", "")))
            if session.interrupt_event().is_set():
                interrupted = True
                print("\n[已打断]")
                break
            if not got_first_token and event.get("type") in ("text_delta", "error"):
                got_first_token = True
                sys.stdout.write(" " * 40 + "\r")  # UI 对齐:清 40 列
                sys.stdout.flush()
            _handle_stream_event(event)
            if event.get("type") == "round_end":
                # 2026-09-15（会话问题重构 P1-1）: round 边界消费挂起队列。
                # 斜杠命令立即执行；普通文本插队（queued_next，turn 结束后
                # 直接作为下一轮输入）；EOF/quit 中止当前 turn。
                _consume_queue_round(ctx, int(event.get("round_idx", 0)))
                if ctx.processor.quit_requested:
                    interrupted = True
                    print("\n[已中止]")
                    break
                continue
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
                turn_output_tokens = int(
                    (event.get("usage") or {}).get("output_tokens", 0) or 0
                )
    except KeyboardInterrupt:
        # pump 模式下 Ctrl+C 承担中断语义（Esc 让位给输入框）
        interrupted = True
        print("\n[已打断]")
    finally:
        # H17-TUI 修复: 清理入 finally — 流中非 KeyboardInterrupt 异常（网络错等）
        # 也不泄漏监听线程。审计#6: 先停线程再清 interrupt。
        # pump 会话级运行，此处不再 stop（唯一 stdin 读者地位不变）。
        _wd.stop()  # N5b: 流收尾（正常/打断/异常），watchdog 停表
        session.set_streaming(False)  # 流结束，恢复阻塞 prompt()
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
        if persist_result.is_error and get_output_format() == "plain":
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
        turn_output_tokens=turn_output_tokens,
        turn_input_delta=max(
            0,
            int((engine.get_stats().get("usage") or {}).get("input_tokens", 0) or 0)
            - int(usage_t0.get("input_tokens", 0) or 0),
        ),
        turn_duration_s=round(time.monotonic() - turn_t0, 3),
    )
    return ""



def _requeue_extras(ctx: _ReplCtx, extras: list[str]) -> None:
    """把收集的额外输入按原顺序回填队列（逆序 put 保持 FIFO 语义）。

    真重复收敛：_consume_queue 三处（EOF / quit / 正常收尾）逐字相同的
    `for _x in reversed(extras): ctx.input_queue.put(_x)` 回填循环，维护点 3→1。
    """
    for _x in reversed(extras):
        ctx.input_queue.put(_x)


def _consume_queue_round(ctx: _ReplCtx, round_idx: int) -> None:
    """2026-09-15（会话问题重构 P1-1）: round 边界消费挂起队列。

    与 _consume_queue（turn 结束才消费）不同，此函数在每一个 tool round
    完成、下一 round 即将开始（或 turn 即将结束）时被调用，实现「一个
    session round 完成后即可读被挂起的命令」：

    - 斜杠命令（/model /schedule /help 等）→ 立即执行（改 CLI 状态/engine
      配置，不进 engine._messages，无跨线程竞态——与 _consume_queue 同语义）
    - 普通文本 → 记入 ctx.queued_next（插队：当前 tool 轮继续，turn 结束后
      作为下一轮输入直接执行，不等用户再打字）
    - EOF/quit → 记 quit_requested（流循环检查后中止）

    纯消费不改引擎内部状态：引擎继续自己的 tool 轮，这里只读队列。
    """
    processor = ctx.processor
    input_queue = ctx.input_queue
    queued_next = None
    extras: list[str] = []
    while True:
        item = input_queue.get(timeout=0.0)  # 非阻塞：round 边界不等队列
        if item is None:
            break
        if InputQueue.is_eof(item):
            _requeue_extras(ctx, extras)
            processor.quit_requested = True
            return
        if processor.handle(item):
            if processor.quit_requested:
                _requeue_extras(ctx, extras)
                return
            continue
        if queued_next is None:
            queued_next = item
        else:
            extras.append(item)
    _requeue_extras(ctx, extras)
    if queued_next is not None:
        # 插队：当前 round 的 tool 轮继续，turn 结束后作为下一轮输入
        ctx.queued_next = queued_next
        if get_output_format() == "plain":
            print(f"\n[round {round_idx} 插队] {queued_next[:40]}")


def _consume_queue(ctx: _ReplCtx) -> str:
    """H17-TUI: 消费生成期挂起队列（斜杠命令即时执行；首个文本成为下一轮输入，
    余下重新排队保持顺序；EOF/quit 视为退出请求）。原 _interactive_loop
    尾部队列消费段原样迁移。"""
    from lingclaude.cli.input_queue import InputQueue

    processor = ctx.processor
    input_queue = ctx.input_queue
    # H17-TUI: 消费生成期挂起队列（斜杠命令即时执行；首个文本成为下一轮输入，
    # 余下重新排队保持顺序；EOF/quit 视为退出请求）
    # 修复: 余项不可边 drain 边回填同一队列 — get 永远取回回填项，循环永不
    # break（2026-09-08 死循环事故）。先收集，循环结束后再回填。
    queued_next = None
    extras: list[str] = []
    # 修复: 不再以 not input_pump.dead 为循环条件 — pump 死亡时挂起队列
    # 里的输入仍须消费（2026-09-09: pump 静默死亡 → 6 条挂起全部滞留丢弃）。
    # 队列空时 get 超时返回 None 自然 break。
    while True:
        item = input_queue.get(timeout=0.2)
        if item is None:
            break
        if InputQueue.is_eof(item):
            _requeue_extras(ctx, extras)
            return _TURN_QUIT
        if processor.handle(item):
            if processor.quit_requested:
                _requeue_extras(ctx, extras)
                return _TURN_QUIT
            continue
        if queued_next is None:
            queued_next = item
        else:
            extras.append(item)
    _requeue_extras(ctx, extras)
    # 2026-09-15（会话问题重构 P1-1）: round 边界消费（_consume_queue_round）
    # 可能已把普通文本记为 ctx.queued_next（插队）。此处仅当 round 未设置时
    # 才覆盖 —— 插队的文本优先，避免 turn 结束后被覆盖丢失。
    if ctx.queued_next is None:
        ctx.queued_next = queued_next
    if processor.quit_requested:
        return _TURN_QUIT
    return queued_next if queued_next is not None else ""


def _interactive_loop(engine: "QueryEngine", first_prompt: str | None) -> int:
    version = _get_version()
    if get_output_format() == "plain":
        print(f"灵克 v{version} — 交互模式（'exit'/'quit'/Ctrl+D 退出，Ctrl+C 清行）")
        print(f"Provider: {_provider_status(engine)}")
        print()

    ctx = _ReplCtx(engine=engine, status=None, session=None)  # type: ignore[arg-type]
    # F12e:TTY 终端状态保护 — 进入时保存 termios,退出时恢复(_restore_tty)。
    ctx.saved_termios = None
    if sys.stdin.isatty():
        try:
            ctx.saved_termios = termios.tcgetattr(sys.stdin.fileno())
        except Exception:  # noqa: BLE001 — 无 termios 平台静默跳过
            ctx.saved_termios = None

    # RFC §3.3: I/O 抽象层 — LINGCLAUDE_CLI_MODE=plain 或非 TTY → FallbackSession
    # Step 3: Tab 补全（prompt_toolkit WordCompleter）
    _completer: Any = None
    try:
        from prompt_toolkit.completion import WordCompleter

        _completer = WordCompleter(SLASH_COMPLETER_WORDS, ignore_case=True)
    except ImportError:
        _completer = None
    session: PromptSessionInterface = create_session(completer=_completer)
    ctx.session = session

    # P2 常驻全屏 TUI：输出源注入 + 会话级启动（stdout 代理接管，生成期
    # 输入框常驻）。pump 启动条件放行 FullTuiSession（InputPump 调
    # session.prompt() 阻塞在内部提交队列，stdin 唯一读者仍是 PT 事件循环，
    # 无双读者；生成期 prompt 不消费 interrupt，Ctrl+C 打断归流循环）。
    _is_full_tui_session_obj = isinstance(session, FullTuiSession)
    if _is_full_tui_session_obj:
        session.install_output_source(_full_tui_output_source(engine))

    # H17-TUI: 状态栏 + 挂起队列接线 — 设计文档 docs/cli/TUI_BOTTOM_INPUT_DESIGN.md
    # 组件（status.py/input_queue.py/interface.py）此前已就绪但从未被接线。
    # 三件套在此构造；仅 TTY+plain 生效，json/jsonl/Fallback 自动降级。
    from lingclaude.cli.input_queue import InputPump
    from lingclaude.cli.status import StatusModel

    status = StatusModel()
    ctx.status = status
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
    ctx.input_queue = InputQueue()
    ctx.processor = SlashCommandProcessor(engine, status)
    ctx.input_pump = InputPump(session, ctx.input_queue, prompt_text=lambda: _status_prompt(ctx))
    ctx.pump_mode = False

    # H17-TUI 步骤2: 安装状态栏（PT 实现生效；Fallback no-op 自动降级）。
    # TUI_ONLY_SNAPSHOT: json/jsonl 输出模式禁用；每轮 refresh_cwd 对齐 /cd 场景。
    ctx.status_bar_active = False
    if get_output_format() == "plain":
        try:
            session.install_bottom_toolbar(
                lambda: toolbar_fragments(_toolbar_snapshot(ctx))
            )
            ctx.status_bar_active = not isinstance(session, FallbackSession)
        except Exception:  # noqa: BLE001 — 状态栏安装失败不阻塞交互
            ctx.status_bar_active = False

        # H17-TUI: pump 会话级启动 — PT 形态下唯一 stdin 读者（H17 架构：PT
        # Application 并发运行 → AssertionError，2026-09-08 事故）。
        # P2 常驻全屏：InputPump 调 session.prompt() 只等内部提交队列（不碰
        # stdin），PT 事件循环仍唯一读 stdin —— 同样满足单读者，放行。
        if (
            ctx.status_bar_active
            and sys.stdin.isatty()
            and (
                isinstance(session, PromptToolkitSession)
                or _is_full_tui_session_obj
            )
        ):
            ctx.pump_mode = True
            if _is_full_tui_session_obj:
                # 先启动常驻全屏（stdout 代理接管 → 状态栏/输出进窗），
                # 再启动 pump（pump 线程阻塞在提交队列）。
                session.start()
                # 欢迎横幅打印在 start() 之前的主屏缓冲，alt-screen 下不可见
                # —— 补写进输出窗（Ctrl+C 语义按全屏键位描述）。
                session.append_output(
                    f"灵克 v{version} — 交互模式（'exit'/'quit'/Ctrl+D 退出，"
                    "Ctrl+C 中断/清行）\n"
                    f"Provider: {_provider_status(engine)}\n"
                )
            ctx.input_pump.start()

    prompt = first_prompt or ""
    while True:
        if not prompt:
            try:
                prompt = _next_input(ctx).strip()
            except (EOFError, KeyboardInterrupt):
                _bye(ctx, newline_first=True)
                break
        if prompt.lower() in ("exit", "quit", "q"):
            _bye(ctx)
            break
        if not prompt:
            continue
        # T1-7: 斜杠命令优先消费
        if ctx.processor.handle(prompt):
            if ctx.processor.quit_requested:
                _bye(ctx)
                break
            prompt = ""
            continue

        if engine._provider:
            action = _run_stream_turn(ctx, prompt)
            if action == _TURN_QUIT:
                _bye(ctx, newline_first=True)
                break
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

        # H17-TUI: 消费生成期挂起队列
        action = _consume_queue(ctx)
        if action == _TURN_QUIT:
            _bye(ctx, newline_first=True)
            break

        _feed_behavior_to_daemon(engine, None)

        if ctx.queued_next is not None:
            prompt = ctx.queued_next
            # 2026-09-15 修复: 取用后立即清空，否则同一插队文本被永久重复执行
            # （P1-1 引入的回归："[排队执行] xxx" 无限循环刷屏）。
            ctx.queued_next = None
            next_prompt_hint = f"[排队执行] {prompt[:40]}"
            if get_output_format() == "plain":
                print(next_prompt_hint)
        else:
            prompt = ""
            try:
                prompt = _next_input(ctx).strip()
            except (EOFError, KeyboardInterrupt):
                _bye(ctx, newline_first=True)
                break

    _shutdown_pump(ctx)

    # P0-2: 退出时持久化会话（--continue/--resume 的数据来源）。
    if engine._conversation and get_output_format() == "plain":
        result = engine._session_persister.persist_session()
        if result.is_ok:
            print(f"[会话已保存] {result.data}")

    stats = engine.get_stats()
    # F12e:先恢复终端状态,再打统计 — 统计输出落在干净的行首。
    _restore_tty(ctx)
    print()
    print_session_summary(SessionSummary(
        turns=stats["turns"],
        session_id=stats["session_id"],
        usage=stats["usage"],
        behavior={},
    ))
    _maybe_run_daemon_cycle()
    return 0
