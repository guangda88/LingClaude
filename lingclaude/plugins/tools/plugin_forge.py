"""plugin_forge — 插片生成回路：任务 → 生成 → 自测 → 自注册 → 记录。

灵元铁律对齐：
- 铁律 3（变化走接缝）：生成的产物只落在 plugins/agents/ 下，主干零 diff。
- 铁律 4（修剪语法）：每次生成回写 knowledge.db（含测试结果），低效生成可被追溯降级。
- 铁律 6（信任等级）：生成的 manifest 强制 trust_level/plug_level 声明，不默认放行。

设计约束（2026-09-28 首版）：
- 生成用模板渲染，不用 LLM 自由写代码——「结构确定、内容参数化」。
- LLM 只允许填 YAML 语义槽（display_name / kernel_note / capabilities），
  代码部分由模板保证。验证闸门可信后再放宽。
- 生成器两类：
  - cli_agent（首版，靶面 = crush/opencode/claude/qwen 同构薄壳，
    实测 15-19 行 plugin.py + 一份 manifest，差异面 100% 收敛在 manifest）
  - mcp_wrap（2026-09-28 批 2，靶面 = 12 子 MCP 化 / 8 对外工程探针化，
    模板对齐 agent_lingxi 参考实现 + mcp-wrap skill 的 manifest 契约）

回路四段接线：
  1. 生成：模板渲染 manifest.agent.json + plugin.py
  2. 自测：AST 解析 + manifest schema 校验（N2 双声明/N3 域前缀）+ 命令存在性
  3. 自注册：落盘 → HotReloadTrigger.check() 拾取 → registry_loader 验证
  4. 记录：LearnedRule(category="generated_plugin") 回写 knowledge.db
"""
from __future__ import annotations

import ast
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any

logger = logging.getLogger(__name__)

LC_ROOT = Path(__file__).parents[3]
# 注意：AGENTS_DIR 必须与 registry_loader.AGENTS_ROOT 一致（包内路径），
# 不能锚到仓库根的 plugins/——那里是另一个目录。
AGENTS_DIR = Path(__file__).parent.parent / "agents"

# ── manifest 必填字段（实测 agent_crush/opencode 对齐）────────────────
_REQUIRED_MANIFEST_FIELDS = (
    "name", "target", "display_name", "version",
    "trust_level", "plug_level", "caller",
    "transport", "capabilities", "health_probe",
    "state_record", "org_member",
)
_REQUIRED_TRANSPORT_FIELDS = ("kind", "mode", "call_timeout_s", "command")

# ── 薄壳 plugin.py 模板（实测 agent_crush/plugin.py 结构）─────────────
_PLUGIN_PY_TEMPLATE = Template('''"""agent/$org_member 插片 — $target 外部 agent 桥（plugin_forge 生成，cli-subprocess 薄壳）。"""
from __future__ import annotations

from pathlib import Path

from lingclaude.plugins.agents.agent_cli_base import CliAgentPluginBase, make_register

MANIFEST_PATH = Path(__file__).parent / "manifest.agent.json"


class ${class_name}Plugin(CliAgentPluginBase):
    """agent/$org_member — $headless_desc 子进程桥（manifest 全驱动，无本地逻辑）。

    ⚠️ plugin_forge 生成（$forge_date）：$gen_note
    """


register = make_register(${class_name}Plugin, MANIFEST_PATH)
''')


@dataclass
class ForgeSpec:
    """cli_agent 生成器输入契约（YAML 语义槽）。

    首版只支持 forge_type=cli_agent；MCP 封装生成器为批2。
    """
    org_member: str                    # 短名（目录名 agent_{org_member}）
    display_name: str                  # 展示名
    cli_path: str                      # 可执行文件绝对路径
    headless_args: list[str] = field(default_factory=list)
    prompt_mode: str = "stdin"         # stdin | arg
    call_timeout_s: int = 600
    capabilities: list[str] = field(default_factory=lambda: ["code_generation"])
    kernel_note: str = ""              # 内核说明（版本/厂商）
    headless_verified: bool = False    # 是否已实测 headless
    gen_note: str = "manifest 全驱动，无本地逻辑"   # 生成备注（docstring 内）

    @property
    def target(self) -> str:
        return f"{self.org_member}-cli"

    @property
    def class_name(self) -> str:
        return "".join(w.capitalize() for w in self.org_member.split("_"))

    @property
    def plugin_dir(self) -> Path:
        return AGENTS_DIR / f"agent_{self.org_member}"


@dataclass
class ForgeResult:
    """生成回路结果（四段各自的成败 + 证据）。"""
    ok: bool
    plugin_dir: str = ""
    generated_files: list[str] = field(default_factory=list)
    self_test: dict[str, Any] = field(default_factory=dict)   # ast/manifest/cli 三项
    registered: bool = False
    register_keys: list[str] = field(default_factory=list)
    error: str = ""


# ── 段 1：生成（模板渲染）────────────────────────────────────────────

def _render_manifest(spec: ForgeSpec) -> dict[str, Any]:
    """按 crush/opencode 实测 manifest 结构渲染（字段顺序对齐，diff 最小化）。"""
    return {
        "name": f"agent/{spec.org_member}",
        "target": spec.target,
        "display_name": spec.display_name,
        "version": "0.1.0",
        "trust_level": "T2",
        "plug_level": "L1",
        "caller": "lingclaude",
        "transport": {
            "kind": "cli-subprocess",
            "mode": "exec",
            "call_timeout_s": spec.call_timeout_s,
            "command": [spec.cli_path, *spec.headless_args],
            "prompt_mode": spec.prompt_mode,
            "command_note": (
                f"plugin_forge 生成（2026-09-28）：{spec.org_member} headless "
                f"{'arg' if spec.prompt_mode == 'arg' else 'stdin'} 模式。"
                + ("headless 未实测，首次派单前需人工验证。" if not spec.headless_verified else "")
            ),
        },
        "stop_layer": {
            "kernel": spec.kernel_note or f"外部 agent 桥（{spec.target}）",
            "seams": ["family_agent"],
            "implementations": 1,
            "note": "cli-subprocess 型，共享 CliAgentPluginBase；record 家法/缺席查/fail-soft 对齐 agent_family。",
        },
        "capabilities": spec.capabilities,
        "health_probe": {
            "interval_s": 300,
            "timeout_s": 10,
            "method": "version_check",
            "absent_after": 2,
        },
        "state_record": f"agent_run:{spec.org_member}",
        "org_member": spec.org_member,
        "notes": (
            f"plugin_forge 生成插片（{spec.gen_note}）。"
            + ("" if spec.headless_verified else " ⚠️ headless 未实测——首个 run 可能返回 failed（缺席查语义，不假活）。")
        ),
    }


def _render_plugin_py(spec: ForgeSpec, forge_date: str = "2026-09-28") -> str:
    return _PLUGIN_PY_TEMPLATE.substitute(
        org_member=spec.org_member,
        target=spec.target,
        class_name=spec.class_name,
        headless_desc=" ".join(spec.headless_args) or "exec",
        forge_date=forge_date,
        gen_note=spec.gen_note,
    )


# ── forge_type=mcp_wrap（批 2：12 子 MCP 化 / 8 对外工程探针化）────────
# 契约依据：mcp-wrap skill（manifest 字段 + 踩坑清单）+ agent_lingxi 参考
# 实现 + mcp_common 公共接缝。N3 域前缀六域（core/agent/cap/os/hw/gov，
# core 为存量豁免域，生成器不产出 core 域）。
_N3_NAMESPACES = ("core", "agent", "cap", "os", "hw", "gov")

_REQUIRED_MCP_MANIFEST_FIELDS = (
    "name", "target", "display_name", "version",
    "trust_level", "plug_level", "caller",
    "transport", "capabilities", "health_probe",
    "state_record",
)
_REQUIRED_MCP_TRANSPORT_FIELDS = (
    "kind", "mode", "command", "call_timeout_s", "startup_timeout_s",
)


@dataclass
class McpWrapSpec:
    """mcp_wrap 生成器输入契约（mcp-wrap skill YAML 语义槽）。

    ns = 目录前缀（agent/proj/cap/os/hw/gov）；缝 key 域前缀经 N3 六域
    校验（proj 目录走 agent 域前缀，对齐 proj_family_meeting 先例：
    目录 proj_family_meeting，缝 key agent/proj-family-meeting）。
    """
    name: str                       # 服务名（目录名 {ns}_{name}）
    display_name: str               # 展示名
    command: list[str]              # 实测过的完整启动命令（禁 npx，踩坑 1）
    cwd: str = ""                   # 启动目录
    mode: str = "stdio"             # stdio | http
    ns: str = "agent"               # 目录前缀
    mcp_tool: str = "execute_command"   # run 时调用的 MCP 工具名
    tool_arg_name: str = "command"      # tools/call arguments 中 task 的参数名
    trust_level: str = "T2"         # T1/T2/T3（铁律 6，12 子默认 T2）
    plug_level: str = "L1"          # L1/L2/L3（默认 L1）
    capabilities: list[str] = field(default_factory=list)
    kernel_note: str = ""           # 停层 kernel 说明
    startup_timeout_s: int = 30
    call_timeout_s: int = 120
    verified: bool = False          # initialize 握手是否已本机实测（踩坑 6）

    @property
    def seam_ns(self) -> str:
        """缝 key 域前缀：proj 目录走 agent 域（proj_family_meeting 先例）。"""
        return "agent" if self.ns == "proj" else self.ns

    @property
    def seam_key(self) -> str:
        return f"{self.seam_ns}/{self.name}"

    @property
    def target(self) -> str:
        return f"{self.name}-mcp"

    @property
    def class_name(self) -> str:
        return "".join(w.capitalize() for w in self.name.replace("-", "_").split("_"))

    @property
    def plugin_dir(self) -> Path:
        return AGENTS_DIR / f"{self.ns}_{self.name}"


def _render_mcp_manifest(spec: McpWrapSpec) -> dict[str, Any]:
    """按 mcp-wrap skill 的 manifest 契约渲染（字段对齐 agent_lingxi）。"""
    return {
        "name": spec.seam_key,
        "target": spec.target,
        "display_name": spec.display_name,
        "version": "0.1.0",
        "trust_level": spec.trust_level,
        "plug_level": spec.plug_level,
        "caller": "lingclaude",
        "transport": {
            "kind": "mcp",
            "mode": spec.mode,
            "command": list(spec.command),
            "cwd": spec.cwd or None,
            "startup_timeout_s": spec.startup_timeout_s,
            "call_timeout_s": spec.call_timeout_s,
            "command_note": (
                f"plugin_forge 生成（2026-09-28）：{spec.name} MCP wrap "
                f"({spec.mode})，mcp_tool={spec.mcp_tool}。"
                + ("" if spec.verified else " initialize 握手未实测，首次派单前需人工验证（踩坑 6：未实测必留探针 debt）。")
            ),
        },
        "stop_layer": {
            "kernel": spec.kernel_note or f"{spec.name} MCP 服务（协议翻译层，零业务逻辑）",
            "seams": ["family_agent"],
            "implementations": 1,
            "note": "MCP stdio 型，复用 mcp_common（extract_response/domain_of/health_key）；record 家法/缺席查对齐 agent_lingxi。",
        },
        "capabilities": list(spec.capabilities) or [spec.mcp_tool],
        "health_probe": {
            "interval_s": 300,
            "timeout_s": 5,
            "method": "initialize",
            "absent_after": 2,
        },
        "state_record": f"agent_run:{spec.name}",
        "notes": (
            f"plugin_forge 生成插片（mcp_wrap，trust={spec.trust_level}，plug={spec.plug_level}）。"
            + ("" if spec.verified else " ⚠️ initialize 握手未实测——记探针 debt，首探可能 absent（缺席查语义，不假活）。")
        ),
    }


# ── mcp_wrap plugin.py 模板（agent_lingxi 参考实现泛化，四必保语义齐）──
# 1. run 成功锚定响应体（id=1 result），非进程存活（exit 0 ≠ 成功，踩坑 4）
# 2. 失败也入账（J4：timeout/failed/aborted 是合法终态）
# 3. 探针锚定 initialize result，error 响应不判活（J5 行为级，踩坑 3 补 version）
# 4. register() 入口（registry 域前缀缝 key，N3）
_MCP_PLUGIN_PY_TEMPLATE = Template('''"""$seam_key 插片 —— $display_name MCP 封装（plugin_forge 生成，批 2）。

⚠️ plugin_forge 生成（$forge_date）：$kernel_note
MCP stdio 传输；run/abort/status 全程 record 化（J4）；探针锚定 initialize result（J5）。
踩坑对齐：node 命令幂等注入 --use-openssl-ca（踩坑 2）；clientInfo.version 必填（踩坑 3）。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from lingclaude.core.state_store import StateStore
from lingclaude.plugins.agents.mcp_common import (
    domain_of, extract_response, health_key,
)

MANIFEST = Path(__file__).parent / "manifest.agent.json"
ABSENT_THRESHOLD = 2  # 候选铁律 8：连续 N 次探针失败即 absent
TERMINAL_STATES = ("succeeded", "timeout", "failed", "aborted")


class ${class_name}Plugin:
    """AgentSeam 协议实现（run/abort/status），MCP stdio 传输。"""

    name = "$seam_key"  # N3：域前缀缝 key
    MCP_TOOL = "$mcp_tool"
    MCP_ARG = "$tool_arg_name"

    def __init__(self, store: StateStore | None = None) -> None:
        self._manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self._store = store or StateStore(
            backend="json",
            root=Path(__file__).parents[3] / "data" / "agent_runs")
        self._proc: subprocess.Popen | None = None
        self._probe_failures = 0

    def _server_cmd(self) -> list[str]:
        """manifest.transport.command 为单一事实源（防双源漂移）。

        踩坑 2：node 命令幂等注入 --use-openssl-ca（沙箱 ulimit -v 下
        内嵌根证书包初始化崩溃修复，lingxi 教训 197 行测试）。
        """
        cmd = list(self._manifest["transport"].get("command") or [])
        if cmd and cmd[0] == "node" and "--use-openssl-ca" not in cmd:
            cmd.insert(1, "--use-openssl-ca")
        return cmd

    def run(self, task: str, **kwargs) -> dict:
        """一次 MCP tools/call，全程 record 化（J4：失败也入账）。"""
        run_id = f"{self.MCP_TOOL}:{int(time.time())}"
        self._record(run_id, "running", {"task": task[:200]})
        try:
            result = self._call_mcp(task)
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
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            self._record(run_id, "aborted", {})
            return True
        return False

    def status(self) -> dict:
        """健康状态（候选铁律 8：探针失败累计 → absent，不假活）。"""
        healthy = self._probe()
        self._probe_failures = self._probe_failures + 1 if not healthy else 0
        absent = self._probe_failures >= ABSENT_THRESHOLD
        self._record_health(healthy, absent)
        return {
            "name": self.name,
            "healthy": healthy,
            "absent": absent,
            "probe_failures": self._probe_failures,
        }

    def _record_health(self, healthy: bool, absent: bool) -> None:
        """N4 缺席查数据面：健康状态持久化为 health_state record。"""
        self._store.save("health_state", health_key(self.name), {
            "name": self.name,
            "domain": domain_of(self.name),
            "healthy": healthy,
            "absent": absent,
            "probe_failures": self._probe_failures,
            "updated_at": time.time(),
        })

    def _call_mcp(self, task: str) -> str:
        """MCP stdio 单次调用：initialize 握手 + tools/call。

        成功锚定 id=1 响应的 result 字段（J5：error 响应不判活，
        踩坑 4：exit 0 ≠ 成功）。clientInfo.version 必填（踩坑 3，-32603）。
        """
        caller = self._manifest.get("caller", "lingclaude")
        args = {self.MCP_ARG: task}
        if caller:
            args["caller"] = caller
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "lingclaude-agent",
                                                  "version": "1.0.0"}}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": self.MCP_TOOL, "arguments": args}}),
        ]
        payload = "\\n".join(lines) + "\\n"
        cwd = self._manifest["transport"].get("cwd") or None
        self._proc = subprocess.Popen(
            self._server_cmd(), cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True)
        try:
            out, _ = self._proc.communicate(
                payload,
                timeout=self._manifest["transport"].get("call_timeout_s", 120))
            resp = extract_response(out, 1)
            if resp is None:
                raise RuntimeError(
                    "MCP call failed: no response for tools/call (id=1)")
            if "error" in resp or "result" not in resp:
                raise RuntimeError(
                    f"MCP call failed: {str(resp.get('error', 'no result'))[:200]}")
            return json.dumps(resp, ensure_ascii=False)
        finally:
            if self._proc.poll() is None:
                self._proc.terminate()

    def _probe(self) -> bool:
        """健康探针：initialize 回 id=0 result 且非 error（J5 行为级）。"""
        try:
            probe = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                "params": {"protocolVersion": "2024-11-05",
                                           "capabilities": {},
                                           "clientInfo": {"name": "lc-probe",
                                                          "version": "1.0.0"}}})
            r = subprocess.run(self._server_cmd(), input=probe + "\\n",
                               capture_output=True, text=True,
                               timeout=self._manifest["health_probe"]["timeout_s"],
                               cwd=self._manifest["transport"].get("cwd") or None)
            resp = extract_response(r.stdout, 0)
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


def register(registry) -> None:
    """插片入口：SeamRegistry.register(SeamType.AGENT, 缝 key, plugin)。"""
    from lingclaude.core.seam import SeamType  # 延迟 import，避免循环
    registry.register(SeamType.AGENT, ${class_name}Plugin.name,
                      ${class_name}Plugin())
''')


def _render_mcp_plugin_py(spec: McpWrapSpec, forge_date: str = "2026-09-28") -> str:
    return _MCP_PLUGIN_PY_TEMPLATE.substitute(
        seam_key=spec.seam_key,
        display_name=spec.display_name,
        class_name=spec.class_name,
        mcp_tool=spec.mcp_tool,
        tool_arg_name=spec.tool_arg_name,
        kernel_note=spec.kernel_note or "MCP stdio 封装",
        forge_date=forge_date,
    )


def _self_test_mcp(spec: McpWrapSpec, manifest: dict[str, Any],
                   plugin_src: str) -> dict[str, Any]:
    """mcp_wrap 自测三闸门（对齐 mcp-wrap skill 入册前自查清单）。"""
    result: dict[str, Any] = {
        "ast": False, "manifest": False, "command": False, "errors": []}

    # 闸门 1：AST（对齐 AGENTS.md「.py 写入前 AST 检查」）
    try:
        tree = ast.parse(plugin_src)
        has_register = any(
            isinstance(n, ast.FunctionDef) and n.name == "register"
            for n in ast.walk(tree))
        if not has_register:
            result["errors"].append("plugin.py 缺 register 入口（registry_loader 契约）")
        else:
            result["ast"] = True
    except SyntaxError as e:
        result["errors"].append(f"AST 解析失败: {e}")

    # 闸门 2：manifest schema（必填字段 + N2 双声明 + N3 域前缀）
    missing = [f for f in _REQUIRED_MCP_MANIFEST_FIELDS if f not in manifest]
    if missing:
        result["errors"].append(f"manifest 缺字段: {missing}")
    else:
        tr = manifest.get("transport", {})
        missing_tr = [f for f in _REQUIRED_MCP_TRANSPORT_FIELDS if f not in tr]
        if missing_tr:
            result["errors"].append(f"transport 缺字段: {missing_tr}")
        elif manifest.get("trust_level") not in ("T1", "T2", "T3"):
            result["errors"].append(
                f"trust_level 声明非法: {manifest.get('trust_level')!r}（N2 双声明）")
        elif manifest.get("plug_level") not in ("L1", "L2", "L3"):
            result["errors"].append(
                f"plug_level 声明非法: {manifest.get('plug_level')!r}（N2 双声明）")
        elif not isinstance(manifest.get("name"), str) or "/" not in manifest["name"]:
            result["errors"].append(
                f"缝 key 缺域前缀: {manifest.get('name')!r}（N3）")
        elif manifest["name"].split("/", 1)[0] not in _N3_NAMESPACES:
            result["errors"].append(
                f"域前缀不在 N3 六域内: {manifest['name']}（N3）")
        else:
            result["manifest"] = True

    # 闸门 3：启动命令存在性（生成时即发现路径笔误；cwd 声明了也要存在）
    cmd = spec.command
    if not cmd or not (Path(cmd[0]).is_file() or shutil.which(cmd[0])):
        result["errors"].append(f"启动命令不存在: {cmd[:1] if cmd else spec.command}")
    elif spec.cwd and not Path(spec.cwd).is_dir():
        result["errors"].append(f"cwd 不存在: {spec.cwd}")
    else:
        result["command"] = True

    result["ok"] = result["ast"] and result["manifest"] and result["command"]
    return result


# ── 段 2：自测（AST + manifest schema + CLI 存在性）───────────────────

def _self_test(spec: ForgeSpec, manifest: dict[str, Any], plugin_src: str) -> dict[str, Any]:
    """生成后自动自测——三段闸门，任一不过即整体失败。"""
    result: dict[str, Any] = {"ast": False, "manifest": False, "cli_path": False, "errors": []}

    # AST 检查（对齐 AGENTS.md「.py 写入前 AST 检查」）
    try:
        tree = ast.parse(plugin_src)
        has_register = any(
            isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "register" for t in n.targets
            ) for n in ast.walk(tree)
        )
        if not has_register:
            result["errors"].append("plugin.py 缺 register 入口（registry_loader 契约）")
        else:
            result["ast"] = True
    except SyntaxError as e:
        result["errors"].append(f"AST 解析失败: {e}")

    # manifest schema 校验
    missing = [f for f in _REQUIRED_MANIFEST_FIELDS if f not in manifest]
    if missing:
        result["errors"].append(f"manifest 缺字段: {missing}")
    else:
        tr = manifest.get("transport", {})
        missing_tr = [f for f in _REQUIRED_TRANSPORT_FIELDS if f not in tr]
        if missing_tr:
            result["errors"].append(f"transport 缺字段: {missing_tr}")
        elif manifest["name"] != f"agent/{spec.org_member}":
            result["errors"].append(f"manifest.name 与 org_member 不一致: {manifest['name']}")
        else:
            result["manifest"] = True

    # CLI 路径存在性（生成时即可发现路径笔误）
    cli = spec.cli_path
    if Path(cli).is_file() or shutil.which(cli):
        result["cli_path"] = True
    else:
        result["errors"].append(f"CLI 路径不存在: {cli}（生成的插片健康探针将缺席查）")

    result["ok"] = result["ast"] and result["manifest"] and result["cli_path"]
    return result


# ── 段 2b：headless 探针（第四闸门，2026-09-28 批 3）────────────────
# 验证链最后一块：静态三闸门（AST/manifest/命令存在性）之外，真实跑一次
# headless 调用——cli_agent 发最小 prompt 看退出码+输出；mcp_wrap 发
# initialize 握手看 id=0 result（踩坑 3 补 version）。探针失败不阻断
# 落盘（debt 语义：verified=False 的产物照旧可入册，缺席查兜底），
# 但 verified 标不翻——消 debt 必须探针实证（AGENTS.md H17：
# 声明已完成必须附当轮验证证据）。
_HEADLESS_PROBE_TIMEOUT_S = 60


def _probe_cli_headless(spec: ForgeSpec) -> tuple[bool, str]:
    """cli_agent headless 探针：最小 prompt 单次调用。

    :returns: (通过, 证据)。通过 = 退出码 0 + 非空输出。
    """
    if spec.prompt_mode == "arg":
        cmd = [spec.cli_path, *spec.headless_args, "ping"]
    else:  # stdin
        cmd = [spec.cli_path, *spec.headless_args]
    try:
        r = subprocess.run(cmd, input="ping\n" if spec.prompt_mode == "stdin" else None,
                           capture_output=True, text=True,
                           timeout=_HEADLESS_PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, f"headless 探针超时（>{_HEADLESS_PROBE_TIMEOUT_S}s）"
    except OSError as e:
        return False, f"headless 探针无法启动: {e}"
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        return False, f"退出码 {r.returncode}: {(r.stderr or '')[:120]}"
    if not out:
        return False, "空输出（进程活≠成功，踩坑 4：成功锚定响应体）"
    return True, f"exit=0 out_len={len(out)} head={out[:60]!r}"


def _probe_mcp_initialize(spec: McpWrapSpec) -> tuple[bool, str]:
    """mcp_wrap headless 探针：initialize 握手（与模板 _probe 同语义）。

    通过 = id=0 响应含 result 且非 error（J5 行为级，踩坑 3 补 version）。
    node 命令同样幂等注入 --use-openssl-ca（踩坑 2，与模板 _server_cmd 一致）。
    """
    cmd = list(spec.command)
    if cmd and cmd[0] == "node" and "--use-openssl-ca" not in cmd:
        cmd.insert(1, "--use-openssl-ca")
    probe = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05",
                                   "capabilities": {},
                                   "clientInfo": {"name": "lc-probe",
                                                  "version": "1.0.0"}}})
    try:
        r = subprocess.run(cmd, input=probe + "\n", capture_output=True,
                           text=True, timeout=_HEADLESS_PROBE_TIMEOUT_S,
                           cwd=spec.cwd or None)
    except subprocess.TimeoutExpired:
        return False, f"initialize 握手超时（>{_HEADLESS_PROBE_TIMEOUT_S}s）"
    except OSError as e:
        return False, f"initialize 握手无法启动: {e}"
    resp = None
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("id") == 0:
            resp = obj
            break
    if resp is None:
        return False, "无 id=0 响应（进程活≠握手成功）"
    if "error" in resp or "result" not in resp:
        return False, f"error 响应不得判活: {str(resp.get('error'))[:120]}"
    return True, f"initialize OK serverInfo={str(resp.get('result', {}).get('serverInfo'))[:60]}"


# ── 段 4：记录（knowledge.db 回写）────────────────────────────────────

# 延迟绑定的 KnowledgeBase（测试可 patch 此模块属性）
_KnowledgeBase = None

def _get_knowledge_base_cls():
    """延迟 import KnowledgeBase——避免模块加载时的重依赖链。"""
    global _KnowledgeBase
    if _KnowledgeBase is None:
        from lingclaude.self_optimizer.learner.knowledge import KnowledgeBase
        _KnowledgeBase = KnowledgeBase
    return _KnowledgeBase


def _record_forge(spec: ForgeSpec | McpWrapSpec, result: ForgeResult) -> None:
    """生成参数+测试结果回写 knowledge.db（fail-soft：库缺席只记日志）。"""
    try:
        from lingclaude.self_optimizer.learner.models import FeedbackCategory, LearnedRule, Pattern
        if isinstance(spec, McpWrapSpec):
            member, seam_key, cmd_desc = spec.name, spec.seam_key, " ".join(spec.command[:2])
        else:
            member, seam_key, cmd_desc = spec.org_member, f"agent/{spec.org_member}", spec.cli_path
        KB = _get_knowledge_base_cls()
        kb = KB()
        rule = LearnedRule(
            id=f"generated_plugin_{member}",
            name=f"生成插片 {seam_key}",
            description=(
                f"plugin_forge 生成 {seam_key}: "
                f"self_test={'PASS' if result.self_test.get('ok') else 'FAIL'}, "
                f"registered={result.registered}, cmd={cmd_desc}"
            ),
            category=FeedbackCategory.BEST_PRACTICE,
            pattern=Pattern(
                context_keywords=(member, "plugin_forge", "generated"),
            ),
            tools=("plugin_forge",),
            frequency=1,
            confidence=0.9 if result.ok else 0.3,
            status="active" if result.ok else "draft",
        )
        kb.add_rule(rule)
        kb.close()
    except Exception as e:  # 记录失败不阻断生成回路
        logger.warning("plugin_forge 记录回写失败（fail-soft）: %s", e)


# ── 编排函数：四段串成一条回路 ────────────────────────────────────────

def forge_cli_agent(spec: ForgeSpec, *, auto_register: bool = True) -> ForgeResult:
    """cli_agent 生成回路主编排：生成 → 自测 → 落盘 → 自注册 → 记录。

    :param spec: 输入契约（YAML 语义槽填充后的 dataclass）
    :param auto_register: 是否触发 hot_reload 拾取（测试可关闭走手动验证）
    :return: ForgeResult（每段成败独立可查）
    """
    result = ForgeResult(ok=False, plugin_dir=str(spec.plugin_dir))

    # 幂等守卫：目录已存在且非空 → 拒绝覆盖（add-only 语义，对齐 hot_reload）
    if spec.plugin_dir.exists() and any(spec.plugin_dir.iterdir()):
        result.error = f"目录已存在且非空: {spec.plugin_dir}（add-only，拒绝覆盖）"
        _record_forge(spec, result)
        return result

    # 段 1：生成
    manifest = _render_manifest(spec)
    plugin_src = _render_plugin_py(spec)

    # 段 2：自测（落盘前——不干净的产物不进插件目录）
    result.self_test = _self_test(spec, manifest, plugin_src)
    if not result.self_test.get("ok"):
        result.error = f"自测未过: {result.self_test.get('errors')}"
        _record_forge(spec, result)
        return result

    # 落盘（自测通过才写——这是「生成后自动跑」与「先写再查」的分界）
    spec.plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = spec.plugin_dir / "manifest.agent.json"
    plugin_path = spec.plugin_dir / "plugin.py"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plugin_path.write_text(plugin_src, encoding="utf-8")
    result.generated_files = [str(manifest_path), str(plugin_path)]

    # 段 2b：headless 探针（落盘后真实跑一次——验证链最后一块）。
    # debt 语义：探针失败不推翻落盘/注册，只保留 verified=False 的债务标注；
    # 探针通过才翻标 verified=True 并重写 manifest（H17：翻标必须附实证）。
    if not spec.headless_verified:
        probe_ok, probe_ev = _probe_cli_headless(spec)
        result.self_test["headless_probe"] = {"ok": probe_ok, "evidence": probe_ev}
        if probe_ok:
            manifest["transport"]["command_note"] = (
                f"plugin_forge 生成（2026-09-28）：{spec.org_member} headless 探针实证："
                f"{probe_ev}。"
            )
            manifest["notes"] = (
                f"plugin_forge 生成插片（{spec.gen_note}）。"
                f"✅ headless 已实测（2026-09-28 探针：{probe_ev}）。"
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            spec.headless_verified = True
        else:
            logger.warning("headless 探针未过（debt 保留）: %s", probe_ev)

    # 段 3：自注册（hot_reload 拾取 → registry_loader 验证）
    if auto_register:
        try:
            from lingclaude.engine.hot_reload_trigger import HotReloadTrigger
            from lingclaude.plugins.agents import registry_loader
            HotReloadTrigger().check()  # 强制一轮拾取（绕过节流：新目录 diff 必触发）
            reg = registry_loader.load_all()
            registered = reg.get("registered", [])
            expect_key = f"agent/{spec.org_member}"
            result.registered = expect_key in registered
            result.register_keys = [k for k in registered if spec.org_member in k]
            if not result.registered:
                result.error = f"注册验证失败: {expect_key} 未在 registered 列表"
        except Exception as e:
            result.error = f"自注册段异常（fail-soft，产物已落盘）: {e}"
            logger.warning("plugin_forge 自注册失败: %s", e)

    result.ok = result.self_test.get("ok", False) and (
        result.registered if auto_register else True)

    # 段 4：记录
    _record_forge(spec, result)
    return result


def forge_mcp_wrap(spec: McpWrapSpec, *, auto_register: bool = True) -> ForgeResult:
    """mcp_wrap 生成回路主编排：生成 → 自测 → 落盘 → 自注册 → 记录。

    与 forge_cli_agent 同四段语义；差异在自测闸门（N2/N3 声明校验 +
    MCP 启动命令存在性）与产物路径（{ns}_{name} 目录）。
    """
    result = ForgeResult(ok=False, plugin_dir=str(spec.plugin_dir))

    # 幂等守卫：目录已存在且非空 → 拒绝覆盖（add-only 语义，对齐 hot_reload）
    if spec.plugin_dir.exists() and any(spec.plugin_dir.iterdir()):
        result.error = f"目录已存在且非空: {spec.plugin_dir}（add-only，拒绝覆盖）"
        _record_forge(spec, result)
        return result

    # 段 1：生成
    manifest = _render_mcp_manifest(spec)
    plugin_src = _render_mcp_plugin_py(spec)

    # 段 2：自测（落盘前——不干净的产物不进插件目录）
    result.self_test = _self_test_mcp(spec, manifest, plugin_src)
    if not result.self_test.get("ok"):
        result.error = f"自测未过: {result.self_test.get('errors')}"
        _record_forge(spec, result)
        return result

    # 落盘
    spec.plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = spec.plugin_dir / "manifest.agent.json"
    plugin_path = spec.plugin_dir / "plugin.py"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    plugin_path.write_text(plugin_src, encoding="utf-8")
    result.generated_files = [str(manifest_path), str(plugin_path)]

    # 段 2b：headless 探针（initialize 握手——验证链最后一块）。
    # debt 语义同 cli_agent：失败保留 verified=False 债务，通过才翻标重写 manifest。
    if not spec.verified:
        probe_ok, probe_ev = _probe_mcp_initialize(spec)
        result.self_test["headless_probe"] = {"ok": probe_ok, "evidence": probe_ev}
        if probe_ok:
            manifest["transport"]["command_note"] = (
                f"plugin_forge 生成（2026-09-28）：{spec.name} MCP wrap "
                f"({spec.mode})，initialize 握手实证：{probe_ev}。"
            )
            manifest["notes"] = (
                f"plugin_forge 生成插片（mcp_wrap，trust={spec.trust_level}，"
                f"plug={spec.plug_level}）。"
                f"✅ initialize 已实测（2026-09-28 探针：{probe_ev}）。"
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
            spec.verified = True
        else:
            logger.warning("initialize 探针未过（debt 保留）: %s", probe_ev)

    # 段 3：自注册（hot_reload 拾取 → registry_loader 验证）
    if auto_register:
        try:
            from lingclaude.engine.hot_reload_trigger import HotReloadTrigger
            from lingclaude.plugins.agents import registry_loader
            HotReloadTrigger().check()  # 强制一轮拾取（绕过节流：新目录 diff 必触发）
            reg = registry_loader.load_all()
            registered = reg.get("registered", [])
            expect_key = spec.seam_key
            result.registered = expect_key in registered
            result.register_keys = [k for k in registered if spec.name in k]
            if not result.registered:
                result.error = f"注册验证失败: {expect_key} 未在 registered 列表"
        except Exception as e:
            result.error = f"自注册段异常（fail-soft，产物已落盘）: {e}"
            logger.warning("plugin_forge 自注册失败: %s", e)

    result.ok = result.self_test.get("ok", False) and (
        result.registered if auto_register else True)

    # 段 4：记录
    _record_forge(spec, result)
    return result


def forge_from_yaml(yaml_text: str, *, auto_register: bool = True) -> ForgeResult:
    """YAML 输入契约 → Spec → forge。LLM 只允许填 YAML 语义槽。

    forge_type: cli_agent | mcp_wrap
    """
    import yaml  # 局部 import（pyyaml 已是依赖）
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict):
        return ForgeResult(ok=False, error="YAML 顶层必须是 mapping")
    if data.get("forge_type") == "mcp_wrap":
        return forge_mcp_wrap_from_dict(data, auto_register=auto_register)
    if data.get("forge_type") != "cli_agent":
        return ForgeResult(ok=False, error=f"只支持 forge_type=cli_agent|mcp_wrap，收到: {data.get('forge_type')}")
    try:
        spec = ForgeSpec(
            org_member=data["org_member"],
            display_name=data["display_name"],
            cli_path=data["cli_path"],
            headless_args=data.get("headless_args", []),
            prompt_mode=data.get("prompt_mode", "stdin"),
            call_timeout_s=int(data.get("call_timeout_s", 600)),
            capabilities=data.get("capabilities", ["code_generation"]),
            kernel_note=data.get("kernel_note", ""),
            headless_verified=bool(data.get("headless_verified", False)),
        )
    except KeyError as e:
        return ForgeResult(ok=False, error=f"YAML 缺必填字段: {e}")
    return forge_cli_agent(spec, auto_register=auto_register)


def forge_mcp_wrap_from_dict(data: dict[str, Any], *, auto_register: bool = True) -> ForgeResult:
    """mcp_wrap YAML 契约 → McpWrapSpec → forge_mcp_wrap。"""
    try:
        spec = McpWrapSpec(
            name=data["name"],
            display_name=data["display_name"],
            command=list(data["command"]),
            cwd=data.get("cwd", "") or "",
            mode=data.get("mode", "stdio"),
            ns=data.get("ns", "agent"),
            mcp_tool=data.get("mcp_tool", "execute_command"),
            tool_arg_name=data.get("tool_arg_name", "command"),
            trust_level=data.get("trust_level", "T2"),
            plug_level=data.get("plug_level", "L1"),
            capabilities=data.get("capabilities", []) or [],
            kernel_note=data.get("kernel_note", ""),
            startup_timeout_s=int(data.get("startup_timeout_s", 30)),
            call_timeout_s=int(data.get("call_timeout_s", 120)),
            verified=bool(data.get("verified", False)),
        )
    except KeyError as e:
        return ForgeResult(ok=False, error=f"YAML 缺必填字段: {e}")
    if not spec.command:
        return ForgeResult(ok=False, error="command 不能为空（实测过的完整启动命令，踩坑 1 禁 npx）")
    return forge_mcp_wrap(spec, auto_register=auto_register)
