"""credential_pool 装配链 vault 化（P1b 二期「池句柄化」，2026-10-02）。

背景（P1b 一期遗留挂账）：多账号池仍走 LINGCLAUDE_CREDENTIAL_POOL_KEYS env
通道——59 个明文 key 出 env 的口子池子占一份。本模块把池装配接上 vault，
env 通道降级为显式覆盖（向后兼容，CI/无 vault 部署零分叉）。

存储格式：vault 单键 ``credential_pool_keys``，值与 env 同格式
``provider:key1,key2;other:key3``（分号分 provider，逗号分账号）——
单一格式两种来源，消除「env 一套 vault 一套」的格式漂移。

装配顺序（factory._pool_get_key 惰性装配点消费）：
    env 显式值  >  vault 条目  >  空串（=空池，落回既有 env/key_store 链）

fail-open 契约：vault 锁着 / 条目缺失 / 解密失败 → 空串，绝不 raise——
本函数在 key 兜底链的装配路径上，与 vault.get(None) 契约同构。
"""

from __future__ import annotations

import os

VAULT_POOL_NAME = "credential_pool_keys"
ENV_POOL_NAME = "LINGCLAUDE_CREDENTIAL_POOL_KEYS"


def parse_pool_spec(raw: str) -> dict[str, list[str]]:
    """解析池规格字符串（env 与 vault 共用唯一解析器，防双格式漂移）。

    格式：``provider:key1,key2;other:key3``。非法片段跳过不 raise。
    """
    pools: dict[str, list[str]] = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        provider, _, keys = part.partition(":")
        api_keys = [k.strip() for k in keys.split(",") if k.strip()]
        if provider.strip() and api_keys:
            pools[provider.strip()] = api_keys
    return pools


def load_pool_spec(vault=None) -> str:
    """返回池规格原文：env 显式值优先，否则 vault 条目，全无则空串。

    vault 参数缺省时惰性构造（list 场景免根密钥；get 在锁定时返回 None）。
    """
    env_raw = os.environ.get(ENV_POOL_NAME, "").strip()
    if env_raw:
        return env_raw  # 显式覆盖：优先级最高，向后兼容零破坏
    try:
        if vault is None:
            from lingclaude.model.vault import Vault

            vault = Vault()
        return (vault.get(VAULT_POOL_NAME) or "").strip()
    except Exception:  # noqa: BLE001 — vault 不可用 ≠ 装配失败，空串降级
        return ""


def build_pool(vault=None):
    """按装配顺序构造 CredentialPool（env > vault > 空池）。

    返回类型保持 CredentialPool（空池语义：next_key → None，调用方
    落回 env/key_store 链，行为与 P1-7 接线时零分叉）。
    """
    from lingclaude.model.credential_pool import CredentialPool

    spec = load_pool_spec(vault)
    return CredentialPool.from_spec(spec)
