"""外部 Agent 网关 MCP 薄壳插片（agent/proj-agent-gateway，第二层试验田：lc 调外部编程 agent）。

Hermes WebUI / Orca 式的多 agent 聚合在 lc 侧的对应物：lc 经 .mcp.json 连接本薄壳
（server.py，FastMCP stdio，5 工具），驱动 cc/codex/crush/opencode/atomcode 各出一版
结论。与 lc-guard（ac 调 lc 守卫面）互为对偶——federation_pair key=lc-ac 双 record。

铁律锚点（每行代码过法）：
- 铁律 1：本插片全在 plugins/agents/proj_agent_gateway/，core/ 零 diff；
- 铁律 3/J4：每次 MCP 调用记 agent_run:agent-gateway record（转发+错误结构化，J4）；
- 铁律 6：trust_level=T3（外部 agent 非 lc 代码在手，只探活+透传结果）+ plug_level=L2
  （缺席降级：单 agent 挂了不影响其余，不崩主干）；
- 铁律 7：缝 key 带域前缀 agent/proj-agent-gateway（N3 守卫消费，外部工程域）；
- 候选铁律 8：MCP 探针失败累计 → absent（N4 缺席查，不假活）；
- J1 薄壳纪律：封装层零 agent 业务判断，只子进程分发+超时+错误结构化，不抄 agent kernel。

复用资产：lc_mcp_guard plugin.py 的 MCP stdio 调用范式（initialize + tools/call，
J5 锚定 id=1 的 result 判成功）；mcp-wrap skill 踩坑清单。

方向说明（与 lc-guard 对偶）：
- lc-guard：ac 是 MCP 客户端 → lc 是 MCP server 薄壳（ac 调 lc 守卫面）；
- 本插片：lc 是 MCP 客户端 → 本薄壳是 MCP server（lc 调外部 agent），ac 是 agent 之一
  （经 headless -p 子进程，绕开 daemon goal 面 401）。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from lingclaude.core.state_store import StateStore

MANIFEST = Path(__file__).parent / "manifest.agent.json"
ABSENT_THRESHOLD = 2  # 候选铁律 8：连续 N 次探针失败即 absent

# 铁律 3/J4 run 状态机的终态集合（超过即视为 record 语义破坏）
TERMINAL_STATES = ("succeeded", "timeout", "failed", "aborted")


class AgentGatewayMcpPlugin:
    """AgentSeam 协议实现（run/abort/status），MCP stdio 传输（外部 agent 网关薄壳）。"""

    name = "agent/proj-agent-gateway"  # 铁律 7：域前缀缝 key（外部工程域 proj/）

    def __init__(self, store: StateStore | None = None) -> None:
        self._manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self._store = store or StateStore(backend="json",
                                          root=Path(__file__).parents[4] / "data" / "agent_runs")
        self._probe_failures = 0
        self._last_resp: dict | None = None

    # ── AgentSeam 协议三动作 ────────────────────────────────────────────
    def run(self, task: str, **kwargs) -> dict:
        """执行一次外部 agent 网关 MCP 调用，全程 record 化（J4）。

        task 形态：MCP 工具名 + 参数，如
        - "agent_status"
        - "agent_invoke:{\"agent\": \"ac\", \"prompt\": \"…\"}"
        - "agent_batch:{\"agents\": [\"cc\", \"codex\"], \"prompt\": \"…\"}"
        """
        run_id = f"agent-gateway:{int(time.time())}"
        self._record(run_id, "running", {"task": task[:200]})
        try:
            tool_name, args = self._parse_task(task)
            result = self._call_mcp_tool(tool_name, args)
            self._record(run_id, "succeeded", {"result": str(result)[:500]})
            return {"run_id": run_id, "state": "succeeded", "result": result}
        except subprocess.TimeoutExpired:
            self._record(run_id, "timeout", {})
            return {"run_id": run_id, "state": "timeout"}
        except Exception as e:  # noqa: BLE001 —— 失败也必须入账（J4）
            self._record(run_id, "failed", {"error": str(e)[:300]})
            return {"run_id": run_id, "state": "failed", "error": str(e)}

    @staticmethod
    def _parse_task(task: str) -> tuple[str, dict]:
        """解析 task → (tool_name, arguments)。

        - "agent_status" → ("agent_status", {})
        - "agent_invoke:{\"agent\": \"ac\"}" → ("agent_invoke", {"agent": "ac"})
        """
        if ":" in task and task.split(":", 1)[1].startswith("{"):
            tool, raw = task.split(":", 1)
            return tool.strip(), json.loads(raw)
        return task.strip(), {}

    def abort(self, run_id: str) -> bool:
        """终止运行中的调用（AgentSeam 协议）。MCP stdio 不可中断，仅 record 化。"""
        rec = self._store.load("agent_run", run_id) or {}
        if rec.get("state") not in TERMINAL_STATES:
            self._record(run_id, "aborted", {"note": "MCP stdio 不可中断，record 化 aborted"})
            return True
        return False

    def status(self) -> dict:
        """健康状态（候选铁律 8：探针失败累计 → absent，N4 缺席查）。"""
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
            "absent": absent,
            "probe_failures": self._probe_failures,
        }

    # ── 内部：MCP stdio 调用 + record 化（薄壳纪律：零 agent 业务判断）──
    def _server_cmd(self) -> list[str]:
        """server 启动命令：以 manifest.transport.command 为单一事实源。"""
        cmd = list(self._manifest["transport"].get("command")
                   or ["python3", "lingclaude/plugins/agents/proj_agent_gateway/server.py"])
        return cmd

    def _call_mcp_tool(self, tool_name: str, args: dict) -> dict:
        """经 MCP stdio 调 agent-gateway 薄壳的单个工具（initialize + tools/call）。

        薄壳纪律：只转发工具调用，不判返回内容语义（agent 业务判断在 agent 自己）。
        成功锚定：id=1 的 result 字段（非进程存活，mcp-wrap skill 踩坑 #4）。
        注意：agent_invoke/agent_batch 底层各起一个外部 agent 子进程，耗时可能
        远超 120s——manifest call_timeout_s 已拉到 180，超时走 timeout record（J4 合法终态）。
        """
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "agent-gateway-plugin", "version": "1.0.0"}}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": tool_name, "arguments": args}}),
        ]
        payload = "\n".join(lines) + "\n"
        cwd = self._manifest["transport"].get("cwd")
        r = subprocess.run(
            self._server_cmd(), input=payload, capture_output=True, text=True,
            timeout=self._manifest["transport"].get("call_timeout_s", 180), cwd=cwd)
        resp = self._extract_response(r.stdout, 1)
        if resp is None:
            raise RuntimeError("MCP call failed: no response for tools/call (id=1)")
        if "error" in resp:
            raise RuntimeError(f"MCP tool error: {str(resp['error'])[:300]}")
        text = resp["result"]["content"][0]["text"]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw_text": text}

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
        """健康探针：initialize 是否回 result（轻量，不执行业务）。

        J5 行为级：必须拿到 id=0 的 result 且非 error（error 响应不得判活）。
        注：薄壳 initialize 不真正起外部 agent，只验 FastMCP server 本身可达（快）。
        """
        probe = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                            "params": {"protocolVersion": "2024-11-05",
                                       "capabilities": {},
                                       "clientInfo": {"name": "agent-gateway-probe",
                                                      "version": "1.0.0"}}})
        try:
            r = subprocess.run(self._server_cmd(), input=probe + "\n",
                               capture_output=True, text=True,
                               timeout=self._manifest["health_probe"]["timeout_s"],
                               cwd=self._manifest["transport"].get("cwd"))
            resp = self._extract_response(r.stdout, 0)
            return (r.returncode in (0, 1) and resp is not None
                    and "error" not in resp and "result" in resp)
        except (subprocess.TimeoutExpired, OSError, KeyError):
            return False

    def _record_health(self, healthy: bool, absent: bool) -> None:
        """N4 缺席查数据面：健康状态持久化为 health_state record（J4 可 query）。"""
        from lingclaude.plugins.agents.mcp_common import domain_of, health_key
        self._store.save("health_state", health_key(self.name), {
            "name": self.name,
            "domain": domain_of(self.name),
            "healthy": healthy,
            "absent": absent,
            "probe_failures": self._probe_failures,
            "updated_at": time.time(),
        })

    def _record(self, run_id: str, state: str, extra: dict) -> None:
        """J4：状态变迁全入账（agent_run:agent-gateway type，StateStore）。"""
        rec = self._store.load("agent_run", run_id) or {}
        rec.update({"plugin": self.name, "state": state,
                    "transited_at": time.time(), **extra})
        self._store.save("agent_run", run_id, rec)


def register(registry) -> None:
    """插件入口：SeamRegistry.register(SeamType.AGENT, "agent/proj-agent-gateway", plugin)。

    M5 截肢：unregister 后主干照跑（L2 降级，单 agent 缺席不影响其余）。
    """
    from lingclaude.core.seam import SeamType  # 延迟 import，避免循环
    registry.register(SeamType.AGENT, AgentGatewayMcpPlugin.name, AgentGatewayMcpPlugin())
