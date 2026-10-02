"""pool_vault_loader 单测（池句柄化 2026-10-02）。

覆盖面：
- parse_pool_spec：正常/空/非法片段/冒号缺分/空 key 片段（唯一解析器契约）
- from_spec 与 from_env 解析一致性（同一 raw → 同一池内容）
- 装配顺序：env 显式 > vault 条目 > 空串（显式覆盖语义）
- vault 锁着（无根密钥）→ load_pool_spec 空串，build_pool 空池，不 raise
- vault 命中 → build_pool 出多账号池（next_key 可取）
- factory._pool_get_key 惰性装配走 vault 通道（end-to-end）

hermetic：HOME→tmp_path、无 LC_VAULT_KEY、无池 env；测试键 64-hex 非真实。
"""
from __future__ import annotations

import os

import pytest

from lingclaude.model import pool_vault_loader as pvl
from lingclaude.model.credential_pool import CredentialPool
from lingclaude.model.vault import ENV_KEY_NAME, Vault

ROOT_KEY = "b" * 64  # 64-hex 合法测试键（非真实）
POOL_SPEC = "glm:sk-test-1,sk-test-2;kimi:sk-k1"


@pytest.fixture()
def pool_env(tmp_path, monkeypatch):
    """隔离：HOME→tmp、无池 env、无 vault 根密钥、独立 vault db。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(pvl.ENV_POOL_NAME, raising=False)
    monkeypatch.delenv(ENV_KEY_NAME, raising=False)
    monkeypatch.delenv("LC_VAULT_DB", raising=False)
    monkeypatch.delenv("LC_VAULT_KEY_FILE", raising=False)
    return tmp_path


# ── parse_pool_spec（唯一解析器）─────────────────────────────────

def test_parse_normal():
    pools = pvl.parse_pool_spec(POOL_SPEC)
    assert pools == {"glm": ["sk-test-1", "sk-test-2"], "kimi": ["sk-k1"]}


def test_parse_empty_and_garbage():
    assert pvl.parse_pool_spec("") == {}
    assert pvl.parse_pool_spec("   ") == {}
    assert pvl.parse_pool_spec("no-colon-segment;glm:k") == {"glm": ["k"]}
    assert pvl.parse_pool_spec("glm:,") == {}  # 有 provider 无 key → 跳过


# ── from_spec / from_env 单一解析器一致性 ─────────────────────────

def test_from_spec_builds_pool():
    pool = CredentialPool.from_spec(POOL_SPEC)
    assert pool.next_key("glm") in ("sk-test-1", "sk-test-2")
    assert pool.next_key("kimi") == "sk-k1"


def test_from_env_delegates_same_parser(pool_env, monkeypatch):
    monkeypatch.setenv(pvl.ENV_POOL_NAME, POOL_SPEC)
    a = CredentialPool.from_env()
    b = CredentialPool.from_spec(os.environ[pvl.ENV_POOL_NAME])
    assert sorted(a.providers()) == sorted(b.providers()) == ["glm", "kimi"]


# ── 装配顺序 env > vault > 空串 ──────────────────────────────────

def test_env_explicit_overrides_vault(pool_env, monkeypatch):
    # vault 里先放一份
    os.environ[ENV_KEY_NAME] = ROOT_KEY
    try:
        Vault().set(pvl.VAULT_POOL_NAME, "vaultprov:sk-from-vault")
    finally:
        del os.environ[ENV_KEY_NAME]
    # env 也设一份 → env 赢
    monkeypatch.setenv(pvl.ENV_POOL_NAME, "envprov:sk-from-env")

    pool = pvl.build_pool()
    assert pool.next_key("envprov") == "sk-from-env"
    assert pool.next_key("vaultprov") is None  # vault 条目被显式覆盖遮蔽


def test_vault_hit_when_env_absent(pool_env, monkeypatch):
    # 根密钥落 0600 文件链（而非 env），模拟「写入时用 env、消费时靠文件」
    from lingclaude.model.vault import _atomic_write_0600, default_key_file_path

    _atomic_write_0600(default_key_file_path(), ROOT_KEY + "\n")
    Vault().set(pvl.VAULT_POOL_NAME, POOL_SPEC)
    assert ENV_KEY_NAME not in os.environ  # 前置：env 键确实不在

    pool = pvl.build_pool()
    assert pool.next_key("glm") in ("sk-test-1", "sk-test-2")
    assert pool.next_key("kimi") == "sk-k1"


def test_vault_locked_returns_empty_pool(pool_env):
    """根密钥三链全空：load_pool_spec 空串、build_pool 空池、绝不 raise。"""
    assert pvl.load_pool_spec() == ""
    pool = pvl.build_pool()
    assert pool.providers() == []
    assert pool.next_key("glm") is None


# ── factory 惰性装配走 vault 通道（end-to-end）───────────────────

def test_factory_pool_get_key_via_vault(pool_env, monkeypatch):
    import lingclaude.model.factory as factory

    monkeypatch.setattr(factory, "_CREDENTIAL_POOL", None)  # 强制重建
    from lingclaude.model.vault import _atomic_write_0600, default_key_file_path

    _atomic_write_0600(default_key_file_path(), ROOT_KEY + "\n")  # 0600 文件链供读
    Vault().set(pvl.VAULT_POOL_NAME, "facprov:sk-fac-1")

    got = factory._pool_get_key("facprov")
    assert got == "sk-fac-1"
    factory._pool_record_exhausted("facprov", "sk-fac-1")  # 上报不炸
    # 下一次取号跳过冷却账号（单账号池宁撞墙不空池 → 仍返回它）
    assert factory._pool_get_key("facprov") == "sk-fac-1"
    monkeypatch.setattr(factory, "_CREDENTIAL_POOL", None)  # 还原，防泄漏


# ── vault 条目为空/缺失 → 空池落回原链 ───────────────────────────

def test_vault_missing_entry(pool_env):
    os.environ[ENV_KEY_NAME] = ROOT_KEY
    try:
        Vault().set(pvl.VAULT_POOL_NAME, "")
    except ValueError:
        pass  # vault.set 拒绝空值 — 条目根本不存在，等价
    finally:
        del os.environ[ENV_KEY_NAME]
    assert pvl.load_pool_spec() == ""
