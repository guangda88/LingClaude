"""灵克守卫 MCP 薄壳（agent/lc-guard，铁律 5 双向互认：ac 调 lc 守卫面）。

atomcode↔lc 互为插片实验的 lc 侧交付物（CODING_AGENT_PLUGIN_PLAN_L2_L3.md §1.3）：
- atomcode 是 MCP **客户端**（消费 MCP server tools，`.mcp.json` 连接）；
- lc 把自己封成 MCP **server** 薄壳，把守卫/台账面暴露为 MCP 工具；
- ac 调 `lc_audit_trigger` → 让工作少出错（用户目标 2）；
- 双向探针：ac 侧 MCP list_tools 探活 + lc 侧 health_probe（mcp_list_tools，候选铁律 8）。

铁律锚点（每行代码过法）：
- 铁律 1：薄壳全在 plugins/agents/lc_mcp_guard/，core/ 零 diff；
- 铁律 3/J4：每次工具调用记 agent_run:lc-guard record（转发+错误结构化，J4）；
- 铁律 5：federation_pair record（type=federation_pair key=lc-ac），N1 互账对账；
- 铁律 6：T1 全审计（lc 仓代码在手）+ L1 替换（薄壳可换其他实现，接口一致不崩）；
- 铁律 7：缝 key 带域前缀 agent/lc-guard（N3 守卫消费）；
- J1 薄壳纪律（mcp-wrap skill 守卫线）：封装层禁止业务判断，只转发 lc 对应实体
  （self_audit_trigger / 台账 / 灵族组织 / 铁律条文），不抄守卫 kernel。

传输：MCP stdio（FastMCP，python3 直启，避 npx OOM 踩坑 #1/#2）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("lc-guard", instructions=(
    "灵克（lingclaude）守卫/台账查询面 MCP 薄壳。"
    "把 lc 的铁律 1-8 守卫、arch_ledger 台账、ling_org 灵族组织、铁律条文暴露为工具，"
    "供 atomcode（MCP 客户端）调用，让工作少出错（用户目标 2：ac 调 lc 守卫）。"
    "薄壳只读转发，不写 lc 主干（J1 变化走接缝）。"
))

# lc 仓根（薄壳从仓根启动，cwd=/home/ai/lingclaude）
LC_ROOT = Path(os.environ.get("LC_ROOT", "/home/ai/lingclaude"))
AUDIT_SCRIPT = LC_ROOT / "scripts" / "self_audit_trigger.py"
LEDGER_DIR = LC_ROOT / "data" / "arch_ledger"
ORG_DIR = LC_ROOT / "data" / "ling_org"
LAW_DOC = LC_ROOT / "docs" / "LINGYUAN_IRON_LAW.md"


def _run_script(script: Path, extra_args: list[str]) -> dict:
    """跑 lc 仓脚本，结构化返回（stdout/exit/timeout），薄壳不判业务。"""
    try:
        r = subprocess.run(
            [sys.executable, str(script)] + extra_args,
            capture_output=True, text=True, timeout=60, cwd=str(LC_ROOT))
        return {"exit": r.returncode, "stdout": r.stdout[-4000:], "stderr": r.stderr[-1000:]}
    except subprocess.TimeoutExpired:
        return {"exit": -1, "stdout": "", "stderr": "timeout 60s"}
    except OSError as e:
        return {"exit": -2, "stdout": "", "stderr": str(e)}


# ── lc 守卫工具面（ac 侧可调用，薄壳只读转发）────────────────────────
@mcp.tool()
def lc_audit_trigger() -> dict:
    """触发自审（SDT-lc-006 返审触发器）：铁律/守卫指纹变化即审自身+台账核账。

    atomcode 改完 lc 相关文件后调用，确认守卫是否仍自洽、有无新违例——
    "让工作少出错"的直接手段。薄壳转发 scripts/self_audit_trigger.py，不判业务。
    返回 {exit, stdout, stderr}：exit=0 通过；非 0 = 触发审查（stdout 含审查结论）。
    """
    return _run_script(AUDIT_SCRIPT, [])


@mcp.tool()
def lc_audit_tasks() -> dict:
    """列未关闭的优化任务（self_audit_trigger --tasks）。

    薄壳转发，返回未关闭任务清单。供 ac 了解 lc 当前欠账，避免重蹈已登记的坑。
    """
    return _run_script(AUDIT_SCRIPT, ["--tasks"])


@mcp.tool()
def lc_ledger_query(ledger_type: str, key: str | None = None,
                    limit: int = 20) -> dict:
    """查 arch_ledger 台账（arch_debt/arch_audit_task/arch_law_revision 等）。

    ledger_type: 台账子目录名（arch_debt / arch_audit_task / arch_law_revision /
    arch_audit_state / arch_review / arch_exemption / arch_m6_snapshot /
    arch_reference / agent_registry / arch_audit_policy）；
    key: 可选 record 文件名（如 candzone-ironlaw8-faultdomain-arbitration.json），
    不给则列该类型全部 key；limit: 列 key 上限。
    薄壳只读文件，不改台账（J1：台账变更走 lc 仓审计流程，ac 无 lc 写权限）。
    """
    tdir = (LEDGER_DIR / ledger_type)
    if not tdir.is_dir():
        available = sorted(p.name for p in LEDGER_DIR.iterdir() if p.is_dir())
        return {"error": f"ledger_type '{ledger_type}' 不存在", "available": available}
    if key:
        p = tdir / key
        if not p.exists():
            # key 可能不带 .json 后缀
            p = tdir / f"{key}.json" if not key.endswith(".json") else p
        if p.exists():
            try:
                return {"type": ledger_type, "key": p.name,
                        "record": json.loads(p.read_text(encoding="utf-8"))}
            except (json.JSONDecodeError, OSError) as e:
                return {"type": ledger_type, "key": p.name, "error": str(e)}
        return {"error": f"key '{key}' 不存在于 {ledger_type}"}
    # 列全部 key（限 limit）
    keys = sorted(p.name for p in tdir.glob("*.json"))
    return {"type": ledger_type, "count": len(keys),
            "keys": keys[:limit], "truncated": len(keys) > limit}


@mcp.tool()
def lc_org_query(member: str | None = None, limit: int = 30) -> dict:
    """查 ling_org 灵族组织（org_member record）。

    member: 灵族成员拼音全称（lingflow/lingzhi/lingxi/...），不给则列全部成员；
    limit: 上限。薄壳只读，org_member 变更走 lc 仓审计流程（ac 无写权限）。
    """
    mdir = (ORG_DIR / "org_member")
    if not mdir.is_dir():
        return {"error": "org_member 目录不存在", "dir": str(mdir)}
    if member:
        p = mdir / f"{member}.json"
        if not p.exists():
            names = sorted(x.stem for x in mdir.glob("*.json"))
            # 模糊匹配（拼音全称 vs 简名，踩坑：12 子 key 是拼音全称）
            fuzzy = [n for n in names if member in n or n in member]
            return {"error": f"member '{member}' 未找到",
                    "fuzzy_match": fuzzy, "available": names}
        try:
            return {"member": member, "record": json.loads(p.read_text(encoding="utf-8"))}
        except (json.JSONDecodeError, OSError) as e:
            return {"member": member, "error": str(e)}
    names = sorted(x.stem for x in mdir.glob("*.json"))
    out = {}
    for n in names[:limit]:
        out[n] = json.loads((mdir / f"{n}.json").read_text(encoding="utf-8"))
    return {"count": len(names), "truncated": len(names) > limit, "members": out}


@mcp.tool()
def lc_law_read(section: str | None = None) -> dict:
    """读灵元哲学工程铁律条文（docs/LINGYUAN_IRON_LAW.md）。

    section: 可选锚点（如 "一" / "二" / "铁律8" / "J5"），不给则读全文前 4000 字；
    薄壳只读文本，不改条文（条文变更走用户裁决 + arch_law_revision 入册）。
    """
    if not LAW_DOC.exists():
        return {"error": f"铁律文档不存在: {LAW_DOC}"}
    text = LAW_DOC.read_text(encoding="utf-8")
    if section:
        # 粗粒度锚点定位（铁律 N / JN / 章号），精确查询由 ac 侧二次筛
        idx = text.find(section)
        if idx >= 0:
            return {"section": section, "text": text[max(0, idx - 200): idx + 3000]}
        return {"error": f"锚点 '{section}' 未命中", "hint": "试 铁律8/J5/一/二 等"}
    return {"length": len(text), "text": text[:4000]}


@mcp.tool()
def lc_workclaim_query() -> dict:
    """查 work_claim 锁现状（铁律 8 操作域：谁锁着什么、何时到期）。

    薄壳转发 plugins/agents/work_claim.py 的 holder/check_paths 原语，
    让 ac 改 lc 仓前查锁（避免并行修改撞基线漂移，lingxi 5-failed 教训）。
    只读查询，不 bind/release（写操作走 lc 仓 work_claim 守卫，ac 无写权限）。
    """
    try:
        import importlib
        wc = importlib.import_module("lingclaude.plugins.agents.work_claim")
        from lingclaude.core.state_store import StateStore
        store = StateStore(backend="json", root=LC_ROOT / "data" / "work_claims")
        holders = wc.holder(store)  # 当前持锁人列表
        return {"holders": holders, "note": "只读查询；bind/release 走 lc 仓守卫"}
    except Exception as e:  # noqa: BLE001 —— 转发失败结构化（薄壳纪律：不让 ac 猜）
        return {"error": str(e)[:300], "hint": "work_claim 模块未加载，确认 cwd=" + str(LC_ROOT)}


def main() -> None:
    """MCP stdio 入口：python3 server.py（lc 仓根启动）。"""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
