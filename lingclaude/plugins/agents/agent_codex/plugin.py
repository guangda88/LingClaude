"""Codex 外部 agent 插片 — lc 派单 codex-cli 的 AgentSeam 桥（首例，2026-09-27）。

载体定性：cli-subprocess 型（非 MCP）——codex exec 是一次性非交互子进程，
无 stdio 长连接，故不继承 McpAgentPluginBase；但 record 家法（agent_run 入账）、
缺席查（absent_after）、fail-soft 语义与其对齐（家法不二致）。

安全模型：exec 一律 --sandbox read-only——子代理是"只读建议面"，写操作
由 lc 主会话裁决后自行执行（信任边界：lc 不把写权限下放给外部二进制）。
prompt 经 stdin 传入，规避 shell 注入与 ARG_MAX。

后续 crush/opencode/claude/qwen 插片按本模板复制（manifest 改 command）。
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

from lingclaude.core.state_store import StateStore

logger = logging.getLogger(__name__)

MANIFEST_PATH = Path(__file__).parent / "manifest.agent.json"


class CodexAgentPlugin:
    """agent/codex — codex exec 子进程桥（AgentSeam 协议：run/abort/status）。"""

    def __init__(self, manifest_path: Path = MANIFEST_PATH,
                 store: StateStore | None = None) -> None:
        self._manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._store = store or StateStore(
            backend="json", root=Path(__file__).parents[3] / "data" / "agent_runs")
        self._probe_failures = 0
        self._runs: dict[str, str] = {}  # run_id -> state（进程内 status 面）

    # ── AgentSeam 协议 ─────────────────────────────────────────────────
    @property
    def name(self) -> str:
        return self._manifest["name"]

    def run(self, tool: str, arguments: dict | None = None, **kwargs) -> dict:
        """派单 codex exec。tool 语义：'exec'（默认）= 一次性任务执行。

        arguments:
            prompt: str          — 任务指令（必填）
            cwd: str             — 工作目录（默认 lc 仓库根）
            model: str | None    — 覆盖模型（如 k3-256k，走 codex -c model_provider）
            timeout_s: int       — 覆盖超时（默认 manifest transport.call_timeout_s）
        """
        args = dict(arguments or {})
        prompt = (args.get("prompt") or "").strip()
        if not prompt:
            return self._record_run("failed", {"error": "prompt 必填"})

        tr = self._manifest["transport"]
        cmd = list(tr["command"])
        model = args.get("model")
        if model:
            # providers 见 codex-providers/config.toml（kimi/ZAI/minimax 直连）
            cmd += ["-c", f'model_provider="{model.split("@")[0]}"',
                    "-c", f'model="{model.split("@")[-1]}"']

        cwd = args.get("cwd") or "/home/ai/lingclaude"
        timeout_s = int(args.get("timeout_s") or tr.get("call_timeout_s", 600))

        run_id = f"codex:{int(time.time())}"
        self._runs[run_id] = "running"
        self._record(run_id, "running", {"prompt": prompt[:120], "cwd": cwd})
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd + ["-"],  # '-' = prompt 从 stdin 读
                input=prompt, text=True, cwd=cwd,
                capture_output=True, timeout=timeout_s,
            )
            dur = round(time.monotonic() - t0, 1)
            if proc.returncode == 0:
                out = (proc.stdout or "").strip()
                self._runs[run_id] = "succeeded"
                self._record(run_id, "succeeded",
                             {"duration_s": dur, "output": out[:500]})
                return {"run_id": run_id, "state": "succeeded",
                        "result": out, "duration_s": dur}
            err = (proc.stderr or proc.stdout or "").strip()[:500]
            self._runs[run_id] = "failed"
            self._record(run_id, "failed", {"duration_s": dur, "error": err})
            return {"run_id": run_id, "state": "failed", "error": err}
        except subprocess.TimeoutExpired:
            self._runs[run_id] = "timeout"
            self._record(run_id, "timeout", {"timeout_s": timeout_s})
            return {"run_id": run_id, "state": "failed",
                    "error": f"codex exec 超时（>{timeout_s}s）"}
        except FileNotFoundError as e:
            self._runs[run_id] = "failed"
            self._record(run_id, "failed", {"error": f"codex 二进制缺席: {e}"})
            self._probe_failures += 1
            return {"run_id": run_id, "state": "failed",
                    "error": "codex 二进制不可达（缺席查计数+1）"}

    def abort(self, run_id: str) -> bool:
        """一次性 exec 子进程无悬挂态（超时即回收），abort 语义=标记失败。"""
        if self._runs.get(run_id) == "running":
            self._runs[run_id] = "aborted"
            self._record(run_id, "aborted", {})
            return True
        return False

    def status(self, run_id: str) -> str:
        return self._runs.get(run_id, "unknown")

    # ── 家法：record 入账（J4，对齐 agent_family）─────────────────────
    def _record(self, run_id: str, state: str, extra: dict) -> None:
        try:
            rec = self._store.load("agent_run", run_id) or {}
            rec.update({"transited_at": time.time(), "state": state, **extra})
            self._store.save("agent_run", run_id, rec)
        except Exception:  # noqa: BLE001 —— 审计旁路不反噬主流程（fail-soft）
            logger.debug("agent_run record failed: %s", run_id, exc_info=True)

    def _record_run(self, state: str, extra: dict) -> dict:
        run_id = f"codex:{int(time.time())}"
        self._record(run_id, state, extra)
        return {"run_id": run_id, "state": state, **extra}


def register(registry) -> None:
    from lingclaude.core.seam import SeamType  # 延迟 import，避免循环
    registry.register(SeamType.AGENT, CodexAgentPlugin().name, CodexAgentPlugin())
