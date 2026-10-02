"""task_contract — 任务级验收契约门（M2 gate 契约化，2026-10-02）。

对标 atomcode gate.py「把对模型美德的依赖变成框架机制」的 stop 契约门：
会话有未清零的验收项时不许悄悄退场——exit 拦一次出报告，显式 force 才放行。

与既有机制的分界（不重复造轮子）：
  - session todo（todo_write 面板）= AI 侧执行任务分解，进程内；
  - success_gate = 单次工具输出成功度判定（Noul 概率门）；
  - 本模块 = 用户验收清单（会话级持久化），管「这轮活儿干完了没、用户认没认」。

状态：~/.lingclaude/contract/active_contract.json（原子写；非密文不 0600——
验收清单无凭据面）。条目 {desc, done, created}；无活跃契约 = 门恒放行。

fail 纪律（对齐 atomcode 铁律）：本模块任何异常 → 门放行——这是任务门
不是安全门，绝不允许因自身故障把用户锁在 REPL 里。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_CONTRACT_FILE = "LC_CONTRACT_FILE"


def contract_file() -> Path:
    p = os.environ.get(ENV_CONTRACT_FILE)
    if p:
        return Path(p)
    return Path.home() / ".lingclaude" / "contract" / "active_contract.json"


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)  # 同目录原子替换


def load_contract() -> list[dict]:
    """读活跃契约条目；无文件/坏文件 → []（门放行语义）。"""
    try:
        raw = contract_file().read_text(encoding="utf-8")
        items = json.loads(raw).get("items", [])
        return items if isinstance(items, list) else []
    except FileNotFoundError:
        return []
    except Exception:  # noqa: BLE001 — 坏文件不锁门
        logger.warning("task_contract: 契约文件损坏，按空契约处理", exc_info=True)
        return []


def save_contract(items: list[dict]) -> None:
    """整体保存（空清单 = 删除活跃契约文件）。"""
    f = contract_file()
    if not items:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass
        return
    _atomic_write(f, {"items": items, "updated": time.time()})


def contract_new(descs: list[str]) -> list[dict]:
    """建新契约（覆盖旧契约——建新=上轮已收口）。"""
    items = [
        {"desc": d, "done": False, "created": time.time()}
        for d in descs
        if isinstance(d, str) and d.strip()
    ]
    save_contract(items)
    return items


def contract_add(desc: str) -> list[dict]:
    items = load_contract()
    if desc.strip():
        items.append({"desc": desc.strip(), "done": False, "created": time.time()})
        save_contract(items)
    return items


def contract_done(index: int) -> bool:
    """按 1-based 序号勾验收项。返回是否真的勾了。"""
    items = load_contract()
    if 1 <= index <= len(items):
        items[index - 1]["done"] = True
        save_contract(items)
        return True
    return False


def contract_clear() -> None:
    save_contract([])


def pending_items() -> list[dict]:
    return [it for it in load_contract() if not it.get("done")]


def render_status() -> str:
    """人读状态（/contract 无参输出）。"""
    items = load_contract()
    if not items:
        return "[contract] 无活跃验收契约（/contract new 项1 项2 ... 建立）"
    lines = ["[contract] 验收清单："]
    for i, it in enumerate(items, 1):
        mark = "x" if it.get("done") else " "
        lines.append(f"  [{mark}] {i}. {it['desc']}")
    pend = pending_items()
    lines.append(
        f"  —— {len(items) - len(pend)}/{len(items)} 已验收"
        + (f"，未清 {len(pend)} 项（/quit 会被拦，/quit force 强制）" if pend else "，全清")
    )
    return "\n".join(lines)


def exit_gate(force: bool = False) -> tuple[bool, str]:
    """退出闸：返回 (blocked, report)。

    blocked=True 表示本次 /quit 应被拦下（报告含处置指引）。
    任何异常 → (False, "")（门故障永远放行，绝不把用户锁在 REPL 里）。
    """
    if force:
        return False, ""
    try:
        pend = pending_items()
        if not pend:
            return False, ""
        lines = [
            f"[contract] 有 {len(pend)} 项验收未清零，退出被拦：",
        ]
        for i, it in enumerate(
            [x for x in load_contract() if not x.get("done")], 1
        ):
            lines.append(f"  - {it['desc']}")
        lines.append("  处置：/contract done <序号> 逐项验收，/contract clear 整单收口，")
        lines.append("        或 /quit force 显式越过本门（契约保留，下次进入仍可见）。")
        return True, "\n".join(lines)
    except Exception:  # noqa: BLE001 — 门故障放行（铁律）
        logger.warning("task_contract: exit_gate 异常，放行", exc_info=True)
        return False, ""
