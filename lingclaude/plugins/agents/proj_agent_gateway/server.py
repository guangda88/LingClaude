"""灵克外部 Agent 网关 MCP 薄壳（agent/proj-agent-gateway，第二层试验田：lc 调外部编程 agent）。

Hermes WebUI / Orca / Hermes Studio 在 lc 侧的对应物——把 cc、codex、crush、
opencode、atomcode(ac) 这一批外部编程 agent 统一插片化，供 lc 像调自家成员一样调用。

能力面（5 个 MCP 工具，薄壳=协议翻译，零业务逻辑，J1 不抄 agent kernel）：
- agent_invoke(agent, prompt, mode)  单轮驱动（codex 可 exec/review/resume；ac 走 headless -p）
- agent_chat(agent, prompt, session) 会话型（opencode run/serve、crush server 复用会话）
- agent_batch(agents, prompt)        多 agent 并行出结论（各 agent 独立子进程）
- agent_status()                      5 家可达性探活（command -v + version 冒烟）
- agent_list()                        返回支持的 agent 清单与能力矩阵

ac 特判（daemon goal 面 401 阻塞的替代）：ac 的 `agent_invoke` 走 headless
`atomcode -p` 子进程（实测 PONG 通），返回结论 + resume 句柄，不碰 daemon token。

铁律锚点（每行代码过法）：
- 铁律 1：本薄壳全在 plugins/agents/proj_agent_gateway/，core/ 零 diff；
- 铁律 3/J4：每次工具调用记 agent_run:agent-gateway record（转发+错误结构化，失败如实入账）；
- 铁律 6：trust_level=T3（外部工具只观测）+ plug_level=L2（缺席降级：单 agent 挂了不影响其余）；
- 铁律 7：缝 key 带域前缀 agent/proj-agent-gateway（N3 守卫消费，外部工程域）；
- J1 薄壳纪律：每个 agent 只做 subprocess 分发+超时+错误结构化，不实现 agent 自身业务逻辑；
  各 agent 的调用形态固化在 _AGENTS 表（数据，非结构），改 agent 入口只改表不动代码。

传输：MCP stdio（FastMCP，python3 直启，避 npx OOM 踩坑 #1/#2）。
各 agent 子进程超时独立（call_timeout_s 取 _AGENTS[agent].timeout_s）。

实测锚点（2026-09-18 冒烟）：
- claude -p "say OK" → OK（非交互 print 模式）
- codex exec "reply PONG" → PONG（可 resume/fork/review）
- atomcode -p "reply PONG" → PONG + session resume 提示（headless，daemon goal 面 401 的替代）
- crush run / opencode run 非交互协议在位（server 常驻会话形态见 agent_chat）
"""
from __future__ import annotations

import json
import re
import shutil
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("agent-gateway", instructions=(
    "灵克外部 Agent 网关 MCP 薄壳。把 cc/codex/crush/opencode/atomcode 这批外部编程 agent "
    "暴露为 lc 可调用的统一工具面（Hermes WebUI 式的多 agent 聚合），供 lingclaude 调用。"
    "薄壳只做子进程分发+错误结构化（J1 不抄 agent kernel），单 agent 缺席降级不影响其余（L2）。"
))

DEFAULT_TIMEOUT_S = 120
MAX_OUTPUT = 6000


def _worktree_enabled() -> bool:
    """P1-6 通电（2026-09-23）：默认启用 worktree 扇出。

    语义：默认开；env LINGCLAUDE_AGENT_WORKTREE 显式为 0/false/no/off 时关闭；
    =1/true/TRUE 等旧开法兼容。非 git 仓库等不可用场景由调用方降级 scratch，
    不在此处理。
    """
    raw = os.environ.get("LINGCLAUDE_AGENT_WORKTREE", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


# ── agent 调用形态表（数据，非结构：改 agent 入口只改这里）─────────────────────
# 每个 agent：探测命令（可达性）+ 调用构建（agent, prompt, mode → argv）
# + model/provider 透传拼接形态（quota_args：(argv, model, provider) → 追加的 argv 片段）。
# ac 走 headless -p（daemon goal 面 401 阻塞，已改子进程封装，绕开 token 鉴权坑）。
#
# 配额耗尽换模型（2026-09-18）：每 agent 声明 model/provider 的 CLI 拼接形态
# （数据驱动，J1 薄壳只透传不判业务）。默认不强制指定 → 各 agent 走自身默认；
# 调用方可经 agent_invoke(model=, provider=) 显式指向 lc 套餐模型清单
# （/home/ai/lingcode/config.json 的 providers，glm/minimax/volcengine/kimi/agnes 等），
# 绕开单家按量配额墙。各家 CLI 形态不同，拼法固化在 QUOTA_ARGV 表：
#   cc       --model <m>            （provider 走 key/套餐内，CLI 无独立 provider 旗）
#   codex    -m <m>                 （provider 走 -c model_providers/profile，透传 model 为主）
#   crush    -m <provider/model>    （provider 编进 model 串消歧）
#   opencode -m <provider/model>    （同上）
#   ac       --provider <p> --model <m>  （双旗，最灵活，可整体换计费通道）
# crush/opencode 的 provider/model 消歧：调用方直接传 "provider/model" 串进 model 参数，
# 薄壳不代拆（J1）——故 crush/opencode 只透传单占位 {model}（值可含 provider 前缀）。
QUOTA_ARGV: dict[str, list[str]] = {
    "cc":       ["--model", "{model}"],
    "codex":    ["-m", "{model}"],
    "crush":    ["-m", "{model}"],
    "opencode": ["-m", "{model}"],
    "ac":       ["--provider", "{provider}", "--model", "{model}"],
}

# 套餐 profile 透传（2026-09-19）：codex 的 -p <profile> 切换整套套餐
# （model+provider+reasoning_effort 打包在 ~/.codex/<profile>.config.toml）。
# 各家 profile 旗形态不同，数据驱动：
#   codex  -p {profile}  （minimax/minmax/kimi/volc/vol/voc，实测 PONG 通）
#   其余   无独立 profile 旗（套餐走各自 config/key），透传空
PROFILE_ARGV: dict[str, list[str]] = {
    "codex": ["-p", "{profile}"],
}

# cc 默认套餐模型（2026-09-19 用户已把 cc 模型切到 M3，配额墙内最优）。
# 调用方未显式传 model 时，cc 走 M3 而非 cc 自身默认（避按量 Anthropic 配额墙）。
DEFAULT_MODEL: dict[str, str] = {
    "cc": "M3",
}

_AGENTS: dict[str, dict] = {
    "cc": {
        "name": "claude",
        "bin": "claude",
        "desc": "Claude Code（cc）非交互 print 模式",
        "timeout_s": 180,
        "invoke": lambda p, m: ["claude", "-p", p, "--output-format", "text"],
        "probe": ["claude", "--version"],
    },
    "codex": {
        "name": "codex",
        "bin": "codex",
        "desc": "Codex CLI（exec 单轮 / review 代码审查 / resume 续接）",
        "timeout_s": 180,
        "invoke": lambda p, m: (
            ["codex", "review", p] if m == "review"
            else ["codex", "exec", p]
        ),
        "probe": ["codex", "--version"],
    },
    "crush": {
        "name": "crush",
        "bin": "crush",
        "desc": "Crush（run 单轮；server 常驻 unix-socket 会话复用）",
        "timeout_s": 180,
        "invoke": lambda p, m: ["crush", "run", p],
        "probe": ["crush", "--version"],
    },
    "opencode": {
        "name": "opencode",
        "bin": "opencode",
        "desc": "OpenCode（run 非交互；serve 常驻 HTTP；acp 双向会话）",
        "timeout_s": 180,
        "invoke": lambda p, m: ["opencode", "run", p],
        "probe": ["opencode", "--version"],
    },
    "ac": {
        "name": "atomcode",
        "bin": "atomcode",
        "desc": "AtomCode headless -p（daemon goal 面 401 的替代；返回结论 + resume session id）",
        "timeout_s": 180,
        "invoke": lambda p, m: ["atomcode", "-p", p],
        "probe": ["atomcode", "--version"],
    },
}


def _quota_args(agent: str, model: str = "", provider: str = "") -> list[str]:
    """按 agent 的 CLI 形态拼 model/provider 透传 argv（数据驱动，J1 只透传不判业务）。

    model/provider 均空 → 返回空列表（走各 agent 自身默认，不强制指定）。
    ac：provider+model 双旗；crush/opencode：provider/model 串（调用方拼好传 model）；
    cc/codex：--model/-m 单旗（provider 走 key/套餐，薄壳不代拆）。
    """
    if not model and not provider:
        return []
    pat = QUOTA_ARGV.get(agent, ["--model", "{model}"])
    return [t.format(model=model or "default", provider=provider or "default")
            for t in pat]


def _profile_args(agent: str, profile: str = "") -> list[str]:
    """按 agent 的 CLI 形态拼 profile 透传 argv（数据驱动，J1 只透传不判业务）。

    仅 codex 支持 `-p <profile>` 切整套套餐；其余 agent 无 profile 旗，返回空。
    """
    if not profile:
        return []
    pat = PROFILE_ARGV.get(agent, [])
    return [t.format(profile=profile) for t in pat]


# ── agent 级健康门禁（2026-09-19；2026-09-24 L2① 分级冷却 / L2②a 梯子+半开）─────
# 教训：opencode GLM 周限额满，靠 60-72s 超时试错才发现；cc exit=0 但 stderr
# 带 unrecognized_model 警告（degraded）。failed 一次入冷却；degraded 属软失败
# （exit=0），连续 _DEGRADED_LIMIT 次才入冷却——单次告警可能是瞬时回声，不过度
# 熔断。冷却期内 dispatch 直接跳过（fast-fail，不撞墙）；冷却时长沿梯子连败升级
# （900s→1800s→3600s 封顶），到期转半开探测：首个到达调用放行探测，并发者仍被
# 拒（防探测风暴——一次真实探测要 60-72s）；探测成功 _mark_ok 全复位，失败沿梯
# 子升级重入。force=True 可强制重试（绕过一切门禁）。
_COOLDOWN_LADDER = (900, 1800, 3600)   # 冷却梯子 15m→30m→1h：连败升级，封顶 1h（L2②a）
_DEGRADED_LIMIT = 3        # degraded 连败阈值：连续 N 次软告警按冷却处理（L2①）
_health: dict[str, dict] = {}   # agent → {"failed_until","reason","level","fail_count","half_open"}
_degraded_streak: dict[str, int] = {}   # agent → 连续 degraded 计数（一次 ok 即全复位）


def _mark_ok(agent: str) -> None:
    """成功即全复位（半开探测闭环，2026-09-24 L2②a）：
    探测成功 = 恢复——冷却状态、连败梯子计数、degraded 连败计数全部清零。"""
    _health.pop(agent, None)
    _degraded_streak.pop(agent, None)


def _mark_failed(agent: str, reason: str, level: str = "failed") -> None:
    """冷却记入（分级 + 连败升级梯子，2026-09-24 L2①/②a）：

    - failed：立即入冷却，时长沿 _COOLDOWN_LADDER 按连败次数升级
      （15m→30m→1h 封顶）——反复失败说明重试无意义在加深，冷却应递增；
    - degraded：exit=0 但有配额/模型告警——软失败，连续 _DEGRADED_LIMIT 次才入
      冷却（单次告警可能是瞬时回声，避免过度熔断）；计数挂 _degraded_streak，
      一次 ok（_mark_ok）即全复位。

    半开探测闭环：冷却到期转 half_open（_health_check 放行单次探测），探测成功
    _mark_ok 全复位；探测失败回到本函数，fail_count+1 沿梯子升级重入冷却。
    J4：如实记因由，不假活。
    """
    if level == "degraded":
        streak = _degraded_streak.get(agent, 0) + 1
        if streak < _DEGRADED_LIMIT:
            _degraded_streak[agent] = streak
            h0 = _health.get(agent)
            if h0 and time.monotonic() >= h0["failed_until"]:
                _health.pop(agent, None)   # 半开探测后 degraded<阈：冷却已过期，清态放行（防 limbo）
            return
        _degraded_streak.pop(agent, None)   # 入冷却即清计数
        reason = f"degraded×{streak}: {reason}"
    else:
        _degraded_streak.pop(agent, None)   # hard failed 覆盖任何残余计数（回归修复）
    h = _health.get(agent)
    fail_count = (h.get("fail_count", 1) + 1) if h else 1   # 梯子记忆：探测失败继续升级
    idx = min(fail_count, len(_COOLDOWN_LADDER)) - 1
    _health[agent] = {"failed_until": time.monotonic() + _COOLDOWN_LADDER[idx],
                      "reason": reason[:200], "level": level,
                      "fail_count": fail_count, "half_open": False}


def _health_check(agent: str, force: bool = False) -> dict | None:
    """冷却期内返回 {'cooldown': True, ...}（调用方跳过该 agent）；否则 None。

    半开探测（2026-09-24 L2②a）：冷却到期不再直接清除，转 half_open——第一个
    到达的调用放行为探测（返回 None），后续并发调用仍按冷却拒绝（防探测风暴：
    探测一次真实调用要 60-72s，不能每个请求都去撞墙）。探测结果由调用点回写：
    成功 _mark_ok 全复位 / 失败 _mark_failed 沿梯子升级重入冷却。
    force=True 绕过一切门禁（人工强推，语义不变）。
    """
    h = _health.get(agent)
    if h is None:
        return None
    if time.monotonic() < h["failed_until"]:
        if force:
            return None
        return {"cooldown": True, "remaining_s": round(h["failed_until"] - time.monotonic()),
                "reason": h["reason"]}
    # 冷却已到期：half_open 转换（幂等），首个到达者放行探测
    if not h.get("half_open"):
        h["half_open"] = True
        return None                                  # 本调用即探测者，放行
    if force:
        return None
    return {"cooldown": True, "remaining_s": 0, "half_open": True,
            "reason": f"探测中(半开): {h['reason']}"}


# 失败判定（结构化，薄壳只做信号匹配不判业务）：非零 exit / 超时 / stderr 配额墙特征。
_QUOTA_PAT = re.compile(
    r"(limit\s+exhausted|weekly|monthly|rate.?limit|429|quota|credits?error"
    r"|no payment method|unrecognized_model)", re.I)


def _classify(res: dict) -> str:
    """单次运行结果分级：ok / degraded（exit=0 但 stderr 有配额/模型告警）/ failed。"""
    if res.get("timed_out") or res.get("exit", 0) != 0:
        return "failed"
    if _QUOTA_PAT.search(res.get("stderr", "") or ""):
        return "degraded"      # exit=0 但有告警（如 cc unrecognized_model）——L2①：连败入冷却
    return "ok"


def _fallback_of(agent: str) -> str | None:
    """失败改派链（数据）：opencode→crush（能力重叠，headless 非交互）；其余暂无。"""
    return {"opencode": "crush"}.get(agent)


def _which(agent: str) -> str | None:
    return shutil.which(_AGENTS[agent]["bin"])


def _run(argv: list[str], timeout_s: int, cwd: str = "/home/ai/lingclaude") -> dict:
    """跑外部 agent 子进程，结构化返回（exit/stdout/stderr/timeout），薄壳不判业务。"""
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=timeout_s, cwd=cwd)
        return {
            "exit": r.returncode,
            "stdout": r.stdout[-MAX_OUTPUT:],
            "stderr": r.stderr[-1500:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {"exit": -1, "stdout": "", "stderr": f"timeout {timeout_s}s", "timed_out": True}
    except FileNotFoundError:
        return {"exit": -2, "stdout": "", "stderr": f"binary not found: {argv[0]}", "timed_out": False}
    except OSError as e:
        return {"exit": -3, "stdout": "", "stderr": str(e), "timed_out": False}


# ── 用量/会话句柄抽取（T3 只观测的账面，2026-09-19）────────────────────────────
# token 用量：codex stdout 尾部 "tokens used\n14,564"；其余各家暂无统一格式，抽不到为 None。
_TOKEN_PAT = re.compile(r"tokens?\s*used[:\s\n]*([\d,]+)", re.I)
# resume 句柄：ac stderr "atomcode -p … --resume <uuid>"；codex "session id: <uuid>"。
_SESSION_PATS = (
    re.compile(r"--resume\s+([0-9a-f-]{20,})", re.I),
    re.compile(r"session\s+id[:\s]+([0-9a-f-]{20,})", re.I),
)


def _enrich(res: dict) -> dict:
    """从 stdout/stderr 抽 token 用量 + resume session_id，补进结果（抽不到为 None）。"""
    blob = (res.get("stdout", "") or "") + "\n" + (res.get("stderr", "") or "")
    tok = _TOKEN_PAT.search(blob)
    res["tokens_used"] = int(tok.group(1).replace(",", "")) if tok else None
    sid = None
    for pat in _SESSION_PATS:
        sid = pat.search(blob)
        if sid:
            break
    res["resume_session_id"] = sid.group(1) if sid else None
    return res


# agent_run record 落盘（J4：每次调用入账，失败也记；N1 对账口径）。
# parents[4] = <repo>（同 SCRATCH_ROOT，双层目录实测偏一层教训）。
_RUNS_DIR = Path(__file__).resolve().parents[4] / "data" / "agent_runs" / "agent-gateway"


def _record(res: dict) -> None:
    """单次调用结果落 agent_run record（含 tokens/session 句柄，账面可对）。"""
    try:
        _RUNS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"{time.strftime('%Y%m%d_%H%M%S')}_{res.get('agent', 'x')}_{uuid.uuid4().hex[:6]}.json"
        (_RUNS_DIR / name).write_text(
            json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass  # 账面失败不阻断主流程（record 本身如实缺席，J4 不假活）


# ── MCP 工具面（lc 侧可调用）────────────────────────────────────────────
@mcp.tool()
def agent_invoke(agent: str, prompt: str, mode: str = "default",
                 cwd: str = "/home/ai/lingclaude",
                 model: str = "", provider: str = "",
                 profile: str = "", force: bool = False,
                 fallback: bool = True) -> str:
    """单轮驱动一个外部 agent，取纯文本结论。

    agent: cc | codex | crush | opencode | ac
    mode:  默认 'default'；codex 支持 'review'（代码审查）/'resume'（续会话）
    cwd:   子进程工作目录（默认 lc 仓）
    model/provider: 可选，配额耗尽时换模型/换计费通道（数据驱动拼接，J1 只透传不判业务）。
        默认空 → cc 走 DEFAULT_MODEL（M3），其余 agent 走自身默认。指向 lc 套餐模型
        （/home/ai/lingcode/config.json providers：glm/minimax/volcengine/kimi/agnes）
        可绕开单家按量配额墙。ac 双旗（--provider/--model），crush/opencode 用
        "provider/model" 串传进 model，cc/codex 只透传 model（provider 走 key/套餐）。
    profile: 可选（codex 专用），经 `-p <profile>` 切换整套套餐
        （model+provider+reasoning_effort 打包在 ~/.codex/<profile>.config.toml）。
        可用 profile：minimax / minmax / kimi / volc / vol / voc（实测 PONG 通）。
    force: 失败冷却期内强制重试（默认 False，冷却内 fast-fail）。
    fallback: 失败时自动改派（默认 True，如 opencode→crush，结果带 via 字段标注同源）。

    返回 JSON 字符串：{agent, mode, exit, stdout, stderr, timed_out, health,
                       model, provider, profile, [via]}。
    health: ok / degraded（exit=0 但 stderr 有配额/模型告警）/ failed / cooldown。
    agent 侧故障（配额耗尽/限流）如实透传 exit+stderr，不假活（L2）。
    """
    if agent not in _AGENTS:
        return json.dumps({"error": f"unknown agent {agent!r}",
                           "supported": sorted(_AGENTS)})

    def _dispatch(a: str) -> dict:
        spec = _AGENTS[a]
        argv = spec["invoke"](prompt, mode)
        m = model or DEFAULT_MODEL.get(a, "")
        argv += _quota_args(a, model=m, provider=provider)
        argv += _profile_args(a, profile=profile)
        res = _run(argv, spec.get("timeout_s", DEFAULT_TIMEOUT_S), cwd=cwd)
        res.update({"agent": a, "mode": mode, "cwd": cwd,
                    "model": m or None, "provider": provider or None,
                    "profile": profile or None,
                    "health": _classify(res)})
        return res

    # 健康门禁：冷却期内 fast-fail（不撞墙），force 可穿透
    gate = _health_check(agent, force=force)
    if gate:
        return json.dumps({"agent": agent, "health": "cooldown",
                           "cooldown": gate, "forced": False}, ensure_ascii=False)

    res = _dispatch(agent)
    res = _enrich(res)
    if res["health"] == "ok":
        _mark_ok(agent)
    elif res["health"] in ("failed", "degraded"):
        _mark_failed(agent, res["stderr"] or f"exit={res['exit']}",
                     level=res["health"])
    _record(res)
    # 失败自动改派（一次，不递归）：结果带 via 标注同源，原失败原因保留在 fallback_from
    if res["health"] == "failed" and fallback:
        alt = _fallback_of(agent)
        if alt and alt in _AGENTS and _which(alt) and not _health_check(alt):
            alt_res = _enrich(_dispatch(alt))
            alt_res["via"] = agent          # 同源标注：alt 是替 agent 跑的
            alt_res["fallback_from"] = {"agent": agent, "stderr": res["stderr"][:300],
                                        "exit": res["exit"]}
            _record(alt_res)
            return json.dumps(alt_res, ensure_ascii=False)
    return json.dumps(res, ensure_ascii=False)


@mcp.tool()
def agent_chat(agent: str, prompt: str, session_id: str = "",
               cwd: str = "/home/ai/lingclaude",
               model: str = "", provider: str = "",
               profile: str = "") -> str:
    """会话型驱动外部 agent（复用已有会话）。

    agent: cc | codex | crush | opencode | ac
    session_id: 有 resume 语义的 agent（codex/ac）透传续接；其余忽略走新会话
    model/provider: 可选，同 agent_invoke（配额耗尽换模型/通道，J1 只透传不判业务）
    profile: 可选（codex 专用），同 agent_invoke（`-p <profile>` 切整套套餐）

    当前实现：agent 支持 headless resume 时透传 session_id；不支持的 agent
    忽略 session_id 走新会话。薄壳只透传不判业务（J1）。

    返回 JSON 字符串：{agent, session_id, model, provider, profile, ...agent_invoke 结果}。
    """
    if agent not in _AGENTS:
        return json.dumps({"error": f"unknown agent {agent!r}",
                           "supported": sorted(_AGENTS)})
    spec = _AGENTS[agent]
    argv = list(spec["invoke"](prompt, "default"))
    # 有 resume 语义的 agent（codex/ac）追加 session 参数
    if session_id and agent in ("codex", "ac"):
        argv += ["--resume", session_id] if agent == "ac" else ["resume", session_id]
    model = model or DEFAULT_MODEL.get(agent, "")
    argv += _quota_args(agent, model=model, provider=provider)
    argv += _profile_args(agent, profile=profile)
    res = _enrich(_run(argv, spec.get("timeout_s", DEFAULT_TIMEOUT_S), cwd=cwd))
    res.update({"agent": agent, "session_id": session_id,
                "model": model or None, "provider": provider or None,
                "profile": profile or None, "health": _classify(res)})
    _record(res)
    return json.dumps(res, ensure_ascii=False)


# dispatch scratch 根（每次 agent_batch 建独立子目录，外部 agent 产物先落 scratch，
# lc 回收归位——禁外部 agent 直写共享路径，防多 agent 撞写同一文件）。
# 路径基准：server.py 在 <repo>/lingclaude/plugins/agents/proj_agent_gateway/，
# parents[4] = <repo>（parents[3] 是 <repo>/lingclaude，双层目录，实测偏一层）。
SCRATCH_ROOT = Path(__file__).resolve().parents[4] / "data" / "agent_dispatch"
if __name__ == "__main__":  # pragma: no cover
    pass


def _new_dispatch_dir() -> Path:
    """建一次 dispatch 的独立 scratch 目录（时间戳+uuid，不重不撞）。"""
    d = SCRATCH_ROOT / f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


@mcp.tool()
def agent_batch(agents: list[str], prompt: str,
                model: str = "", provider: str = "",
                profile: str = "") -> str:
    """并行驱动多个外部 agent 对同一 prompt 各出一版结论（多 agent 聚合，真并行）。

    agents: [cc, codex, ...]（子集）；单 agent 失败/缺席不影响其余（L2 降级）。
    model/provider: 可选（同 agent_invoke），对全部 agents 透传同一 model/provider。
        各 agent 未显式指定时按 DEFAULT_MODEL 表走（cc → M3）。
    profile: 可选（codex 专用），透传给 codex（其余 agent 忽略，无 profile 旗）。

    隔离（2026-09-19）：每次调用建独立 scratch 目录（data/agent_dispatch/<ts>_<uuid>/），
    各 agent 子进程 cwd 落各自 scratch 子目录（<scratch>/<agent>/），禁止直写共享路径
    防多 agent 撞写同一文件（本轮 crush/ac 同写 docs/research/ 一个文件的教训）。
    调用方从返回 JSON 的 scratch_dir 字段回收各家产物。

    并行：ThreadPoolExecutor 真并发（此前串行 for 是伪并行，5 家 ~4min → 最慢节点决定）。

    返回 JSON 字符串：{prompt, scratch_dir, wall_s,
                       results:{agent:{exit,stdout,...}}, 缺席的 agent 标注 absent}。
    """
    dispatch = _new_dispatch_dir()
    t0 = time.monotonic()

    # P1-6 通电（2026-09-23）：worktree 扇出默认开——评审共识 P0「改默认值+补钩子」。
    # 默认启用：本目录是 git 仓库时每个 agent 的 cwd 从 scratch 子目录升级为
    # 独立 git worktree（真实分支，产物可 diff/merge 择优）；非 git 仓库 /
    # 建树失败 → 降级 scratch（原行为，不崩）。env LINGCLAUDE_AGENT_WORKTREE=0
    # 显式退回关闭（=1/true 旧开法继续兼容）。
    worktree_root: dict[str, object] = {}
    ws = None
    if _worktree_enabled():
        try:
            from lingclaude.core.worktree import WorktreeSession
            ws = WorktreeSession(repo=str(Path.cwd()))
            if not ws.available:
                ws = None
        except Exception:  # noqa: BLE001 — worktree 不可用降级 scratch
            ws = None

    def _agent_cwd(a: str) -> Path:
        """agent 工作目录：worktree 启用则建独立 worktree，否则 scratch 子目录。"""
        if ws is not None:
            try:
                wt = ws.create(f"gw-{a}")
                worktree_root[a] = {"path": str(wt.path), "branch": wt.branch,
                                    "task_id": wt.task_id}
                return wt.path
            except Exception as e:  # noqa: BLE001 — 建树失败降级 scratch
                worktree_root[a] = {"error": str(e)[:200]}
        d = dispatch / a
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _one(a: str) -> tuple[str, object]:
        if a not in _AGENTS:
            return a, {"error": f"unknown agent {a!r}"}
        spec = _AGENTS[a]
        if _which(a) is None:
            return a, {"absent": True, "bin": spec["bin"]}  # L2：缺席降级
        # 健康门禁：冷却期内该 agent 直接跳过（不撞墙）；结果照实入 results
        gate = _health_check(a)
        if gate:
            return a, {"agent": a, "health": "cooldown", "cooldown": gate}
        argv = list(spec["invoke"](prompt, "default"))
        m = model or DEFAULT_MODEL.get(a, "")
        argv += _quota_args(a, model=m, provider=provider)
        argv += _profile_args(a, profile=profile)
        cwd = _agent_cwd(a)          # worktree 或 scratch 子目录
        res = _enrich(_run(argv, spec.get("timeout_s", DEFAULT_TIMEOUT_S), cwd=str(cwd)))
        res.update({"model": m or None, "provider": provider or None,
                    "profile": profile or None, "scratch": str(cwd),
                    "health": _classify(res)})
        if res["health"] == "ok":
            _mark_ok(a)
        elif res["health"] in ("failed", "degraded"):
            _mark_failed(a, res["stderr"] or f"exit={res['exit']}",
                         level=res["health"])
        _record(res)
        # 失败自动改派（一次）：替跑结果带 via 标注同源
        if res["health"] == "failed":
            alt = _fallback_of(a)
            if alt and alt in _AGENTS and _which(alt) and not _health_check(alt):
                alt_cwd = dispatch / alt
                alt_cwd.mkdir(parents=True, exist_ok=True)
                alt_argv = list(_AGENTS[alt]["invoke"](prompt, "default"))
                alt_m = model or DEFAULT_MODEL.get(alt, "")
                alt_argv += _quota_args(alt, model=alt_m, provider=provider)
                alt_argv += _profile_args(alt, profile=profile)
                alt_res = _enrich(_run(alt_argv, _AGENTS[alt].get("timeout_s", DEFAULT_TIMEOUT_S),
                                       cwd=str(alt_cwd)))
                alt_res.update({"model": alt_m or None, "provider": provider or None,
                                "profile": profile or None, "scratch": str(alt_cwd),
                                "health": _classify(alt_res), "via": a,
                                "fallback_from": {"agent": a, "exit": res["exit"],
                                                  "stderr": res["stderr"][:300]}})
                _record(alt_res)
                return a, alt_res
        return a, res

    results: dict[str, object] = {}
    with ThreadPoolExecutor(max_workers=len(agents) or 1) as ex:
        futs = {ex.submit(_one, a): a for a in agents}
        for fut in as_completed(futs):
            a, res = fut.result()
            results[a] = res

    wall = round(time.monotonic() - t0, 1)
    return json.dumps({"prompt": prompt[:200], "scratch_dir": str(dispatch),
                       "wall_s": wall, "results": results,
                       "worktree": worktree_root or None,
                       "worktree_enabled": ws is not None}, ensure_ascii=False)


@mcp.tool()
def agent_status() -> str:
    """探活 5 家外部 agent（command -v 可达性 + version 冒烟），L2 降级依据。

    返回 JSON：{agent:{bin, available, version}}，version 取探测输出首行。
    """
    out: dict[str, object] = {}
    for a, spec in _AGENTS.items():
        binpath = _which(a)
        if binpath is None:
            out[a] = {"bin": spec["bin"], "available": False, "version": None}
            continue
        r = _run(spec["probe"], 15)
        out[a] = {"bin": spec["bin"], "available": True,
                  "version": (r["stdout"].strip().splitlines() or [""])[0][:80]}
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def agent_list() -> str:
    """返回支持的 agent 清单与能力矩阵（哪些支持 review/resume/会话）。"""
    return json.dumps([
        {"agent": a, "name": s["name"], "bin": s["bin"], "desc": s["desc"],
         "supports_review": a == "codex",
         "supports_resume": a in ("codex", "ac"),
         "supports_session": a in ("codex", "ac", "crush", "opencode")}
        for a, s in _AGENTS.items()
    ], ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
