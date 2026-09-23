"""LSP 装表 A/B 记录器（装表扫尾：LSP 默认化前的数据收集面）。

设计（2026-09-23，用户拍板 A+B 双线）：
- 闸门：env LINGCLAUDE_LSP_AB，三态——
    off        现状（默认）：lsp 正常装表，零记录，零开销
    control    对照组：lsp 不注册（unregister），模型看不到该工具，记运行台账
    treatment  实验组：lsp 正常注册 + 调用记账（次数/成败/耗时/command 分布）
- 落盘：data/ab_runs/lsp/<group>/<session_id>.jsonl，与 agent_runs 同款逐行 JSON
- fail-open：任何异常不阻断主流程（记账失败如实缺席，不假活不崩主干）
- 无 env 时行为与历史完全一致（G8 同款守卫面：默认零变化）
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

AB_ENV_KEY = "LINGCLAUDE_LSP_AB"
_RUNS_DIR = Path(__file__).resolve().parents[2] / "data" / "ab_runs" / "lsp"


@dataclass
class AbStats:
    lsp_calls: int = 0
    lsp_ok: int = 0
    lsp_errors: int = 0
    by_command: dict[str, int] = field(default_factory=dict)
    total_ms: int = 0


def read_ab_group(env: dict[str, str] | None = None) -> str:
    """读闸门组别；非法值一律 fail-open 回 off。"""
    source = os.environ if env is None else env
    raw = (source.get(AB_ENV_KEY) or "off").strip().lower()
    return raw if raw in ("off", "control", "treatment") else "off"


class LspAbRecorder:
    """treatment 组记账器：包一层 _lsp_handler，逐调用落 JSONL。"""

    def __init__(self, session_id: str, runs_dir: Path | None = None) -> None:
        self.session_id = session_id or "unknown"
        self._dir = runs_dir if runs_dir is not None else _RUNS_DIR
        self.stats = AbStats()
        self._started = time.time()

    def wrap(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        async def wrapped(**kwargs: Any) -> Any:
            command = str(kwargs.get("command", "?"))
            t0 = time.time()
            try:
                result = await handler(**kwargs)
            except BaseException:
                self._tally(command, ok=False, dt_ms=int((time.time() - t0) * 1000))
                raise
            ok = bool(getattr(result, "success", True))
            self._tally(command, ok=ok, dt_ms=int((time.time() - t0) * 1000))
            return result

        return wrapped

    def _tally(self, command: str, ok: bool, dt_ms: int) -> None:
        self.stats.lsp_calls += 1
        if ok:
            self.stats.lsp_ok += 1
        else:
            self.stats.lsp_errors += 1
        self.stats.by_command[command] = self.stats.by_command.get(command, 0) + 1
        self.stats.total_ms += dt_ms
        self._flush({"command": command, "ok": ok, "dt_ms": dt_ms})

    def _flush(self, extra: dict[str, Any]) -> None:
        try:
            target = self._dir / "treatment"
            target.mkdir(parents=True, exist_ok=True)
            line = json.dumps(
                {"ts": time.time(), "session_id": self.session_id, **extra},
                ensure_ascii=False,
            )
            with (target / f"{self.session_id}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass  # fail-open：账面失败不阻断主流程

    def snapshot(self) -> dict[str, Any]:
        return {
            "group": "treatment",
            "session_id": self.session_id,
            "elapsed_s": int(time.time() - self._started),
            **self.stats.__dict__.copy(),
        }


def apply_lsp_ab_gate(registry, session_id: str) -> LspAbRecorder | None:
    """装配点闸门（coding.py register_all_tools 之后调用）。

    - control：unregister('lsp')，返回 None（无记账对象，工具不可见即数据）
    - treatment：包 handler 接记账，返回 recorder
    - off / 异常：零动作
    """
    group = read_ab_group()
    if group == "off":
        return None
    try:
        if group == "control":
            registry.unregister("lsp")
            return None
        tool = registry.get("lsp")
        tool_def = getattr(tool, "data", None)
        handler_name = getattr(tool_def, "handler_name", None)
        handler = registry.get_handler(handler_name) if handler_name else None
        if handler is None:  # 取不到绑定名则退化为不记账，工具保留（fail-open）
            return None
        recorder = LspAbRecorder(session_id=session_id)
        registry.register_handler(handler_name, recorder.wrap(handler))
        return recorder
    except Exception:
        return None
