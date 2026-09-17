"""灵克守卫 MCP 薄壳插片（agent/lc-guard，铁律 5 双向互认：ac 调 lc 守卫面）。

atomcode↔lc 互为插片实验的 lc 侧交付物（CODING_AGENT_PLUGIN_PLAN_L2_L3.md §1.3）：
atomcode 是 MCP 客户端（消费 MCP server tools），lc 把自己封成 MCP server 薄壳
（server.py，FastMCP stdio，6 工具），ac 经 .mcp.json 连接调用 lc 守卫面
（lc_audit_trigger 让工作少出错，用户目标 2）。

铁律锚点（每行代码过法）：
- 铁律 1：本插片全在 plugins/agents/lc_mcp_guard/，core/ 零 diff；
- 铁律 3/J4：每次 MCP 调用记 agent_run:lc-guard record（转发+错误结构化，J4）；
- 铁律 5：federation_pair record（type=federation_pair key=lc-ac state=proposing），
  N1 周期对账（缺一/不一致即警）；
- 铁律 6：trust_level=T1（lc 仓代码在手）+ plug_level=L1（薄壳可换其他实现，
  接口一致即不崩，N2 守卫消费）；
- 铁律 7：缝 key 带域前缀 agent/lc-guard（N3 守卫消费）；
- 候选铁律 8：MCP 探针失败累计 → absent（N4 缺席查，不假活）；
- J1 薄壳纪律（mcp-wrap skill 守卫线）：封装层零业务判断，只转发 lc 对应实体
  （self_audit_trigger / 台账 / 灵族组织 / 铁律条文），不抄守卫 kernel。

复用资产：agent_lingxi 三件套范式（manifest + plugin + 测试）；
mcp-wrap skill（MCP 封装模板）；work_claim.py（ac 改 lc 前查锁）。
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


class LcGuardMcpPlugin:
    """AgentSeam 协议实现（run/abort/status），MCP stdio 传输（lc 守卫薄壳）。

    与 agent_lingxi 差异：
    - 方向反转：agent_lingxi 是 lc 调外部 Agent（lc 是 MCP client）；
      本插片是 ac 调 lc 守卫面（lc 是 MCP server 薄壳，本插片封 server 为 lc 插片）；
    - 复用 AgentSeam 协议（run/abort/status）：run(task) = 经 MCP 调 lc 守卫工具。
    """

    name = "agent/lc-guard"  # 铁律 7：域前缀缝 key

    def __init__(self, store: StateStore | None = None) -> None:
        self._manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self._store = store or StateStore(backend="json",
                                          root=Path(__file__).parents[4] / "data" / "agent_runs")
        self._probe_failures = 0
        self._last_resp: dict | None = None

    # ── AgentSeam 协议三动作 ────────────────────────────────────────────
    def run(self, task: str, **kwargs) -> dict:
        """执行一次 lc 守卫 MCP 调用，全程 record 化（J4）。

        task 形态：MCP 工具名 + 参数，如 "lc_audit_trigger" / "lc_ledger_query:{...}"。
        """
        run_id = f"lc-guard:{int(time.time())}"
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

        支持两种形态：
        - "lc_audit_trigger" → ("lc_audit_trigger", {})
        - "lc_ledger_query:{\"ledger_type\": \"arch_debt\"}" →
          ("lc_ledger_query", {"ledger_type": "arch_debt"})
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

    # ── 内部：MCP stdio 调用 + record 化（薄壳纪律：零业务判断）──────
    def _server_cmd(self) -> list[str]:
        """server 启动命令：以 manifest.transport.command 为单一事实源。"""
        cmd = list(self._manifest["transport"].get("command")
                   or ["python3", "lingclaude/plugins/agents/lc_mcp_guard/server.py"])
        return cmd

    def _call_mcp_tool(self, tool_name: str, args: dict) -> dict:
        """经 MCP stdio 调 lc 守卫薄壳的单个工具（initialize + tools/call）。

        薄壳纪律：只转发工具调用，不判返回内容语义（业务判断在 lc 守卫脚本里）。
        成功锚定：id=1 的 result 字段（非进程存活，mcp-wrap skill 踩坑 #4）。
        """
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "lc-guard-plugin", "version": "1.0.0"}}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": tool_name, "arguments": args}}),
        ]
        payload = "\n".join(lines) + "\n"
        cwd = self._manifest["transport"].get("cwd")
        r = subprocess.run(
            self._server_cmd(), input=payload, capture_output=True, text=True,
            timeout=self._manifest["transport"].get("call_timeout_s", 120), cwd=cwd)
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
        """
        probe = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                            "params": {"protocolVersion": "2024-11-05",
                                       "capabilities": {},
                                       "clientInfo": {"name": "lc-guard-probe",
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
        from lingclaude.plugins.agents.mcp_common import domain_of, health_key  # 公共接缝（P2-1 整改：不再 import agent_lingxi 内部）
        self._store.save("health_state", health_key(self.name), {
            "name": self.name,
            "domain": domain_of(self.name),
            "healthy": healthy,
            "absent": absent,
            "probe_failures": self._probe_failures,
            "updated_at": time.time(),
        })

    def _record(self, run_id: str, state: str, extra: dict) -> None:
        """J4：状态变迁全入账（agent_run:lc-guard type，StateStore）。"""
        rec = self._store.load("agent_run", run_id) or {}
        rec.update({"plugin": self.name, "state": state,
                    "transited_at": time.time(), **extra})
        self._store.save("agent_run", run_id, rec)


def register(registry) -> None:
    """插件入口：SeamRegistry.register(SeamType.AGENT, "agent/lc-guard", plugin)。

    M5 截肢：unregister 后主干照跑（L1 替换，薄壳可换其他 lc 守卫 MCP 实现）。
    """
    from lingclaude.core.seam import SeamType  # 延迟 import，避免循环
    registry.register(SeamType.AGENT, LcGuardMcpPlugin.name, LcGuardMcpPlugin())
