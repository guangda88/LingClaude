"""L7 引擎插片契约 — 灵元 1.0「显式插片契约」落地。

问题背景（2026-09-15 D5）:
  l7_cognitive.py 原先用 `Path(__file__).parent.../lingminopt/lingyuan` 硬编码
  相对路径探测 L7 存储引擎，并 `sys.path.insert` 污染运行时路径 ——
  违背灵元 1.0「变化全变插片 / 主干零 import 插片 / 显式契约」三原则:
    - 硬编码路径 = 隐式依赖，不是可声明的插片接口
    - sys.path.insert = 隐式 import，不是显式契约
    - 探测失败静默降级 = 无来源可查

本契约将「L7 引擎在哪里、如何加载」变为显式注册：
  - register_l7_engine(): 外部（lingminopt 侧）注册真实实现
  - load_l7_engine(): 主干侧读取，未注册返回 None（降级安全，零副作用）
  - auto_discover_l7(): 可选探测（env 覆盖路径），默认不调用

对齐 LACP manifest 理念：插片必须有可声明接口才能热替换。
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 契约：store/retrieve 的函数签名（与 l7_memory.py 对齐）
StoreFn = Callable[..., Any]
RetrieveFn = Callable[..., Any]


@dataclass
class L7EngineHandle:
    """L7 引擎句柄：显式注册的插片实现。"""

    store: StoreFn
    retrieve: RetrieveFn
    source: str  # 注册来源（如 "lingminopt:registered" / "env:auto"）


# 模块级注册表（单例，进程内显式契约）
_ENGINE: Optional[L7EngineHandle] = None

# env 覆盖键（配置外置：灵元理念「策略/配置外置热更」）
ENV_L7_PATH = "LINGYUAN_L7_PATH"


def register_l7_engine(
    store: StoreFn,
    retrieve: RetrieveFn,
    *,
    source: str = "registered",
) -> None:
    """显式注册 L7 引擎插片。外部（lingminopt）在 import 时调用。"""
    global _ENGINE
    _ENGINE = L7EngineHandle(store=store, retrieve=retrieve, source=source)
    logger.info("L7 引擎插片已注册（source=%s）", source)


def load_l7_engine() -> Optional[L7EngineHandle]:
    """主干侧读取 L7 引擎。未注册返回 None（降级安全，零副作用）。"""
    return _ENGINE


def unregister_l7_engine() -> None:
    """卸载 L7 引擎插片（测试/热替换用）。"""
    global _ENGINE
    _ENGINE = None


def auto_discover_l7() -> Optional[L7EngineHandle]:
    """可选探测：仅当显式调用时，尝试从 env 路径或默认相对路径加载。

    默认不自动调用 —— 避免 sys.path 污染与隐式依赖。
    优先 env 覆盖（LINGYUAN_L7_PATH），其次默认相对路径。
    """
    candidates: list[tuple[str, str]] = []

    env_path = os.environ.get(ENV_L7_PATH)
    if env_path:
        candidates.append((env_path, "env"))

    # 默认相对路径（保持向后兼容，但仅在此函数显式调用时生效）
    default = str(
        Path(__file__).resolve().parent.parent.parent.parent / "lingminopt" / "lingyuan"
    )
    candidates.append((default, "default"))

    for path_str, source in candidates:
        try:
            path = Path(path_str)
            if not path.is_dir():
                continue
            if str(path) not in sys.path:
                sys.path.insert(0, str(path))
            from l7_memory import retrieve as l7_retrieve
            from l7_memory import store as l7_store

            handle = L7EngineHandle(
                store=l7_store, retrieve=l7_retrieve, source=f"auto:{source}"
            )
            logger.info("L7 引擎自动发现成功（source=%s, path=%s）", source, path)
            return handle
        except Exception as exc:  # noqa: BLE001 — 探测失败继续下一候选
            logger.debug("L7 引擎探测失败（source=%s）: %s", source, exc)
            continue

    logger.debug("L7 引擎自动发现未命中（%d 候选）", len(candidates))
    return None
