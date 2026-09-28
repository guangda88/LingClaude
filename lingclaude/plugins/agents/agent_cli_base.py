"""外部 agent CLI 插片共享基类 — agent_codex 泛化（批2，2026-09-28）。

四家外部 agent（crush/opencode/claude/qwen）与 codex 同构：一次性非交互
子进程、无 MCP stdio 长连接，故统一 cli-subprocess 型；record 家法
（agent_run 入账）、缺席查（absent_after）、fail-soft 语义对齐 agent_family
（家法不二致）。

安全模型（全家族一致）：exec 一律只读沙箱/无写权限标志——子代理是"只读
建议面"，写操作由 lc 主会话裁决后自行执行。prompt 经 stdin 传入，规避
shell 注入与 ARG_MAX。

差异面收敛到 manifest：command（含各家 headless 标志）、prompt_mode
（stdin | arg）、version_args（health probe 用）。
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

from lingclaude.core.state_store import StateStore

logger = logging.getLogger(__name__)

LC_ROOT = Path(__file__).parents[3]


class CliAgentPluginBase:
    """外部 CLI agent 插片基类（AgentSeam 协议：run/abort/status）。"""

    def __init__(self, manifest_path: Path,
                 store: StateStore | None = None) -> None:
        self._manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._store = store or StateStore(
            backend="json", root=LC_ROOT / "data" / "agent_runs")
        self._probe_failures = 0
        self._runs: dict[str, str] = {}  # run_id -> state（进程内 status 面）

    # ── AgentSeam 协议 ─────────────────────────────────────────────────
    @property
    def name(self) -> str:
        return self._manifest["name"]

    def run(self, tool: str, arguments: dict | None = None, **kwargs) -> dict:
        """派单外部 CLI。tool 语义：'exec'（默认）= 一次性任务执行。

        arguments:
            prompt: str    — 任务指令（必填）
            cwd: str       — 工作目录（默认 lc 仓库根）
            timeout_s: int — 覆盖超时（默认 manifest transport.call_timeout_s）
        """
        args = dict(arguments or {})
        prompt = (args.get("prompt") or "").strip()
        if not prompt:
            return self._record_run("failed", {"error": "prompt 必填"})

        tr = self._manifest["transport"]
        cmd = list(tr["command"])
        cwd = args.get("cwd") or str(LC_ROOT)
        timeout_s = int(args.get("timeout_s") or tr.get("call_timeout_s", 600))
        prompt_mode = tr.get("prompt_mode", "stdin")

        if prompt_mode == "arg":
            cmd.append(prompt)

        run_id = f"{self.name.split('/', 1)[1]}:{int(time.time())}"
        self._runs[run_id] = "running"
        self._record(run_id, "running", {"prompt": prompt[:120], "cwd": cwd})
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                input=prompt if prompt_mode == "stdin" else None,
                text=True, cwd=cwd,
                capture_output=True, timeout=timeout_s,
            )
            dur = round(time.monotonic() - t0, 1)
            # 某些 CLI（crush）回答走 stdout 但 rc 可能非 0（配额/告警）——
            # 有 stdout 正文且非空即视为成功，stderr 附带供诊断。
            out = (proc.stdout or "").strip()
            if proc.returncode == 0 or out:
                self._runs[run_id] = "succeeded"
                self._record(run_id, "succeeded",
                             {"duration_s": dur, "output": out[:500],
                              "rc": proc.returncode})
                return {"run_id": run_id, "state": "succeeded",
                        "result": out, "duration_s": dur}
            err = (proc.stderr or out or "").strip()[:500]
            self._runs[run_id] = "failed"
            self._record(run_id, "failed", {"duration_s": dur, "error": err,
                                            "rc": proc.returncode})
            return {"run_id": run_id, "state": "failed", "error": err}
        except subprocess.TimeoutExpired:
            self._runs[run_id] = "timeout"
            self._record(run_id, "timeout", {"timeout_s": timeout_s})
            return {"run_id": run_id, "state": "failed",
                    "error": f"{self.name} 执行超时（>{timeout_s}s）"}
        except FileNotFoundError as e:
            self._runs[run_id] = "failed"
            self._record(run_id, "failed",
                         {"error": f"二进制缺席: {e}"})
            self._probe_failures += 1
            return {"run_id": run_id, "state": "failed",
                    "error": f"{self.name} 二进制不可达（缺席查计数+1）"}

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
        run_id = f"{self.name.split('/', 1)[1]}:{int(time.time())}"
        self._record(run_id, state, extra)
        return {"run_id": run_id, "state": state, **extra}


def make_register(plugin_cls, manifest_path: Path):
    """生成该插片的 register(registry) 入口（各 plugin.py 一行调用）。"""
    def register(registry) -> None:
        from lingclaude.core.seam import SeamType  # 延迟 import，避免循环
        instance = plugin_cls(manifest_path)
        registry.register(SeamType.AGENT, instance.name, instance)
    return register
