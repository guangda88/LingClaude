"""MCP 客户端公共原语（plugins/agents 层共享接缝，P2-1 整改产物）。

审计整改（2026-09-18，lc 审查发现清单 P2-1）：lc_mcp_guard/plugin.py 曾函数内
import agent_lingxi.plugin 的 _health_key/_domain_of/_extract_response——插片 A
import 插片 B 内部 = 隐性横向耦合（灵犀一改 lc-guard 就断，M3 只查纵向不拦）。
整改：把两个 MCP/Agent 插片共享的原语抽到本模块（公共接缝），各自引用，互不依赖。

停层声明（铁律 2 细则 5）：
- kernel：无状态纯函数（缝 key 域解析 / StateStore key 安全化 / JSON-RPC 响应提取）；
- 子插片接缝：无（纯函数集，无可变实现）；
- 消费方：agent_lingxi、lc_mcp_guard（后续 MCP 型插片一律引此处，禁引他插片内部）。

铁律锚点：
- 铁律 1：本模块在 plugins/agents/，core/ 零 diff；
- J1：变化走接缝——插片间共享走显式公共模块，不走插片内部 import；
- J5：行为不变（函数体自 agent_lingxi 原样平移，lingxi 测试继续锚定同语义）。
"""
from __future__ import annotations

import json


def domain_of(seam_key: str) -> str:
    """铁律 7 缝 key 的域 = 域前缀（'agent/lingxi' → 'agent'）。"""
    return seam_key.split("/", 1)[0] if "/" in seam_key else seam_key


def health_key(seam_key: str) -> str:
    """缝 key 是带 '/' 的域前缀 key，StateStore 存取 key 需文件名安全化。"""
    return seam_key.replace("/", "__")


def extract_response(out: str, req_id: int) -> dict | None:
    """从 stdio 多行 JSON-RPC 输出中提取指定 id 的响应行。

    J5 行为级：锚定响应中 id==req_id 的行（协议嵌套语义），非"首行/进程存活"
    ——initialize 回包同样含 result，不能误判为成功（lingxi 根因 4 教训）。
    """
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
