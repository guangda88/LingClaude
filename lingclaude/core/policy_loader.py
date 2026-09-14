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

# 文件 mtime 缓存：{绝对路径: (st_mtime_ns, st_size)}。None 表示加载失败（不缓存失败，
# 下次调用重试 —— graceful degrade 语义：文件补上后自动恢复）。
# P7 (2026-09-14): 从 st_mtime（秒级）升级为 (st_mtime_ns, st_size) 组合判据。
#   实测：ext4 mtime_ns 实际粒度为 jiffy（~4ms@250Hz），同 jiffy 内快速连续写入
#   mtime_ns 仍相同；size 作为第二判据可捕获同 jiffy 内不同长度的改写。
#   同长度改写（内容变、长度不变）依赖 hot_update() 内容比较兜底（bd64a11）。
_MTIME_CACHE: dict[str, tuple[int, int]] = {}

# 数据缓存：{绝对路径: dict}
_DATA_CACHE: dict[str, dict[str, Any]] = {}

# 每策略最后检查时刻（节流按路径独立，避免跨策略/跨测试全局互踩）：
# {绝对路径: monotonic 时间戳}
_LAST_CHECK_BY_PATH: dict[str, float] = {}


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
            st = path.stat()
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = (0, 0)
        _MTIME_CACHE[str(path)] = stamp
        _DATA_CACHE[str(path)] = data
    else:
        # 读失败：清缓存，下次重试
        _MTIME_CACHE.pop(str(path), None)
        _DATA_CACHE.pop(str(path), None)
    return data


def _changed(path: Path) -> bool:
    """文件是否变化（新增/修改都算变化）。

    P7: 用 (st_mtime_ns, st_size) 组合判据 ——
      - st_mtime_ns：纳秒级，比秒级 st_mtime 精确得多
      - st_size：第二判据，捕获同 jiffy（ext4 mtime 实际粒度 ~4ms）内
        快速连续写入中长度不同的改写（mtime_ns 相同但 size 不同）
      同长度改写（内容变、长度不变）由 hot_update() 内容比较兜底（bd64a11）。
    """
    try:
        st = path.stat()
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        return False
    return _MTIME_CACHE.get(str(path)) != stamp


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

    # 节流检查 mtime（按路径独立节流：跨策略/跨测试不互踩）
    now = time.monotonic()
    if now - _LAST_CHECK_BY_PATH.get(key, 0.0) < _WATCH_INTERVAL:
        return _DATA_CACHE.get(key, {})
    _LAST_CHECK_BY_PATH[key] = now

    if _changed(path):
        logger.info("PolicyLoader: 策略文件变更 %s，热重载", path)
        return _load(name)
    return _DATA_CACHE.get(key, {})


def load(name: str) -> dict[str, Any]:
    """强制重读（绕过缓存），返回 dict。测试/热更用。"""
    return _load(name)


def hot_update() -> bool:
    """主动触发热更：重读所有已缓存策略并更新缓存。

    返回是否有策略数据发生变化。语义：调用方明确要求刷新（wiring 装配前 /
    测试热更），**不依赖 mtime 判定** —— 快速连续写入（<1ms 粒度）mtime
    可能相同，漏检会让热更不可靠；主动刷新直接重读，变更即捕获。
    """
    reloaded = False
    for key in list(_MTIME_CACHE.keys()):
        path = Path(key)
        if not path.is_file():
            _MTIME_CACHE.pop(key, None)
            _DATA_CACHE.pop(key, None)
            continue
        new_data = _read_yaml(path)
        old_data = _DATA_CACHE.get(key)
        if new_data:
            try:
                st = path.stat()
                stamp = (st.st_mtime_ns, st.st_size)
            except OSError:
                stamp = (0, 0)
            _MTIME_CACHE[key] = stamp
            _DATA_CACHE[key] = new_data
            _LAST_CHECK_BY_PATH.pop(key, None)
            if new_data != old_data:
                logger.info("PolicyLoader: hot_update 刷新 %s（内容变化）", path)
                reloaded = True
        else:
            _MTIME_CACHE.pop(key, None)
            _DATA_CACHE.pop(key, None)
    return reloaded


def reset() -> None:
    """清空全部缓存（仅测试用）。"""
    _MTIME_CACHE.clear()
    _DATA_CACHE.clear()
    _LAST_CHECK_BY_PATH.clear()
