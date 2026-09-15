"""跨仓依赖单源契约（cross-repo seam）—— 灵元 1.0「变化全变插片 / 主干零隐式依赖」落地。

问题背景（2026-09-15 D6）:
  代码库中 5 处跨仓依赖各写各的:
    - fact_checker.py:30     硬编码 /home/ai/lingzhi + sys.path.insert
    - l10_a_post_audit.py:40 默认相对路径 + env 覆盖(LINGMINOPT_PATH) + sys.path.insert
    - l10_a_post_audit.py:61 importlib 绝对路径加载 lingan/security_gate.py
    - wakeup_channel.py:46   硬编码 /home/ai/lingmessage + sys.path.insert
    - query_engine.py:57-68  硬编码 4 条路径 + sys.path.insert
    - api.py:609             硬编码 /home/ai/lingmessage + sys.path.insert
  每处路径解析/插入逻辑逐字重复, 且硬编码路径 = 隐式依赖, 违背灵元理念:
    - 硬编码路径不可配置 → 部署到非 /home/ai 布局即断
    - sys.path.insert 散落各处 → 无法审计谁注入了什么

本契约将「跨仓仓库路径 + 是否加入 sys.path」统一为单源:
  - repo_path(name):      env 覆盖优先(LING*_PATH), 否则默认 /home/ai/<name>
  - ensure_import_path(): 幂等把仓库路径加入 sys.path, 返回是否本次新增
  - 调用点只写 "我要这个仓库", 不写路径, 不写插入逻辑

对齐 LACP manifest 理念: 插片依赖必须可声明、可审计、可热更。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 灵字辈仓库默认布局（单一事实来源, 可用 env 覆盖）
_REPO_DEFAULT = {
    "lingzhi": "/home/ai/lingzhi",
    "lingmessage": "/home/ai/lingmessage",
    "lingminopt": "/home/ai/lingminopt",
    "lingan": "/home/ai/lingan",
    "lingflow": "/home/ai/lingflow",
    "lingflow_plus": "/home/ai/lingflow_plus",
    "lingyang": "/home/ai/lingyang",
    "lingresearch": "/home/ai/lingresearch",
    "lingtongask": "/home/ai/lingtongask",  # 灵通问道（E12 补充）
    "lingclaude": "/home/ai/lingclaude",  # 本仓（E12 补充：VERSION/env 等路径单源）
    "lingmemory": "/home/ai/lingclaude/lingmemory",  # 本仓子目录(测试薄主干)
    "ling_lib": "/home/ai/.ling_lib",  # 共享工具目录（ling_key_store 等）
}

# env 覆盖键映射（配置外置: 灵元「策略/配置外置热更」）
_REPO_ENV_KEY = {
    "lingzhi": "LINGZHI_PATH",
    "lingmessage": "LINGMESSAGE_PATH",
    "lingminopt": "LINGMINOPT_PATH",
    "lingan": "LINGAN_PATH",  # 目录级覆盖; 文件级加载(security_gate.py)用 LINGAN_SECURITY_GATE_PATH
    "lingflow": "LINGFLOW_PATH",
    "lingflow_plus": "LINGFLOW_PLUS_PATH",
    "lingyang": "LINGYANG_PATH",
    "lingresearch": "LINGRESEARCH_PATH",
    "lingtongask": "LINGTONGASK_PATH",  # 灵通问道（E12）
    "lingclaude": "LINGCLAUDE_PATH",  # 本仓（E12）
    "lingmemory": "LINGMEMORY_PATH",  # 本仓子目录(测试薄主干)
    "ling_lib": "LING_LIB_PATH",
}

# 记录已被 ensure_import_path 加入的仓库名（幂等/审计用）
_IMPORTED: set[str] = set()


def repo_path(name: str) -> Optional[Path]:
    """返回指定跨仓仓库的根路径。env 覆盖优先, 否则默认 /home/ai/<name>。

    Args:
        name: 仓库名（lingzhi / lingmessage / lingminopt / lingan / lingflow /
              lingflow_plus / lingyang / lingresearch / lingmemory / ling_lib）

    Returns:
        Path（已 resolve）或 None（未知仓库名）。
    """
    if name not in _REPO_DEFAULT:
        logger.warning("未知跨仓仓库名: %s", name)
        return None

    env_key = _REPO_ENV_KEY[name]
    env_val = os.environ.get(env_key)
    if env_val:
        return Path(env_val).resolve()

    return Path(_REPO_DEFAULT[name]).resolve()


def ensure_import_path(name: str) -> bool:
    """幂等把仓库路径加入 sys.path。

    加入成功返回 True（本次新增）, 已存在或未知返回 False。
    该函数只做路径登记, 不做 import —— 具体 import 由调用点负责。
    """
    path = repo_path(name)
    if path is None:
        return False

    path_str = str(path)
    if path_str in sys.path:
        return False
    if name in _IMPORTED:
        return False

    sys.path.insert(0, path_str)
    _IMPORTED.add(name)
    logger.debug("cross-repo seam: 已加入 sys.path 仓库 %s -> %s", name, path_str)
    return True


def imported_repos() -> list[str]:
    """审计用: 返回已被 ensure_import_path 登记的仓库名列表。"""
    return sorted(_IMPORTED)


def reset() -> None:
    """测试用: 清空已登记记录（不动 sys.path, 仅清本模块状态）。"""
    _IMPORTED.clear()
