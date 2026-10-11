"""R9-2: 会话终止前强制交接快照（kill-safe beacon）。

缺口（2026-10-10 实战暴露）: 交互会话的消息已逐轮落盘（repl.py:1315，
2026-09-06 事故修复），todo 有 JSON 原子写——但「任务级交接状态」（正在
干什么 / 最近指令 / 为何中断）只存在于进程内存与模型上下文里。用户重启
终端 / kill / 断电后，接力无从谈起（当日主会话手动交接后用户重启进程，
无任何自动机制介入）。

机制（三层，全部尽力而为，任何一层失败不影响其他层与主流程）:
1. 周期信标: daemon 线程每 BEACON_INTERVAL 秒原子写一份交接快照
   （~/.lingclaude/beacons/<project>/<sid>.json）。连 SIGKILL 都覆盖——
   文件永远只落后 ≤10s，天然满足 R9-2 AC「≤10s 前的状态快照」。
2. 信号快刷: prepare_signal_flush() 在常规期预构建 payload 字节串并预开
   fd；SIGTERM/SIGHUP handler 内只做 os.ftruncate + os.pwrite（两者均为
   async-signal-safe，沿 full_tui._signal_restore_handler 的纪律: handler
   内无分配、无锁、无任意 Python 调用）。
3. 继承发现: 新会话启动时 detect_dead_predecessor() 扫描信标目录——
   beacon 文件存在 + 其 pid 已死 = 异常死亡候选，打印继承提示衔接
   --continue / R9 接力。正常退出路径显式清除 beacon（黑匣子语义:
   文件还在 = 可疑死亡）。

边界: 不做自动重登/自动接力决策（blocked_on_human 原则）——本模块只负责
「把状态留在磁盘上 + 告诉下一个会话去哪捡」。
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BEACON_INTERVAL = 10.0  # 秒；R9-2 AC 的「≤10s 前快照」即由此保证
STALE_HOURS = 48  # 超过此年龄的 beacon 不再作为继承提示

# pytest / 单轮进程旁路开关（conftest 同 BUS_LISTENER 先例）
ENV_DISABLE = "LINGCLAUDE_KILL_BEACON"


def _beacons_root() -> Path:
    return Path.home() / ".lingclaude" / "beacons"


def _project_key(project_path: str = "") -> str:
    from lingclaude.core.session import _project_dir_name

    return _project_dir_name(project_path or os.getcwd())


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 别人的活进程
    except OSError:
        return False
    return True


class KillBeacon:
    """会话级 kill-safe 信标。绑定 engine 的最小读取面（鸭子类型，缺属性容错）。"""

    def __init__(self, engine: Any, project_path: str = "", interval: float = BEACON_INTERVAL) -> None:
        self._engine = engine
        self._project_path = project_path or os.getcwd()
        self._interval = max(1.0, float(interval))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sig_fd: int | None = None
        self._sig_payload: bytes = b"{}"
        sid = getattr(engine, "session_id", None) or "unknown"
        self.path = _beacons_root() / _project_key(self._project_path) / f"{sid}.json"

    # ── 采集（常规线程语境，允许任何 Python 调用）────────────────────────

    def _collect(self) -> dict[str, Any]:
        eng = self._engine
        msgs = getattr(eng, "_messages", None) or []
        # 最近一条用户输入 = 消息镜像尾部倒数第 2 条（成对 append 的约定，
        # 见 repl.py 双写修复注释）；取不到就留空，绝不因此失败。
        last_input = ""
        if len(msgs) >= 2:
            last_input = str(msgs[-2])[:500]
        todos: list[dict[str, Any]] = []
        try:
            store = getattr(eng, "_todo_store", None)
            if store is not None:
                todos = [t.to_dict() for t in store.list()]
        except Exception:  # noqa: BLE001 — 尽力而为
            todos = []
        usage = {}
        try:
            usage = (eng.get_stats() or {}).get("usage") or {}
        except Exception:  # noqa: BLE001
            usage = {}
        return {
            "session_id": getattr(eng, "session_id", "") or "",
            "pid": os.getpid(),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "project_path": self._project_path,
            "turns": int(
                ((eng.get_stats() or {}).get("turns", 0) if hasattr(eng, "get_stats") else 0)
                or getattr(eng, "_turn_count", 0)
                or 0
            ),
            "messages_count": len(msgs),
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "last_user_input": last_input,
            "todos": todos,
            "note": "R9-2 auto beacon",
        }

    def write_beacon(self, reason: str = "periodic") -> bool:
        """原子写信标。返回是否成功；绝不抛异常（黑匣子不能反过来杀死会话）。"""
        try:
            data = self._collect()
            # 无身份不写：session_id 为空的快照无法 --continue，且会被
            # detect_dead_predecessor 当作「unknown 前任异常死亡」误报
            # （E2E 实测教训 2026-10-10 unknown.json）。
            if not (data.get("session_id") or "").strip():
                return False
            data["reason"] = reason
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f".tmp{os.getpid()}")
            tmp.write_bytes(payload)
            os.replace(tmp, self.path)
            self._sig_payload = payload  # 同步给信号快刷（最新内容）
            return True
        except Exception as e:  # noqa: BLE001 — 黑匣子尽力而为
            logger.debug("kill_beacon 写入失败: %s", e)
            return False

    # ── 信号快刷（handler 语境：只准 async-signal-safe 调用）───────────────

    def prepare_signal_flush(self) -> None:
        """预开 fd。之后信号 handler 只做 ftruncate+pwrite（安全调用面）。"""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self._sig_fd is None:
                self._sig_fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT, 0o600)
        except OSError as e:
            logger.debug("kill_beacon prepare 失败: %s", e)
            self._sig_fd = None

    def signal_flush(self) -> None:
        """信号 handler 专用：把预构建的最新 payload 刷到预开 fd。

        仅 os.ftruncate / os.pwrite / os.write —— async-signal-safe。
        必须先 prepare_signal_flush() 且 write_beacon() 至少跑过一次。
        """
        fd = self._sig_fd
        if fd is None:
            return
        try:
            os.ftruncate(fd, 0)
            os.pwrite(fd, self._sig_payload, 0)
        except OSError:
            pass

    # ── 生命周期 ────────────────────────────────────────────────────────

    def start(self) -> None:
        self.write_beacon(reason="startup")
        self.prepare_signal_flush()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="kill-beacon")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self.write_beacon(reason="periodic")

    def stop(self, clean: bool = True) -> None:
        """停线程。clean=True（正常退出）清除 beacon 文件（黑匣子语义）。"""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._sig_fd is not None:
            try:
                os.close(self._sig_fd)
            except OSError:
                pass
            self._sig_fd = None
        if clean:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass


# ── 继承发现（新会话启动侧）────────────────────────────────────────────

def detect_dead_predecessor(project_path: str = "", exclude_pid: int | None = None) -> dict[str, Any] | None:
    """扫描本项目的 beacon：文件在 + pid 已死 = 异常死亡候选，返回最新一份。

    exclude_pid: 当前进程（防把自己的 beacon 当前任）。
    """
    root = _beacons_root() / _project_key(project_path)
    if not root.is_dir():
        return None
    me = exclude_pid if exclude_pid is not None else os.getpid()
    cutoff = datetime.now() - timedelta(hours=STALE_HOURS)
    best: tuple[datetime, dict[str, Any]] | None = None
    try:
        candidates = sorted(root.glob("*.json"))
    except OSError:
        return None
    for f in candidates:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            pid = int(data.get("pid", 0))
            ts = datetime.fromisoformat(str(data.get("updated_at", ""))[:19])
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if pid == me or _pid_alive(pid):
            continue  # 活着的不算前任
        if ts < cutoff:
            continue
        if best is None or ts > best[0]:
            data["_beacon_path"] = str(f)
            best = (ts, data)
    return best[1] if best else None


def format_predecessor_hint(data: dict[str, Any]) -> str:
    """继承提示文本（纯函数，便于测试与调用方自由输出）。"""
    sid = data.get("session_id", "?")
    turns = data.get("turns", 0)
    toks = int(data.get("input_tokens", 0) or 0)
    when = str(data.get("updated_at", "?"))
    last = str(data.get("last_user_input", "")).replace("\n", " ")[:80]
    todos = data.get("todos") or []
    open_n = sum(1 for t in todos if t.get("status") != "completed")
    lines = [
        f"[继承提示] 发现异常终止的前会话 session={sid}（最后心跳 {when}，{turns} 轮/{toks // 1000}k tok）",
        f"  未完成 todo: {open_n} 条" + (f"  最近指令: {last}" if last else ""),
        f"  恢复: lingclaude run --continue   快照: {data.get('_beacon_path', '?')}",
    ]
    return "\n".join(lines)


# ── 装配入口（app.py 交互分支调用）──────────────────────────────────────

def install_kill_beacon(engine: Any, project_path: str = "") -> KillBeacon | None:
    """启动信标 + 装信号快刷 + 打印继承提示。返回 beacon 供调用方 stop()。

    返回 None = 旁路（env 关闭 / engine 不合规）。
    """
    if os.environ.get(ENV_DISABLE, "") in ("1", "true", "True"):
        return None
    if engine is None or not getattr(engine, "session_id", None):
        return None
    beacon = KillBeacon(engine, project_path=project_path)
    beacon.start()
    # 绑到 engine：repl.py 退出路径经 getattr(engine, "_kill_beacon") 做 clean stop
    try:
        setattr(engine, "_kill_beacon", beacon)
    except Exception:  # noqa: BLE001 — 绑定失败不影响 beacon 本体
        pass

    # 信号快刷接线：包一层保证只做安全调用，挂在已有 SIGTERM/SIGHUP 链之后。
    # 注意 full_tui 的 _signal_restore_handler 会 re-raise 终止进程——它安装
    # 在 TUI start() 时（更晚），会覆盖本 handler；因此这里同时用 atexit 之外的
    # 保险丝：beacon 周期写本身即主保障，信号快刷是「TUI 未启动/先收到信号」
    # 窗口的补充。
    prev_term = prev_hup = None
    try:
        prev_term = signal.getsignal(signal.SIGTERM)
        prev_hup = signal.getsignal(signal.SIGHUP)

        def _on_term(signum: int, frame: Any) -> None:  # noqa: ARG001
            beacon.signal_flush()
            if callable(prev_term) and prev_term not in (signal.SIG_IGN, signal.SIG_DFL):
                prev_term(signum, frame)
            else:
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)

        def _on_hup(signum: int, frame: Any) -> None:  # noqa: ARG001
            beacon.signal_flush()
            if callable(prev_hup) and prev_hup not in (signal.SIG_IGN, signal.SIG_DFL):
                prev_hup(signum, frame)
            else:
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)

        signal.signal(signal.SIGTERM, _on_term)
        signal.signal(signal.SIGHUP, _on_hup)
    except (ValueError, OSError):  # 非主线程等
        pass

    hint = detect_dead_predecessor(project_path or os.getcwd())
    return beacon
