"""~/.ling_keys.env 单点读取 + mtime 热同步 — 修「key 轮换后运行中进程读不到新 KEY」。

背景（2026-10-12 诊断，哈希级取证）：
- task_router.py:148 明言「key 实际单点存放于 ~/.ling_keys.env」，但运行时取 key 链
  只查 os.environ（进程启动时的快照）→ 长驻进程只要活过一次 key 轮换就必然读旧 KEY
  → 401。实锤：10-11 19:19 轮换的 VOLC_CODING_API_KEY/MINIMAX_API_KEY 新值已进
  ~/.ling_keys.env，但 daemon watch（9-25 启动）等长驻进程 env 仍是旧值，探活每轮
  「路由剔除 http=401」。
- 原 _load_keys_env「只在变量未设时注入」→ shell rc 内联 export 的旧值永远挡住
  文件新值（.bashrc L83/99/100 内联 export 即此类）。

机制（三层语义）：
1. load() 启动注入：按文件值写入 os.environ（覆盖写 = 「文件=单点真源」）。
   LINGCLAUDE_KEYS_PREFER_ENV=1 退回旧行为「未设才注入」（测试隔离/用户显式覆盖用）。
2. sync_if_changed() 热同步：keys 文件 mtime 变化才重读；只更新「env 当前值 ==
   本模块上次注入值」的变量——自己注入的旧值自己刷新，用户/测试显式设的值一律不碰。
   LINGCLAUDE_KEYS_LIVE=0 关闭（PREFER_ENV=1 时同样关闭，完全冻结旧行为）。
3. 注入记录 _INJECTED 保证与 patch.dict(os.environ, {...}) 类测试隔离共存
   （tests/test_task_router.py:461 的「宿主 cloud key 经兜底激活 provider」防护不破）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

DEFAULT_KEYS_PATH = Path.home() / ".ling_keys.env"

# 环境开关
PREFER_ENV_VAR = "LINGCLAUDE_KEYS_PREFER_ENV"  # =1 → 旧行为（env 显式优先，不覆盖）
LIVE_DISABLE_VAR = "LINGCLAUDE_KEYS_LIVE"      # =0 → 关闭热同步

# 本模块注入过的 {变量名: 注入值} —— sync 只更新这些「自己注入」的值
_INJECTED: dict[str, str] = {}

# mtime 节流：热路径不重复 stat/读文件
_last_mtime: float | None = None
_last_stat_at: float = 0.0
_STAT_INTERVAL_SEC = 2.0


def _prefer_env() -> bool:
    return os.environ.get(PREFER_ENV_VAR, "").strip() in ("1", "true", "yes")


def _live_enabled() -> bool:
    if _prefer_env():
        return False
    return os.environ.get(LIVE_DISABLE_VAR, "").strip() not in ("0", "false", "no")


def parse_keys_file(path: Path = DEFAULT_KEYS_PATH) -> dict[str, str]:
    """解析 keys 文件（`export NAME=value` / `NAME=value`，值去引号）。"""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        name, sep, value = line.partition("=")
        if not sep:
            continue
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and value:
            out[name] = value
    return out


def load(path: Path = DEFAULT_KEYS_PATH) -> int:
    """启动注入。默认文件值覆盖写；PREFER_ENV=1 时未设才注入（旧行为）。

    返回实际写入 os.environ 的变量数。所有写入都记录进 _INJECTED，
    供 sync_if_changed 识别「自己注入的值」。
    """
    global _last_mtime, _last_stat_at
    values = parse_keys_file(path)
    prefer_env = _prefer_env()
    written = 0
    for name, value in values.items():
        if prefer_env and name in os.environ:
            continue  # 旧行为：不覆盖用户 shell 已 export 的值
        if os.environ.get(name) != value:
            os.environ[name] = value
            written += 1
        _INJECTED[name] = value
    try:
        _last_mtime = path.stat().st_mtime
    except OSError:
        _last_mtime = None
    _last_stat_at = time.monotonic()
    return written


def sync_if_changed(force: bool = False, path: Path = DEFAULT_KEYS_PATH) -> int:
    """热同步：keys 文件 mtime 变化 → 把新值刷进 os.environ。

    只更新 env 当前值 == _INJECTED[name] 的变量（自己注入的旧值自己刷新）；
    用户/测试显式设过的值（不等于注入值）一律跳过。返回更新的变量数。
    """
    global _last_mtime, _last_stat_at
    if not _live_enabled() and not force:
        return 0
    now = time.monotonic()
    if not force and _last_stat_at and (now - _last_stat_at) < _STAT_INTERVAL_SEC:
        return 0
    _last_stat_at = now
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return 0
    if not force and _last_mtime is not None and mtime == _last_mtime:
        return 0
    _last_mtime = mtime
    values = parse_keys_file(path)
    updated = 0
    for name, value in values.items():
        if name not in _INJECTED:
            continue  # 不是我们注入的 → 不碰（尊重显式/测试隔离）
        if os.environ.get(name) != _INJECTED[name]:
            continue  # env 值被显式改过 → 不碰
        if _INJECTED[name] == value:
            continue  # 值没变
        os.environ[name] = value
        _INJECTED[name] = value
        updated += 1
    return updated


def injected_snapshot() -> dict[str, str]:
    """测试可观测点：本模块注入过的变量快照（拷贝）。"""
    return dict(_INJECTED)
