"""灵元 R2: PolicyLoader —— 统一 YAML 策略加载器 + mtime watch 热更。

P1-1 (2026-09-14, 以灵元 1.0 为尺):
  - 把 4 处各自为政的 `_load_policy()`（behavior_aware_router /
    intelligent_router / l10_a_post_audit / wiring._load_manifest_yaml）
    收敛为统一加载器
  - 复用 model_call.py 已有 mtime 轮询雏形，抽成统一 PolicyLoader
  - 真热重载 = reload data，不是 reload module（灵元哲学）

设计:
  - PolicyLoader.load(name) → dict：按名加载 lingclaude/core/policies/<name>.yaml
    - 读失败（缺失/解析错误）返回 {}，调用方回退内置默认（graceful degrade）
  - PolicyLoader.get(name) → 缓存 + mtime watch：
    - 首次调用加载并缓存
    - 之后调用检查 mtime（节流 _WATCH_INTERVAL 秒），文件变了才重读
    - 返回语义不变：dict（调用方按 .get() 缺省回退，零破坏）
  - PolicyLoader.hot_update() → bool：主动触发一次检查（供 wiring 装配前调用）
  - PolicyLoader.policies_dir() → Path：策略目录（测试可 monkeypatch）

与现有 4 处消费方的迁移契约:
  - 返回值都是 dict，.get() 语义与现状完全一致
  - 新增策略 = policies/ 下加一个 YAML，不新增代码
  - 改策略 = 改 YAML 文件，不重启进程，下个调用生效（mtime watch）

参考: docs/theory/LINGMATE_HOTRELOAD_PATH_v1.md
      docs/theory/LINGYUAN_AUDIT_LINGCLAUDE_v2.md 阶段2
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 策略目录：lingclaude/core/policies/
_POLICIES_DIR = Path(__file__).parent / "policies"

# mtime watch 节流（秒）：复用 model_call._HOT_RELOAD_INTERVAL 同款量级，
# 避免每轮读盘；策略文件变化最迟一个节流周期内生效。
_WATCH_INTERVAL = 30.0

# 文件 mtime 缓存：{绝对路径: st_mtime}。None 表示加载失败（不缓存失败，
# 下次调用重试 —— graceful degrade 语义：文件补上后自动恢复）。
_MTIME_CACHE: dict[str, float] = {}

# 数据缓存：{绝对路径: dict}
_DATA_CACHE: dict[str, dict[str, Any]] = {}


def policies_dir() -> Path:
    """策略目录（测试可 monkeypatch _POLICIES_DIR 覆盖）。"""
    return _POLICIES_DIR


def _resolve_path(name: str) -> Path | None:
    """策略名 → 绝对路径。防目录穿越：只允许 policies/ 下的 <name>.yaml。"""
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        logger.warning("PolicyLoader: 非法策略名 %r（拒绝目录穿越）", name)
        return None
    path = (_POLICIES_DIR / f"{name}.yaml").resolve()
    # 双保险：解析后必须仍在 policies 目录内
    base = _POLICIES_DIR.resolve()
    if not str(path).startswith(str(base) + os.sep):
        logger.warning("PolicyLoader: 策略路径越界 %s（拒绝）", path)
        return None
    return path


def _read_yaml(path: Path) -> dict[str, Any]:
    """读 YAML 文件 → dict。读失败返回 {}（调用方回退内置默认）。"""
    try:
        import yaml

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            logger.warning("PolicyLoader: %s 顶层不是 dict，按空策略处理", path)
            return {}
        return data
    except Exception as exc:  # noqa: BLE001 — 策略加载失败绝不影响主流程
        logger.warning("PolicyLoader: 读取 %s 失败: %s", path, exc)
        return {}


def _load(name: str) -> dict[str, Any]:
    """强制重读一次（不做缓存判断），返回 dict。"""
    path = _resolve_path(name)
    if path is None:
        return {}
    if not path.is_file():
        logger.debug("PolicyLoader: %s 不存在（回退调用方默认）", path)
        return {}
    data = _read_yaml(path)
    if data:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        _MTIME_CACHE[str(path)] = mtime
        _DATA_CACHE[str(path)] = data
    else:
        # 读失败：清缓存，下次重试
        _MTIME_CACHE.pop(str(path), None)
        _DATA_CACHE.pop(str(path), None)
    return data


def _changed(path: Path) -> bool:
    """mtime 是否变化（文件新增/修改都算变化）。"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    return _MTIME_CACHE.get(str(path)) != mtime


def get(name: str) -> dict[str, Any]:
    """取策略数据：缓存 + mtime watch 热更。

    首次调用加载并缓存；之后每次调用检查 mtime（节流），文件变了自动重读。
    返回 dict，读失败/缺失返回 {} —— 调用方 .get() 缺省回退，零破坏。
    """
    path = _resolve_path(name)
    if path is None:
        return {}
    key = str(path)

    # 首次加载
    if key not in _MTIME_CACHE:
        return _load(name)

    # 节流检查 mtime（与 model_call._maybe_hot_reload_config 同款节流）
    now = time.monotonic()
    if now - _LAST_CHECK[0] < _WATCH_INTERVAL:
        return _DATA_CACHE.get(key, {})
    _LAST_CHECK[0] = now

    if _changed(path):
        logger.info("PolicyLoader: 策略文件变更 %s，热重载", path)
        return _load(name)
    return _DATA_CACHE.get(key, {})


def load(name: str) -> dict[str, Any]:
    """强制重读（绕过缓存），返回 dict。测试/热更用。"""
    return _load(name)


def hot_update() -> bool:
    """主动触发一次热更检查：遍历已缓存策略，mtime 变了就重载。

    返回是否有任何策略被重载。供 wiring 装配前调用，让 manifest YAML
    修改在下次装配生效（P1-2 接线点）。
    """
    reloaded = False
    for key in list(_MTIME_CACHE.keys()):
        path = Path(key)
        if _changed(path):
            logger.info("PolicyLoader: hot_update 命中 %s", path)
            _load(Path(path).name.removesuffix(".yaml"))
            reloaded = True
    return reloaded


def reset() -> None:
    """清空全部缓存（仅测试用）。"""
    _MTIME_CACHE.clear()
    _DATA_CACHE.clear()


# 模块级最后检查节流（get 用）
_LAST_CHECK = [0.0]
