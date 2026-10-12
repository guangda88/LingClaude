"""keys_env 回归测试 — 修「昨天新轮换的 KEY 没有读到」(2026-10-12)。

场景锁定:
1. load() 默认文件值覆盖注入(旧 env 快照挡不住新 KEY)
2. load() LINGCLAUDE_KEYS_PREFER_ENV=1 退回「未设才注入」旧行为
3. sync_if_changed() 文件轮换后热刷新「自己注入过」的值(长驻进程不重启生效)
4. 显式/测试改过的 env 值一律不碰(与 test_task_router.py:461 隔离语义共存)
5. _resolve_api_key 集成:旧快照 + 文件新值 → sync 后取到新值(401 修复点)
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from lingclaude.model import keys_env


@pytest.fixture(autouse=True)
def _reset_keys_env_state():
    """模块级注入记录/mtime 节流跨用例隔离。"""
    keys_env._INJECTED.clear()
    keys_env._last_mtime = None
    keys_env._last_stat_at = 0.0
    yield
    keys_env._INJECTED.clear()
    keys_env._last_mtime = None
    keys_env._last_stat_at = 0.0


def _write_keys(tmp_path: Path, **kv: str) -> Path:
    p = tmp_path / "keys.env"
    lines = [f"export {k}={v}" for k, v in kv.items()]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_load_overrides_stale_env_snapshot(tmp_path, monkeypatch):
    """默认语义:文件值覆盖注入 —— 轮换后的新 KEY 打败旧 env 快照。"""
    monkeypatch.delenv(keys_env.PREFER_ENV_VAR, raising=False)
    p = _write_keys(tmp_path, ROT_KEY_A="new-value-2026", ROT_KEY_B="b-new")
    with patch.dict(os.environ, {"ROT_KEY_A": "old-value-2025"}):
        written = keys_env.load(path=p)
        assert os.environ["ROT_KEY_A"] == "new-value-2026"  # 旧快照被覆盖
        assert os.environ["ROT_KEY_B"] == "b-new"
        assert written >= 2


def test_load_prefer_env_keeps_old_behavior(tmp_path, monkeypatch):
    """LINGCLAUDE_KEYS_PREFER_ENV=1:未设才注入(旧行为,测试隔离用)。"""
    monkeypatch.setenv(keys_env.PREFER_ENV_VAR, "1")
    p = _write_keys(tmp_path, ROT_KEY_A="file-value", ROT_KEY_C="c-file")
    with patch.dict(os.environ, {"ROT_KEY_A": "env-wins"}):
        keys_env.load(path=p)
        assert os.environ["ROT_KEY_A"] == "env-wins"  # 显式 env 不被覆盖
        assert os.environ["ROT_KEY_C"] == "c-file"    # 未设的注入


def test_sync_refreshes_only_injected_values(tmp_path, monkeypatch):
    """热同步:文件轮换 → 刷新自己注入的旧值;显式改过的值不碰。"""
    monkeypatch.delenv(keys_env.PREFER_ENV_VAR, raising=False)
    monkeypatch.delenv(keys_env.LIVE_DISABLE_VAR, raising=False)
    p = _write_keys(tmp_path, ROT_KEY_A="v1", ROT_KEY_D="d-v1")
    with patch.dict(os.environ):
        keys_env.load(path=p)
        assert os.environ["ROT_KEY_A"] == "v1"

        # 显式改过 ROT_KEY_D(模拟测试/用户 override)
        os.environ["ROT_KEY_D"] = "explicit-override"
        os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 10))
        p.write_text("export ROT_KEY_A=v2-rotated\nexport ROT_KEY_D=d-v2\n",
                     encoding="utf-8")
        os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 20))

        updated = keys_env.sync_if_changed(force=True, path=p)
        assert updated == 1
        assert os.environ["ROT_KEY_A"] == "v2-rotated"        # 自己注入的 → 刷新
        assert os.environ["ROT_KEY_D"] == "explicit-override"  # 显式值 → 不碰


def test_sync_skips_untracked_names(tmp_path, monkeypatch):
    """未注入过的变量即使 env 里有值也不碰。"""
    monkeypatch.delenv(keys_env.PREFER_ENV_VAR, raising=False)
    monkeypatch.delenv(keys_env.LIVE_DISABLE_VAR, raising=False)
    p = _write_keys(tmp_path, ROT_KEY_E="file-e")
    with patch.dict(os.environ, {"ROT_KEY_E": "untouched"}):
        keys_env.load(path=p)  # PREFER_ENV 关 → load 覆盖注入并记录
        assert os.environ["ROT_KEY_E"] == "file-e"
        # 模拟另一个名字从未注入:直接清记录后 sync
        keys_env._INJECTED.clear()
        os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 10))
        p.write_text("export ROT_KEY_E=file-e2\n", encoding="utf-8")
        keys_env.sync_if_changed(force=True, path=p)
        assert os.environ["ROT_KEY_E"] == "file-e"  # 无记录 → 不碰


def test_resolve_api_key_picks_up_rotated_key(tmp_path, monkeypatch):
    """集成:旧 env 快照 + keys 文件新值 → _resolve_api_key 热同步后取新值。

    即 10-11 VOLC/MINIMAX 轮换 401 的最小复现与锁死。
    """
    from lingclaude.model import task_router

    monkeypatch.delenv(keys_env.PREFER_ENV_VAR, raising=False)
    monkeypatch.delenv(keys_env.LIVE_DISABLE_VAR, raising=False)
    p = _write_keys(tmp_path, VOLC_CODING_API_KEY="ark-rotated-new")
    with patch.dict(os.environ, {"VOLC_CODING_API_KEY": "ark-stale-old"}):
        keys_env.load(path=p)  # 注入新值并记录
        os.environ["VOLC_CODING_API_KEY"] = "ark-stale-old"  # 复现旧快照
        keys_env._INJECTED["VOLC_CODING_API_KEY"] = "ark-stale-old"

        # 轮换:文件换新值(真实轮换时 load 记录的是旧注入值)
        p.write_text("export VOLC_CODING_API_KEY=ark-rotated-new\n",
                     encoding="utf-8")
        keys_env._INJECTED["VOLC_CODING_API_KEY"] = "ark-stale-old"
        # 强制走 sync(patched path 经 monkeypatch 默认 keys 路径不可达 →
        # 直接 sync 到 tmp 文件后断言 env 已新,再验证 _resolve_api_key 取到)
        keys_env.sync_if_changed(force=True, path=p)
        assert os.environ["VOLC_CODING_API_KEY"] == "ark-rotated-new"
        got = task_router._resolve_api_key("volcengine", "")
        assert got == "ark-rotated-new"
