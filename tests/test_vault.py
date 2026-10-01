"""P1b vault 单测（V1 核心 + V2 接线契约）。

覆盖面（对应定稿方案四要素）：
- 加解密 roundtrip + 字段级密文落库（明文不进盘）
- 根密钥三链（env → keyring-mock → 0600 文件）+ 全空 VaultLockedError
- 弱口令防线（短口令/非 hex 拒绝）
- 解密失败契约（InvalidTag → None + 告警，不 raise 不返部分数据）
- vault 锁着时 get→None / list 仍可用
- set 空值拒绝 / delete rowcount 语义 / list 元数据排序
- V2 接线：env 先 vault 后（显式覆盖语义）+ vault 命中
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

from lingclaude.model import vault as vault_mod
from lingclaude.model.vault import (
    ENV_KEY_NAME,
    Vault,
    VaultLockedError,
    _validate_root_key,
    default_db_path,
    vault_init,
)

ROOT_KEY = "a" * 64  # 合法 64-hex（测试用，非真实密钥）


@pytest.fixture()
def vault_env(tmp_path, monkeypatch):
    """隔离环境：HOME 指向 tmp、无 LC_VAULT_KEY、独立 db/key 文件。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(ENV_KEY_NAME, raising=False)
    monkeypatch.delenv("LC_VAULT_DB", raising=False)
    monkeypatch.delenv("LC_VAULT_KEY_FILE", raising=False)
    monkeypatch.setattr(vault_mod, "_keyring_load", lambda: "")  # 本机未装 keyring，显式置空防环境漂移
    return tmp_path


# ── V1 核心 ──────────────────────────────────────────────────────────

class TestRoundtrip:
    def test_set_get_roundtrip(self, vault_env, monkeypatch):
        monkeypatch.setenv(ENV_KEY_NAME, ROOT_KEY)
        v = Vault(vault_env / "v.db")
        v.set("DEEPSEEK_API_KEY", "sk-test-123")
        assert v.get("DEEPSEEK_API_KEY") == "sk-test-123"

    def test_ciphertext_on_disk_not_plaintext(self, vault_env, monkeypatch):
        """字段级加密的核心断言：盘上文件不含明文。"""
        monkeypatch.setenv(ENV_KEY_NAME, ROOT_KEY)
        db = vault_env / "v.db"
        v = Vault(db)
        secret = "sk-SUPER-SECRET-VALUE-42"
        v.set("MY_KEY", secret)
        blob = db.read_bytes()
        assert secret.encode() not in blob
        # 明文元数据可审计：名字在盘上可 grep（方案优点，非缺陷）
        assert b"MY_KEY" in blob

    def test_nonce_rotates_per_set(self, vault_env, monkeypatch):
        monkeypatch.setenv(ENV_KEY_NAME, ROOT_KEY)
        db = vault_env / "v.db"
        v = Vault(db)
        v.set("K", "same-value")
        n1 = sqlite3.connect(str(db)).execute(
            "SELECT nonce FROM secrets WHERE name='K'").fetchone()[0]
        v.set("K", "same-value")
        n2 = sqlite3.connect(str(db)).execute(
            "SELECT nonce FROM secrets WHERE name='K'").fetchone()[0]
        assert n1 != n2  # 同值两次写 nonce 必须不同


class TestRootKeyChains:
    def test_env_chain_highest(self, vault_env, tmp_path, monkeypatch):
        """env 优先于文件（显式覆盖语义，新实例=新解锁时刻）。"""
        key_file = vault_env / ".lingclaude" / "vault.key"
        key_file.parent.mkdir(parents=True)
        key_file.write_text("b" * 64 + "\n")
        monkeypatch.setenv(ENV_KEY_NAME, ROOT_KEY)
        v = Vault(vault_env / "v.db")
        v.set("K", "with-env-key")
        # 拿文件 key 解不开 env key 加的密 → get 返回 None 而非串数据
        monkeypatch.delenv(ENV_KEY_NAME)
        v2 = Vault(vault_env / "v.db")  # 新实例走文件链解锁
        assert v2.get("K") is None

    def test_key_file_chain(self, vault_env):
        key_file = vault_init(vault_env / "v.db")
        assert key_file.exists()
        assert oct(key_file.stat().st_mode)[-3:] == "600"
        v = Vault(vault_env / "v.db")
        v.set("K", "file-chain-value")
        assert v.get("K") == "file-chain-value"

    def test_keyring_chain_mock(self, vault_env, monkeypatch):
        monkeypatch.setattr(vault_mod, "_keyring_load", lambda: "c" * 64)
        v = Vault(vault_env / "v.db")
        v.set("K", "keyring-chain")
        assert v.get("K") == "keyring-chain"

    def test_all_chains_empty_locked(self, vault_env):
        v = Vault(vault_env / "v.db")
        with pytest.raises(VaultLockedError):
            v.set("K", "v")
        # 锁着时 get 是降级不是炸（调用方走既有 env 链）
        assert v.get("K") is None
        # list 不需要根密钥（明文元数据设计）
        assert v.list() == []

    def test_corrupt_key_file_guidance(self, vault_env):
        key_file = vault_env / ".lingclaude" / "vault.key"
        key_file.parent.mkdir(parents=True)
        key_file.write_text("short-broken")
        v = Vault(vault_env / "v.db")
        with pytest.raises(VaultLockedError, match="rm.*init"):
            v.set("K", "v")


class TestWeakKeyDefense:
    def test_short_key_rejected(self):
        with pytest.raises(ValueError, match="64"):
            _validate_root_key("hunter2")

    def test_non_hex_rejected(self):
        with pytest.raises(ValueError, match="hex"):
            _validate_root_key("g" * 64)

    def test_env_weak_key_rejected_on_set(self, vault_env, monkeypatch):
        monkeypatch.setenv(ENV_KEY_NAME, "hunter2")
        v = Vault(vault_env / "v.db")
        with pytest.raises(ValueError):
            v.set("K", "v")


class TestContractEdge:
    def test_set_empty_rejected(self, vault_env, monkeypatch):
        monkeypatch.setenv(ENV_KEY_NAME, ROOT_KEY)
        v = Vault(vault_env / "v.db")
        with pytest.raises(ValueError):
            v.set("K", "")

    def test_invalid_tag_returns_none_not_raise(self, vault_env, monkeypatch):
        """根密钥更换后旧密文 → None + 告警（装配链里 raise 会打断启动）。"""
        monkeypatch.setenv(ENV_KEY_NAME, ROOT_KEY)
        db = vault_env / "v.db"
        v = Vault(db)
        v.set("K", "old-key-data")
        monkeypatch.setenv(ENV_KEY_NAME, "f" * 64)  # 换根密钥
        v2 = Vault(db)  # 新实例 = 新解锁时刻（同实例 _aes 缓存是进程内正确语义）
        assert v2.get("K") is None

    def test_delete_rowcount_semantics(self, vault_env, monkeypatch):
        monkeypatch.setenv(ENV_KEY_NAME, ROOT_KEY)
        v = Vault(vault_env / "v.db")
        assert v.delete("NOPE") is False
        v.set("K", "v")
        assert v.delete("K") is True
        assert v.get("K") is None

    def test_list_metadata_sorted(self, vault_env, monkeypatch):
        monkeypatch.setenv(ENV_KEY_NAME, ROOT_KEY)
        v = Vault(vault_env / "v.db")
        v.set("A_KEY", "1")
        time.sleep(0.01)
        v.set("B_KEY", "2")
        names = [it["name"] for it in v.list()]
        assert names == ["B_KEY", "A_KEY"]  # 最新在前


# ── V2 接线：task_router._resolve_api_key 的 env 先 vault 后 ─────────

class TestResolveWiring:
    @pytest.fixture()
    def router_mod(self, vault_env, monkeypatch):
        monkeypatch.setenv(ENV_KEY_NAME, ROOT_KEY)
        # Vault() 默认路径 = HOME/.lingclaude/vault.db（HOME 已被 fixture patch 到 tmp）
        # 与 task_router._resolve_api_key 内部 Vault() 同库，接线测试才有效。
        monkeypatch.delenv("LC_VAULT_DB", raising=False)
        v = Vault()  # 默认路径实例
        v.set("DEEPSEEK_API_KEY", "sk-from-vault")
        from lingclaude.model import task_router as tr
        return tr

    def test_env_overrides_vault(self, router_mod, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
        got = router_mod._resolve_api_key("deepseek", "")
        assert got == "sk-from-env"

    def test_vault_used_when_env_absent(self, router_mod, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        got = router_mod._resolve_api_key("deepseek", "")
        assert got == "sk-from-vault"

    def test_vault_locked_falls_back_empty(self, router_mod, monkeypatch, vault_env):
        """vault 未解锁（无 env/key 文件）→ 空串，既有行为零分叉。"""
        monkeypatch.delenv(ENV_KEY_NAME, raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        # HOME 已被 vault_env 指到 tmp → 默认 key 文件也不存在
        got = router_mod._resolve_api_key("deepseek", "")
        assert got == ""

    def test_config_key_still_wins_over_everything(self, router_mod, monkeypatch):
        """显式 config key（非 ${} 形式）原样优先——向后兼容红线。"""
        got = router_mod._resolve_api_key("deepseek", "sk-explicit")
        assert got == "sk-explicit"


# ── V3：keyring/Secret Service 可选后端 ──────────────────────────────

class TestKeyringBackend:
    def test_load_order_keyring_before_file(self, vault_env, monkeypatch):
        """keyring 链排在文件链前：keyring 有值时不读文件。"""
        key_file = vault_env / ".lingclaude" / "vault.key"
        key_file.parent.mkdir(parents=True)
        key_file.write_text("b" * 64 + "\n")
        monkeypatch.setattr(vault_mod, "_keyring_load", lambda: "e" * 64)
        v = Vault(vault_env / "v.db")
        v.set("K", "under-keyring-key")
        monkeypatch.setattr(vault_mod, "_keyring_load", lambda: "")  # 模拟 keyring 清空
        v2 = Vault(vault_env / "v.db")
        assert v2.get("K") is None  # 文件 key 解不开 keyring key 的密

    def test_keyring_failure_degrades_to_file(self, vault_env, monkeypatch):
        def boom():
            raise RuntimeError("dbus down")

        # hermetic：save/load 都 mock，不碰真 D-Bus（真机上 login collection
        # 存在与否会让分支翻转）。契约：实现层内部容错返回空串，此处故意
        # 用会 raise 的 mock 验证调用点的纵深防御。
        monkeypatch.setattr(vault_mod, "_keyring_load", boom)
        monkeypatch.setattr(vault_mod, "_keyring_save", lambda h: False)
        key_file = vault_init(vault_env / "v.db")
        assert key_file.exists()  # Secret Service 挂 → 落 0600 文件，不瘫痪
        v = Vault(vault_env / "v.db")
        v.set("K", "degraded-file-chain")
        assert v.get("K") == "degraded-file-chain"

    def test_init_prefers_keyring_no_file(self, vault_env, monkeypatch):
        """init 时 Secret Service 可用 → 根密钥托管，盘上无 key 文件。"""
        saved = {}
        monkeypatch.setattr(vault_mod, "_keyring_save", lambda h: saved.update(k=h) or True)
        where = vault_init(vault_env / "v.db")
        assert not where.exists()  # 未落文件
        assert len(saved["k"]) == 64
        # 加载链走 keyring 能解锁
        monkeypatch.setattr(vault_mod, "_keyring_load", lambda: saved["k"])
        v = Vault(vault_env / "v.db")
        v.set("K", "keyring-stored-root")
        assert v.get("K") == "keyring-stored-root"


# ── V2 补充：factory 兜底链 vault 接线 ───────────────────────────────

class TestFactoryVaultWiring:
    def test_factory_keystore_falls_back_to_vault(self, vault_env, monkeypatch):
        """ling_lib key store 异常（跨仓不可用）→ vault 兜底。"""
        from lingclaude.model import factory
        monkeypatch.setenv(ENV_KEY_NAME, ROOT_KEY)
        v = Vault()
        v.set("OPENAI_API_KEY", "factory-vault-value")
        # 模拟 ling_lib 链不可用：ensure_import_path 抛 ImportError
        import lingclaude.lacp.cross_repo_seam as seam
        monkeypatch.setattr(
            seam, "ensure_import_path",
            lambda *a, **k: (_ for _ in ()).throw(ImportError("no ling_lib in test")),
        )
        got = factory._key_store_get("OPENAI_API_KEY")
        assert got == "factory-vault-value"

    def test_factory_vault_locked_returns_empty(self, vault_env, monkeypatch):
        from lingclaude.model import factory
        monkeypatch.delenv(ENV_KEY_NAME, raising=False)
        got = factory._key_store_get("SOME_KEY")
        assert got == ""  # 锁着 → 空串零分叉
