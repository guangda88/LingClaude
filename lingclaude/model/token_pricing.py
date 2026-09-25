#!/usr/bin/env python3
"""token cost 换算 — 单价表外置口径（token-schema-legacy-path-ingest 清偿②）。

设计（v0.4 立项单 resolve_hint："cost 字段单价表外置配置"）：
- 单价表: LINGCLAUDE_TOKEN_PRICE_TABLE 环境变量指向 JSON 文件，
  结构 {"<模型名子串>": {"input": <USD/1M>, "output": <USD/1M>, "cached": <USD/1M 可选>}}
  命中规则 = 模型名包含该子串，多个命中取最长 key（如 "claude-sonnet-4" 优先于 "claude"）。
- 未配置表 / 模型未登记 → 返回 None，调用方标 cost_status="unpriced"。
  纪律：绝不内置单价、绝不估算——价格随时效性，编造的数字比没有数字更危险
  （同 N6 口径纪律：单口径结论只作线索）。
- best-effort：表文件缺失/损坏按未配置处理（warning 一次/进程）。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ENV_PRICE_TABLE = "LINGCLAUDE_TOKEN_PRICE_TABLE"
_warned_missing = False


def _load_price_table() -> dict[str, dict[str, float]] | None:
    """读外置单价表；未配置/损坏 → None（进程内只告警一次防刷屏）。"""
    global _warned_missing
    path_raw = os.environ.get(_ENV_PRICE_TABLE, "").strip()
    if not path_raw:
        return None
    try:
        raw = json.loads(Path(path_raw).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
        logger.warning("[token_pricing] 单价表顶层须为对象: %s", path_raw)
        return None
    except Exception as e:  # noqa: BLE001 — 外置配置缺失按未定价处理
        if not _warned_missing:
            logger.warning("[token_pricing] 单价表不可读(%s): %s", path_raw, e)
            _warned_missing = True
        return None


def _match_prices(
    table: dict[str, dict[str, float]], model: str
) -> dict[str, float] | None:
    """模型名子串最长命中；无命中 → None。"""
    hits = [k for k in table if k and k in model]
    if not hits:
        return None
    return table[max(hits, key=len)]


def compute_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
) -> float | None:
    """单轮成本（USD）。口径假设 cached ⊆ input（OpenAI 系）：
    非缓存 input = input_tokens - cached_tokens。任何字段缺失/未定价 → None。
    """
    try:
        table = _load_price_table()
        if table is None:
            return None
        prices = _match_prices(table, model or "")
        if prices is None:
            return None
        pin = prices.get("input")
        pout = prices.get("output")
        if pin is None or pout is None:
            return None
        pcached = prices.get("cached", pin)  # 未单列缓存价 → 按非缓存 input 价计（保守）
        non_cached_input = max(0, int(input_tokens) - max(0, int(cached_tokens)))
        cost = (
            non_cached_input * (pin / 1_000_000)
            + max(0, int(cached_tokens)) * (pcached / 1_000_000)
            + max(0, int(output_tokens)) * (pout / 1_000_000)
        )
        return cost
    except Exception:  # noqa: BLE001 — best-effort，成本换算永不破坏主流程
        return None


def pricing_status(model: str) -> str:
    """诊断口：priced / unpriced（表未配置或模型未登记）。"""
    table = _load_price_table()
    if table is None:
        return "unpriced"
    return "priced" if _match_prices(table, model or "") else "unpriced"


def _demo_payload() -> dict[str, Any]:
    """供 doctor/测试展示的最小表样例（不含真实价格数字）。"""
    return {
        "<model-name-substring>": {
            "input": "<USD per 1M tokens>",
            "output": "<USD per 1M tokens>",
            "cached": "<optional, USD per 1M tokens>",
        }
    }
