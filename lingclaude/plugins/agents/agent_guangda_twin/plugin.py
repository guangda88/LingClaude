"""光大分身 Agent 插片 —— guangda-twin 经 AgentSeam 挂载。

铁律锚点（每行代码过法）：
- 铁律 1：本插片全在 plugins/agents/，core/ 零 diff；
- 铁律 3/J4：run/abort/status 全程 record 化（agent_run type，StateStore）；
- 铁律 5：trust_level=T1 + plug_level=L1 双声明（N2 守卫消费）；
- 铁律 6：缝 key 带域前缀 agent/guangda-twin（N3 守卫消费）；
- 铁律 8：health_probe 缺席检测（连续 2 次失败 → record transition absent）。

传输：MCP stdio（python3 -m twin mcp，cwd=/home/ai/guangda-twin）。

N7 横向耦合禁令：**本文件不 import guangda-twin 的任何 Python 符号**，
只用子进程 + JSON-RPC 通信。反向依赖为零。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from lingclaude.core.state_store import StateStore

MANIFEST = Path(__file__).parent / "manifest.agent.json"
ABSENT_THRESHOLD = 2

TERMINAL_STATES = ("succeeded", "timeout", "failed", "aborted")


class GuangdaTwinPlugin:
    """AgentSeam 协议实现（run/abort/status），MCP stdio 传输。"""

    name = "agent/guangda-twin"

    def __init__(self, store: StateStore | None = None) -> None:
        self._manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self._store = store or StateStore(
            backend="json",
            root=Path(__file__).parents[3] / "data" / "agent_runs")
        self._proc: subprocess.Popen | None = None
        self._probe_failures = 0

    def _server_cmd(self) -> list[str]:
        return list(self._manifest["transport"].get("command")
                    or ["python3", "-m", "twin", "mcp"])

    # ── AgentSeam 协议三动作 ────────────────────────────────────────────

    def run(self, task: str, **kwargs) -> dict:
        """执行一次 twin MCP 调用，全程 record 化（J4）。失败也必须入账。"""
        run_id = f"guangda-twin:{int(time.time())}"
        self._record(run_id, "running", {"task": task[:200], **kwargs})
        try:
            tool = kwargs.pop("tool", "twin_ask")
            result = self._call_mcp(tool, task, **kwargs)
            self._record(run_id, "succeeded", {"result": str(result)[:500]})
            return {"run_id": run_id, "state": "succeeded", "result": result}
        except subprocess.TimeoutExpired:
            self._record(run_id, "timeout", {})
            return {"run_id": run_id, "state": "timeout"}
        except Exception as e:  # noqa: BLE001 —— 失败也必须入账（J4）
            self._record(run_id, "failed", {"error": str(e)[:300]})
            return {"run_id": run_id, "state": "failed", "error": str(e)}

    def abort(self, run_id: str) -> bool:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._record(run_id, "aborted", {})
            return True
        return False

    def status(self) -> dict:
        """健康状态（铁律 8：探针失败累计 → absent）。"""
        healthy = self._probe()
        if not healthy:
            self._probe_failures += 1
        else:
            self._probe_failures = 0
        absent = self._probe_failures >= ABSENT_THRESHOLD
        self._record_health(healthy, absent)
        return {"name": self.name, "healthy": healthy, "absent": absent,
                "probe_failures": self._probe_failures}

    def _record_health(self, healthy: bool, absent: bool) -> None:
        self._store.save("health_state", _health_key(self.name), {
            "name": self.name, "domain": _domain_of(self.name),
            "healthy": healthy, "absent": absent,
            "probe_failures": self._probe_failures, "updated_at": time.time(),
        })

    # ── 内部：MCP stdio 调用 ────────────────────────────────────────────

    def _call_mcp(self, tool: str, question: str, **kwargs) -> str:
        caller = self._manifest.get("caller", "lingclaude")
        args = {"question": question, **kwargs}
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "lingclaude-agent", "version": "1.0.0"}}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": tool, "arguments": args}}),
        ]
        payload = "\n".join(lines) + "\n"
        self._proc = subprocess.Popen(
            self._server_cmd(),
            cwd=self._manifest["transport"].get("cwd"),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
        )
        try:
            out, _ = self._proc.communicate(
                payload, timeout=self._manifest["transport"].get("call_timeout_s", 120))
            resp = self._extract_response(out, 1)
            if resp is None:
                raise RuntimeError("MCP call failed: no response for tools/call (id=1)")
            if "error" in resp:
                raise RuntimeError(f"MCP call failed: {str(resp['error'])[:200]}")
            return json.dumps(resp.get("result", resp), ensure_ascii=False)
        finally:
            if self._proc.poll() is None:
                self._proc.terminate()

    @staticmethod
    def _extract_response(out: str, req_id: int) -> dict | None:
        for line in out.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("id") == req_id:
                return obj
        return None

    def _probe(self) -> bool:
        """健康探针：tools/list 是否回 result（轻量，不执行业务）。"""
        try:
            lines = [
                json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                       "clientInfo": {"name": "lingclaude-agent",
                                                      "version": "1.0.0"}}}),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}),
            ]
            self._proc = subprocess.Popen(
                self._server_cmd(), cwd=self._manifest["transport"].get("cwd"),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True)
            try:
                out, _ = self._proc.communicate(
                    "\n".join(lines) + "\n",
                    timeout=self._manifest["health_probe"].get("timeout_s", 5))
            finally:
                if self._proc.poll() is None:
                    self._proc.terminate()
            resp = self._extract_response(out, 1)
            return bool(resp and "result" in resp)
        except Exception:  # noqa: BLE001
            return False

    def _record(self, run_id: str, state: str, payload: dict) -> None:
        if state not in TERMINAL_STATES and state != "running":
            raise ValueError(f"非法 run 状态：{state}")
        self._store.save("agent_run", run_id, {
            "name": self.name, "run_id": run_id, "state": state,
            "domain": _domain_of(self.name), "updated_at": time.time(), **payload,
        })


def _domain_of(name: str) -> str:
    return name.split("/", 1)[0] if "/" in name else "agent"


def _health_key(name: str) -> str:
    return f"health:{name}"


def register(seam_registry=None) -> dict:
    """供 SeamRegistry.register(SeamType.AGENT, ...) 调用（J1 变化走接缝）。"""
    plugin = GuangdaTwinPlugin()
    if seam_registry is not None:
        from lingclaude.core.seam import SeamType  # 2026-10-02: 修 47c0e90 断链（core.seams 复数不存在，真名 core.seam）
        seam_registry.register(SeamType.AGENT, plugin.name, plugin)
    return {"registered": plugin.name, "plug_level": "L1", "trust_level": "T1"}
