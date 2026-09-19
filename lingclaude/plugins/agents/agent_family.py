"""灵字辈家族成员批量插片化——MCP stdio 型公共基类（参数化，单实现）。

背景（用户 2026-09-18 指令）："12子+8对外工程=20实体" 全量插片化，lingxi 已完成，余 19。
组织事实源：data/ling_org/org_member/*.json——每个成员的 plug_seam_key / trust_level /
plug_level 是用户已裁定的登记事实，插片声明必须与账本一致（测试锚定，N1 对账精神）。

设计裁决（单实现原则）：19 个成员不复制 271 行 lingxi 范式，而是抽取参数化公共基类，
各成员插片 = manifest 全声明 + 薄壳子类。lingxi 保留原实现不动（J5：既有测试锚定）。

铁律锚点：
- 铁律 1：全在 plugins/agents/，core/ 零 diff；
- 铁律 2 细则 5：stop_layer 三要素（kernel=本基类参数化实现；seams=[family_agent]；
  implementations=家族成员数）；铁律 6：trust/plug 双声明与 org_member 账本一致；
- 铁律 3/J4：run 全程 record 化（agent_run:<key>），失败/超时也入账；
- 铁律 4：载体未构建/未确认 = 债务语法（arch_debt），不留无账红灯、不假活；
- 铁律 8：探针失败累计 → absent（缺席查），健康落 health_state record，
  domain=缝 key 域前缀（mcp_common.domain_of 同源）；
- J1：变化走接缝——成员载体变化只改各自 manifest，不动基类；
- J5：契约测试 + 参数化桥接（tests/agents/test_agent_family.py）。
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from pathlib import Path

from lingclaude.core.state_store import StateStore
from lingclaude.plugins.agents.mcp_common import domain_of, health_key

# 铁律 3/J4 run 状态机终态集合（超出即视为 record 语义破坏）
TERMINAL_STATES = ("succeeded", "timeout", "failed", "aborted")

AGENTS_ROOT = Path(__file__).parent


class McpAgentPluginBase:
    """家族成员 MCP stdio 插片基类（AgentSeam 协议，manifest 全驱动）。

    与 agent_lingxi 的关系：同协议（run/abort/status）、同 record 家法（agent_run）、
    同缺席查语义（absent_after）；差异仅在——本基类由 manifest 驱动任意成员，
    工具调用不限定 execute_command（run(tool, arguments) 通用形态）。
    """

    def __init__(self, manifest_path: Path, store: StateStore | None = None) -> None:
        self._manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._store = store or StateStore(
            backend="json", root=Path(__file__).parents[3] / "data" / "agent_runs")
        self._probe_failures = 0
        # server 自报工具清单（tools/list 缓存）；None=未加载，{}=server 无工具
        self._tools_schema: dict[str, dict] | None = None

    # ── AgentSeam 协议 ─────────────────────────────────────────────────
    @property
    def name(self) -> str:
        """铁律 7：域前缀缝 key（agent/<member>），与 manifest.name 同源。"""
        return self._manifest["name"]

    def run(self, tool: str, arguments: dict | None = None, **kwargs) -> dict:
        """执行一次成员 MCP 工具调用，全程 record 化（J4）。"""
        run_id = f"{self.name.split('/', 1)[1]}:{int(time.time())}"
        self._record(run_id, "running", {"tool": tool})
        try:
            result = self._call_tool(tool, arguments or {})
            self._record(run_id, "succeeded", {"result": str(result)[:500]})
            return {"run_id": run_id, "state": "succeeded", "result": result}
        except subprocess.TimeoutExpired:
            self._record(run_id, "timeout", {"tool": tool})
            return {"run_id": run_id, "state": "timeout", "tool": tool}
        except Exception as e:  # noqa: BLE001 —— 失败也必须入账（J4）
            self._record(run_id, "failed", {"error": str(e)[:300]})
            return {"run_id": run_id, "state": "failed", "error": str(e)}

    def abort(self, run_id: str) -> bool:
        """终止运行中的调用。MCP stdio 单次调用不可中断，仅 record 化 aborted。"""
        rec = self._store.load("agent_run", run_id) or {}
        if rec.get("state") not in TERMINAL_STATES:
            self._record(run_id, "aborted", {"note": "MCP stdio 不可中断，record 化 aborted"})
            return True
        return False

    def status(self) -> dict:
        """健康状态（铁律 8：探针失败累计 → absent，缺席查）。"""
        healthy = self._probe()
        self._probe_failures = 0 if healthy else self._probe_failures + 1
        absent = self._probe_failures >= int(
            self._manifest.get("health_probe", {}).get("absent_after", 2))
        self._record_health(healthy, absent)
        return {"name": self.name, "healthy": healthy, "absent": absent,
                "probe_failures": self._probe_failures}

    # ── 内部：MCP stdio 调用 + record 化 ──────────────────────────────
    def _server_cmd(self) -> list[str]:
        """server 启动命令：以 manifest.transport.command 为单一事实源。"""
        return list(self._manifest["transport"]["command"])

    def _call_tool(self, tool: str, arguments: dict) -> str:
        """经 MCP stdio 发起一次工具调用（统一走 _stdio_exchange 会话）。

        契约修复（2026-09-20，mtg_20260919_224049 闭幕后 P1）：
        server 的 tools/list 自报 schema 是唯一契约源——参数按 schema 过滤，
        未声明参数一律报错（含历史 caller 硬注入，lingbus post_reply 契约失配根因）。
        结果判定锚定目标 id 响应行（J5 行为级）；JSON-RPC error 带真实 message 上抛。
        """
        tool_schemas = self._load_tools()
        if tool not in tool_schemas:
            raise RuntimeError(
                f"unknown tool {tool!r} on {self.name} "
                f"(server declares: {sorted(tool_schemas) or '[]'})")
        declared = tool_schemas[tool].get("inputSchema", {}).get("properties", {})
        unknown = [k for k in arguments if k not in declared]
        if unknown:
            raise RuntimeError(
                f"contract mismatch on {self.name}.{tool}: "
                f"arguments not declared by server schema: {sorted(unknown)}; "
                f"declared: {sorted(declared)}")
        resp = self._stdio_exchange(
            [("tools/call", {"name": tool, "arguments": arguments}, 1)])
        return json.dumps(resp, ensure_ascii=False)

    def _load_tools(self) -> dict[str, dict]:
        """经 tools/list 拉取并缓存 server 自报工具清单（契约唯一事实源）。"""
        if self._tools_schema is None:
            resp = self._stdio_exchange([("tools/list", {}, 1)])
            tools = (resp or {}).get("tools", []) if resp else []
            self._tools_schema = {t.get("name", ""): t for t in tools}
        return self._tools_schema

    def _stdio_exchange(self, calls: list[tuple[str, dict, int]]) -> dict | None:
        """一次 stdio 会话：initialize 握手 + 多段 JSON-RPC 请求，返回末段 id 的完整响应。

        calls 元组 = (method, params, expect_id)；expect_id 为 None 表示通知（不期待响应）。
        stdin 保持打开直至收到目标响应——防止慢工具（建库/redzone 检查）期间
        stdin EOF 触发 transport 关停吞掉响应（2026-09-20 fixtest 实证根因）。
        读线程 + deadline 兜底，超时报 TimeoutExpired。
        """
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "lingclaude-agent", "version": "1.0.0"}}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        ]
        last_id = 0
        last_method = ""
        for method, params, expect_id in calls:
            if expect_id is None:
                lines.append(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}))
            else:
                lines.append(json.dumps({"jsonrpc": "2.0", "id": expect_id,
                                         "method": method, "params": params}))
                last_id = expect_id
                last_method = method
        payload = "\n".join(lines) + "\n"  # 根因 4 教训：按行读 stdin 必须结尾换行
        cwd = self._manifest["transport"].get("cwd")
        timeout = self._manifest["transport"].get("call_timeout_s", 120)
        proc = subprocess.Popen(
            self._server_cmd(), cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True)
        try:
            proc.stdin.write(payload)
            proc.stdin.flush()  # 不关闭 stdin：EOF 会让 transport 提前关停吞响应
            if not last_id:
                return None
            q: "queue.Queue[str | None]" = queue.Queue()

            def _reader() -> None:
                try:
                    for line in proc.stdout:
                        q.put(line)
                finally:
                    q.put(None)

            threading.Thread(target=_reader, daemon=True).start()
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(self._server_cmd(), timeout)
                try:
                    line = q.get(timeout=remaining)
                except queue.Empty:
                    raise subprocess.TimeoutExpired(self._server_cmd(), timeout) from None
                if line is None:
                    raise RuntimeError(
                        f"MCP call failed: server stdout closed before id={last_id} "
                        f"response ({last_method})")
                resp = _extract_response(line, last_id)
                if resp is not None:
                    if "error" in resp:
                        msg = resp["error"].get("message", json.dumps(resp["error"])[:200])
                        raise RuntimeError(f"MCP error on {last_method}: {msg}")
                    if "result" not in resp:
                        raise RuntimeError(f"MCP call failed: no result (id={last_id})")
                    return resp["result"]
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
            if proc.poll() is None:
                proc.terminate()

    def _probe(self) -> bool:
        """健康探针：initialize 回 result 才算活（error 响应不得判活，J5 行为级）。"""
        try:
            probe = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                "params": {"protocolVersion": "2024-11-05",
                                           "capabilities": {},
                                           "clientInfo": {"name": "lc-probe", "version": "1.0.0"}}})
            r = subprocess.run(self._server_cmd(), input=probe + "\n", capture_output=True,
                               text=True,
                               timeout=self._manifest["health_probe"]["timeout_s"],
                               cwd=self._manifest["transport"].get("cwd"))
            resp = _extract_response(r.stdout, 0)
            return (r.returncode in (0, 1) and resp is not None
                    and "error" not in resp and "result" in resp)
        except (subprocess.TimeoutExpired, OSError, KeyError):
            return False

    def _record(self, run_id: str, state: str, extra: dict) -> None:
        """J4：状态变迁全入账（agent_run type）。"""
        rec = self._store.load("agent_run", run_id) or {}
        rec.update({"plugin": self.name, "state": state,
                    "transited_at": time.time(), **extra})
        self._store.save("agent_run", run_id, rec)

    def _record_health(self, healthy: bool, absent: bool) -> None:
        """铁律 8：健康状态落 record（domain=域前缀），缺席查/域级圈死的数据面。"""
        key = health_key(self.name)
        self._store.save("health_state", key, {
            "name": self.name,
            "domain": domain_of(self.name),
            "healthy": healthy,
            "absent": absent,
            "probe_failures": self._probe_failures,
            "updated_at": time.time(),
        })


def _extract_response(out: str, req_id: int) -> dict | None:
    """从 stdio 多行 JSON-RPC 输出中提取指定 id 的响应行（公共接缝转发）。"""
    from lingclaude.plugins.agents.mcp_common import extract_response
    return extract_response(out, req_id)


def load_org_member(member_id: str, root: Path | None = None) -> dict:
    """读取 org_member 登记账本（组织事实源，N1 对账用）。"""
    base = root if root is not None else Path(__file__).parents[3] / "data" / "ling_org" / "org_member"
    path = base / f"{member_id}.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
