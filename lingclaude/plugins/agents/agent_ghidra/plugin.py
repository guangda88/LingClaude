"""Ghidra 逆向插片 — AgentSeam 协议实现（run/abort/status）。

架构（三层）：
  lc 主干 → plugin.py（本层，MCP stdio 桥接调用）
         → bridge_mcp_ghidra.py（LaurieWired 官方 bridge，MCP 协议翻译）
         → Ghidra headless 内 GhidraHttpServer.py（Jython HTTP server，8081）

前置条件：Ghidra 11.4.3 装机 + TestProj 已导入目标二进制（headless 分析完成）+
后端 HTTP server 已起（backend.health_probe_http 可探活）。
mcp-wrap 流水线产出（2026-09-27），模板照抄 agent_lingxi（四处必保语义齐全）。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from lingclaude.core.state_store import StateStore

MANIFEST = Path(__file__).parent / "manifest.agent.json"
ABSENT_THRESHOLD = 2  # 候选铁律 8：连续 N 次探针失败即 absent

# 铁律 3/J4 run 状态机终态集合
TERMINAL_STATES = ("succeeded", "timeout", "failed", "aborted")

# 允许经 run() 调用的 MCP 工具白名单（只读为主 + 受限写，T2 契约审计面）
ALLOWED_TOOLS = frozenset({
    "list_methods", "list_classes", "list_segments", "list_imports",
    "list_exports", "list_data_items", "list_strings", "list_namespaces",
    "search_functions_by_name", "decompile_function", "decompile_function_by_address",
    "disassemble_function", "get_function_by_address", "get_current_address",
    "get_current_function", "list_functions",
    "get_xrefs_to", "get_xrefs_from", "get_function_xrefs",
    "rename_function", "rename_data", "rename_function_by_address",
    "set_decompiler_comment", "set_disassembly_comment",
})


class GhidraAgentPlugin:
    """AgentSeam 协议实现（run/abort/status），MCP stdio 传输（bridge 脚本）。"""

    name = "agent/ghidra"  # 铁律 7：域前缀缝 key

    def __init__(self, store: StateStore | None = None) -> None:
        self._manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self._store = store or StateStore(backend="json",
                                          root=Path(__file__).parents[3] / "data" / "agent_runs")
        self._proc: subprocess.Popen | None = None
        self._probe_failures = 0

    def _server_cmd(self) -> list[str]:
        """server 启动命令：以 manifest.transport.command 为单一事实源（防漂移）。"""
        return list(self._manifest["transport"].get("command") or [])

    def _backend_healthy(self) -> bool:
        """后端（Ghidra headless HTTP 8081）探活：GET health_probe_http 返回 200。"""
        import urllib.request
        try:
            url = (self._manifest["backend"]["http_base"].rstrip("/")
                   + self._manifest["backend"].get("health_probe_http", "/methods?limit=1"))
            with urllib.request.urlopen(url, timeout=5) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001 —— 探针异常一律不健康，不反噬主干
            return False

    # ── AgentSeam 协议三动作 ────────────────────────────────────────────
    def run(self, task: str, **kwargs) -> dict:
        """执行一次 Ghidra MCP 工具调用，全程 record 化（J4）。

        :param task: 工具名（如 "list_methods" / "decompile_function"）
        :param kwargs: 工具参数（如 address="004013c0" / offset=0 limit=10）
        """
        tool = task.strip()
        run_id = f"ghidra:{int(time.time())}"
        # T2 契约审计：白名单外工具拒绝（fail-closed，不静默放行）
        if tool not in ALLOWED_TOOLS:
            self._record(run_id, "failed", {"error": f"tool not in allowlist: {tool}"})
            return {"run_id": run_id, "state": "failed",
                    "error": f"tool not in allowlist: {tool}"}
        self._record(run_id, "running", {"task": tool[:200]})
        try:
            result = self._call_mcp(tool, **kwargs)
            # 锚定语义：结果里必须含 MCP result（非进程存活）
            self._record(run_id, "succeeded", {"result": str(result)[:500]})
            return {"run_id": run_id, "state": "succeeded", "result": result}
        except subprocess.TimeoutExpired:
            self._record(run_id, "timeout", {})
            return {"run_id": run_id, "state": "timeout"}
        except Exception as e:  # noqa: BLE001 —— 失败也必须入账（J4）
            self._record(run_id, "failed", {"error": str(e)[:300]})
            return {"run_id": run_id, "state": "failed", "error": str(e)}

    def abort(self, run_id: str) -> bool:
        """终止运行中的调用（AgentSeam 协议）。"""
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._record(run_id, "aborted", {})
            return True
        return False

    def status(self) -> dict:
        """健康状态（候选铁律 8：探针失败累计 → absent）。"""
        healthy = self._probe()
        if not healthy:
            self._probe_failures += 1
        else:
            self._probe_failures = 0
        absent = self._probe_failures >= ABSENT_THRESHOLD
        self._record_health(healthy, absent)
        return {
            "name": self.name,
            "healthy": healthy,
            "absent": absent,  # N4 语义：absent 后下游 query 得此值，不假活
            "probe_failures": self._probe_failures,
            "backend_healthy": self._backend_healthy(),
        }

    def _record_health(self, healthy: bool, absent: bool) -> None:
        """N4 缺席查数据面：健康状态持久化为 health_state record（J4 可 query）。"""
        self._store.save("health_state", _health_key(self.name), {
            "name": self.name,
            "domain": _domain_of(self.name),
            "healthy": healthy,
            "absent": absent,
            "probe_failures": self._probe_failures,
            "updated_at": time.time(),
        })

    # ── 内部：MCP stdio 调用 + record 化 ───────────────────────────────
    def _call_mcp(self, tool: str, **kwargs) -> str:
        """经 MCP stdio 发起一次工具调用（initialize 握手 + tools/call）。

        结果判定锚定响应中 id==1 的行（协议嵌套语义，J5 行为级）。
        """
        arguments = {k: v for k, v in kwargs.items() if v is not None}
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "lingclaude-agent", "version": "1.0.0"}}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": tool, "arguments": arguments}}),
        ]
        payload = "\n".join(lines) + "\n"
        cwd = self._manifest["transport"].get("cwd")
        self._proc = subprocess.Popen(
            self._server_cmd(), cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            out, _ = self._proc.communicate(
                payload, timeout=self._manifest["transport"].get("call_timeout_s", 120))
            resp = self._extract_response(out, 1)
            if resp is None:
                raise RuntimeError("MCP call failed: no response for tools/call (id=1)")
            if "error" in resp or "result" not in resp:
                raise RuntimeError(f"MCP call failed: {str(resp.get('error', 'no result'))[:200]}")
            return json.dumps(resp, ensure_ascii=False)
        finally:
            if self._proc.poll() is None:
                self._proc.terminate()

    @staticmethod
    def _extract_response(out: str, req_id: int) -> dict | None:
        """从 stdio 多行 JSON-RPC 输出中提取指定 id 的响应行。"""
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
        """健康探针：bridge initialize 是否回 result（轻量，不执行业务）。

        J5 行为级：必须拿到 id=0 的 result 且非 error（error 响应不得判活）。
        """
        try:
            probe = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                "params": {"protocolVersion": "2024-11-05",
                                           "capabilities": {},
                                           "clientInfo": {"name": "lc-probe",
                                                          "version": "1.0.0"}}})
            r = subprocess.run(self._server_cmd(), input=probe + "\n", capture_output=True,
                               text=True, timeout=self._manifest["health_probe"]["timeout_s"],
                               cwd=self._manifest["transport"].get("cwd"))
            resp = self._extract_response(r.stdout, 0)
            return (resp is not None
                    and "error" not in resp and "result" in resp)
        except (subprocess.TimeoutExpired, OSError, KeyError):
            return False

    def _record(self, run_id: str, state: str, extra: dict) -> None:
        """J4：状态变迁全入账（agent_run type）。"""
        rec = self._store.load("agent_run", run_id) or {}
        rec.update({"plugin": self.name, "state": state,
                    "transited_at": time.time(), **extra})
        self._store.save("agent_run", run_id, rec)


# P2-1 模式：原语平移至 plugins/agents/mcp_common.py（公共接缝），本模块保留别名
from lingclaude.plugins.agents.mcp_common import (  # noqa: E402
    domain_of as _domain_of,
    health_key as _health_key,
)


def register(registry) -> None:
    """插件入口：SeamRegistry.register(SeamType.AGENT, "agent/ghidra", plugin)。"""
    from lingclaude.core.seam import SeamType  # 延迟 import，避免循环
    registry.register(SeamType.AGENT, GhidraAgentPlugin.name, GhidraAgentPlugin())
