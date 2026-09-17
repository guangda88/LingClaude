"""灵犀 Agent 插片（Phase 1 试验田）—— ling-term-mcp 经 AgentSeam 挂载。

铁律锚点（用户 2026-09-17 裁定 Phase 1，每行代码过法）：
- 铁律 1：本插片全在 plugins/agents/，core/ 零 diff；
- 铁律 3/J4：run/abort/status 全程 record 化（agent_run type，StateStore）；
- 铁律 5：trust_level=T1 + plug_level=L1 双声明（N2 守卫消费）；
- 铁律 6：缝 key 带域前缀 agent/lingxi（N3 守卫消费）；
- 候选铁律 8：health_probe 缺席检测（连续 2 次失败 → record transition absent）。

传输：MCP stdio（node dist/cli.js，lingxi 仓）。

探针债务清偿（20260917-21，debt agent-lingxi-mcp-probe-timeout）：
- 根因 1：沙箱 `ulimit -v` 1GB 下 node 内嵌根证书包初始化 malloc 失败
  → 崩溃栈 x509!=nullptr（crypto_context.cc:797），进程活不过 0.4s，
  表象即"5s 内无 result"。修复：server 命令注入 --use-openssl-ca
  （改读系统 CA 存储，实测单独即足，0.4s 内 result）。
- 根因 2：npx 包装器（npm/cli）在同一 VA 上限下自身 OOM，弃用之。
- 根因 3：探针 initialize 载荷缺 clientInfo.version（新 MCP SDK 必填，
  否则 -32603 invalid_type）。修复：补 "version": "1.0.0"。
- 协议修正：_call_mcp 补 initialize/initialized 握手（MCP 规范），
  结果判定锚定 id=1 行（协议嵌套，非"首行/进程存活"）。
- 身份：灵犀 identity 中间件要求 caller 为注册成员，经 manifest
  "caller" 声明（lingclaude 在 src/security/identity.ts 注册表）。
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


class LingxiAgentPlugin:
    """AgentSeam 协议实现（run/abort/status），MCP stdio 传输。"""

    name = "agent/lingxi"  # 铁律 7：域前缀缝 key

    def __init__(self, store: StateStore | None = None) -> None:
        self._manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self._store = store or StateStore(backend="json",
                                          root=Path(__file__).parents[3] / "data" / "agent_runs")
        self._proc: subprocess.Popen | None = None
        self._probe_failures = 0

    def _server_cmd(self) -> list[str]:
        """server 启动命令：以 manifest.transport.command 为单一事实源。

        根因 1 防漂移：若命令为 node 且未带 --use-openssl-ca，则幂等注入
        （规避沙箱 ulimit -v 1GB 下内嵌根证书包初始化崩溃）。测试注入
        非=node 的假命令（cat/false/脚本）时原样返回，保持 hermetic。
        """
        cmd = list(self._manifest["transport"].get("command")
                   or ["node", "--use-openssl-ca", "dist/cli.js"])
        if cmd and cmd[0] == "node" and "--use-openssl-ca" not in cmd:
            cmd.insert(1, "--use-openssl-ca")
        return cmd

    # ── AgentSeam 协议三动作 ────────────────────────────────────────────
    def run(self, task: str, **kwargs) -> dict:
        """执行一次 lingxi MCP 调用，全程 record 化（J4）。"""
        run_id = f"lingxi:{int(time.time())}"
        self._record(run_id, "running", {"task": task[:200]})
        try:
            result = self._call_mcp(task, **kwargs)
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
        }

    def _record_health(self, healthy: bool, absent: bool) -> None:
        """N4 缺席查数据面：健康状态持久化为 health_state record（J4 可 query）。

        圈死前提：域级缺席查按 record 的 domain 字段过滤汇总，
        状态必须先落盘可查，否则域级守卫无从成立。
        """
        self._store.save("health_state", _health_key(self.name), {
            "name": self.name,
            "domain": _domain_of(self.name),
            "healthy": healthy,
            "absent": absent,
            "probe_failures": self._probe_failures,
            "updated_at": time.time(),
        })

    # ── 内部：MCP stdio 调用 + record 化 ───────────────────────────────
    def _call_mcp(self, task: str, **kwargs) -> str:
        """经 MCP stdio 发起一次工具调用（initialize 握手 + tools/call）。

        结果判定锚定响应中 id==1 的行（协议嵌套语义，J5 行为级），
        非"首行/进程存活"——initialize 回包同样含 result，不能误判为成功。
        """
        caller = self._manifest.get("caller", "lingclaude")
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "lingclaude-agent", "version": "1.0.0"}}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "execute_command",
                                   "arguments": {"command": task, "caller": caller}}}),
        ]
        payload = "\n".join(lines) + "\n"
        cwd = self._manifest["transport"].get("cwd")
        self._proc = subprocess.Popen(
            self._server_cmd(), cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
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
        """健康探针：initialize 是否回 result（轻量，不执行业务）。

        清偿记录：载荷补 clientInfo.version（-32603 修复）；
        命令走 _server_cmd()（--use-openssl-ca，根证书包崩溃修复）。
        """
        try:
            probe = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                "params": {"protocolVersion": "2024-11-05",
                                           "capabilities": {},
                                           "clientInfo": {"name": "lc-probe",
                                                          "version": "1.0.0"}}})
            # 根因 4：灵犀 CLI 按行读 stdin，无结尾换行则请求不被处理（无响应），
            # 必须补 '\n'（与 _call_mcp 的 payload 结尾换行同规）
            r = subprocess.run(self._server_cmd(), input=probe + "\n", capture_output=True,
                               text=True, timeout=self._manifest["health_probe"]["timeout_s"],
                               cwd=self._manifest["transport"].get("cwd"))
            resp = self._extract_response(r.stdout, 0)
            # J5 行为级：必须拿到 id=0 的 result 且非 error（error 响应不得判活）
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


def _domain_of(seam_key: str) -> str:
    """铁律 7 缝 key 的域 = 域前缀（'agent/lingxi' → 'agent'）。"""
    return seam_key.split("/", 1)[0] if "/" in seam_key else seam_key


def _health_key(seam_key: str) -> str:
    """缝 key 是带 '/' 的域前缀 key，StateStore 存取 key 需文件名安全化。"""
    return seam_key.replace("/", "__")


def query_domain_health(store: StateStore, domain: str,
                        root: Path | None = None) -> dict:
    """N4 缺席查（域级圈死）：汇总指定域内全部健康 record。

    语义（铁律 8 隔离故障域）：
    - 域内任一 absent → 该域查锁/查活一律按 absent 处置，不假活、不放行半成品；
    - 域外（其他 domain）不受此域故障影响；
    - 域内无任何 record → unknown（不臆断健康）。

    返回 {"domain", "records", "absent": [names], "healthy": [names],
          "unknown": [names], "quarantined": bool}
    """
    records = list_health_records(store, root)
    mine = [r for r in records if r.get("domain") == domain]
    absent = sorted(r["name"] for r in mine if r.get("absent") is True)
    healthy = sorted(r["name"] for r in mine if r.get("healthy") is True
                     and r.get("absent") is not True)
    unknown = sorted(r["name"] for r in mine
                     if r.get("healthy") is None and r.get("absent") is not True)
    return {
        "domain": domain,
        "records": len(mine),
        "absent": absent,
        "healthy": healthy,
        "unknown": unknown,
        "quarantined": bool(absent),  # 圈死开关：域内存在缺席即整域隔离
    }


def list_health_records(store: StateStore,
                        root: Path | None = None) -> list[dict]:
    """枚举 health_state record：JSON 后端按目录枚举（key 规范可逆）。

    复用 backend._path_for 的根布局约定（root/health_state/*.json），
    不在守卫外另立读取路径；key 经 _health_key 双下划线编码，可逆还原。
    """
    base = root if root is not None else store._backend._root
    d = base / "health_state"
    if not d.is_dir():
        return []
    out: list[dict] = []
    for p in sorted(d.glob("*.json")):
        rec = store.load("health_state", p.stem, root=root)
        if rec is not None:
            out.append(rec)
    return out


def register(registry) -> None:
    """插件入口：SeamRegistry.register(SeamType.AGENT, "agent/lingxi", plugin)。"""
    from lingclaude.core.seam import SeamType  # 延迟 import，避免循环
    registry.register(SeamType.AGENT, LingxiAgentPlugin.name, LingxiAgentPlugin())
