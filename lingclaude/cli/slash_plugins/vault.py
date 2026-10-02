"""斜杠命令插件：/vault —— 凭证库状态与生命周期管理（池句柄化配套 2026-10-02）。

语义边界（有意收窄）：
  /vault          — 状态查看：db 路径/条目数/根密钥三链哪条在供
  /vault init     — 生成 64-hex 根密钥 → 写 0600 key 文件 → 建库
  /vault delete NAME — 删条目（池规格键名见 model/pool_vault_loader）
  故意不提供 /vault set：命令行传明文会进 REPL 历史/台账，等于新开泄漏面。
  写入路径：vault_init 后经 python API（Vault().set）或池规格一次性灌入。
"""
from __future__ import annotations


def vault_cmd(processor, arg: str = "") -> None:
    from lingclaude.model.vault import (
        ENV_KEY_NAME,
        Vault,
        default_db_path,
        default_key_file_path,
        vault_init,
    )

    sub = arg.strip().split()
    if sub and sub[0] == "init":
        path = vault_init()
        print(f"[vault] 根密钥已生成 → {default_key_file_path()}（0600）")
        print(f"[vault] 库已就绪 → {path}")
        print("[vault] 写入示例：python3 -c \"from lingclaude.model.vault import Vault; "
              "Vault().set('credential_pool_keys', 'provider:key1,key2')\"")
        return
    if sub and sub[0] == "delete":
        if len(sub) < 2:
            print("[/vault] 用法：/vault delete NAME")
            return
        ok = Vault().delete(sub[1])
        print(f"[vault] {sub[1]}: {'已删除' if ok else '条目不存在'}")
        return
    if sub:
        print("[/vault] 用法：/vault（状态）｜/vault init｜/vault delete NAME")
        return

    # 状态视图
    import os

    db = default_db_path()
    entries = Vault().list()
    key_file = default_key_file_path()
    chains = []
    if os.environ.get(ENV_KEY_NAME):
        chains.append("env(LC_VAULT_KEY)")
    if key_file.exists():
        chains.append(f"key文件({key_file.name})")
    try:
        import keyring  # noqa: F401

        chains.append("keyring(可选)")
    except Exception:  # noqa: BLE001
        pass
    print(f"[vault] db: {db}（{'存在' if db.exists() else '未创建'}）")
    print(f"[vault] 条目: {len(entries)}")
    for e in entries[:10]:
        print(f"  - {e['name']}  (updated {int(e['updated_at'])})")
    print(f"[vault] 根密钥链候选: {', '.join(chains) if chains else '全空（锁着）'}")


def register(add) -> None:
    """loader 契约：add(name, fn, desc, aliases=(), needs_args=, arg_hint=)。"""
    add(
        "/vault", vault_cmd,
        "凭证库：/vault 状态｜/vault init 生成根密钥建库｜/vault delete NAME",
    )
