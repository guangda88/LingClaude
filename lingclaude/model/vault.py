"""lc 凭证 Vault — SQLite 明文 schema + 字段级 AES-256-GCM（P1b 定稿 2026-10-01）。

方案（atomcode 复审定稿）：
- 存储：普通 SQLite，表 secrets(name TEXT PRIMARY KEY, nonce BLOB,
  ciphertext BLOB, updated_at REAL)。明文元数据（名字/时间）可审计可
  grep——排查"哪个 key 过期了"不用解密。
- 密码学：cryptography 库 AES-256-GCM（12B random nonce/条）。
  **不自造密码学**（XOR+KDF 之类在审计视角直接打回）。
- 根密钥锚点（递归问题的诚实回答）：根密钥必须盘外来源，否则任何加密
  都是自我循环。本模块查找链：
      1) env LC_VAULT_KEY（64-hex；显式覆盖，最高优先）
      2) keyring 后端（可选，V3：Secret Service 托管，本机未装自动跳过）
      3) 0600 文件 ~/.lingclaude/vault.key（init 默认落点）
  三链全空 → VaultLockedError，拒绝半残运行。

威胁模型边界（必须诚实，写进文档而不是藏进注释）：
  本 vault 防「盘被拷走/备份泄漏/误提交后密文可读」「同机其他用户读文件」
  「粗心 agent 扫盘读明文」；**不防 root、不防同账号恶意进程读内存/读
  已解锁的根密钥文件**。要真正闭合只有用户每次输主密码派生密钥（KDF，
  lc 无人值守场景不现实）或硬件锚点（超出单机工具范畴）。加密目标是
  降低备份/误提交/横向泄漏面，不是对抗本机 root——把边界说清楚，
  比假装闭合了重要。

弱口令防线：根密钥只接受 64-hex（secrets.token_hex(32) 产物）。
  init 自动生成；env 注入短口令/非 hex 直接拒绝——用户拿 hunter2 当
  根密钥时，AES-GCM 也救不了，所以入口就拦。不做 KDF 口令派生环节。

nonce 纪律：12B random per-encrypt（os.urandom）。同根密钥下 2^32 次
  加密才进入碰撞感知区；secrets 库百级条目、写频极低，余量充足。
  **勿将本模块挪作高频加密用途**（那需要重构为计数器 nonce 方案）。

进程内纪律：根密钥只在内存；get() 返回明文给调用方；本模块所有日志
  只出现条目名，永不出现明文/密文（防 traceback/日志泄密）。

env 兼容：查找条目名对齐既有 env 变量名（如 DEEPSEEK_API_KEY），
  gen_env.py 迁移时同名搬入即可，消费方零改动。
"""
from __future__ import annotations

import os
import secrets as _secrets
import sqlite3
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

__all__ = [
    "VaultLockedError",
    "Vault",
    "vault_init",
    "default_db_path",
    "default_key_file_path",
]

ENV_KEY_NAME = "LC_VAULT_KEY"
_KEY_HEX_LEN = 64          # 32B = AES-256
_NONCE_LEN = 12            # GCM 标准 96-bit
_SCHEMA = """
CREATE TABLE IF NOT EXISTS secrets (
    name        TEXT PRIMARY KEY,
    nonce       BLOB NOT NULL,
    ciphertext  BLOB NOT NULL,
    updated_at  REAL NOT NULL
)
"""


class VaultLockedError(RuntimeError):
    """根密钥三链全空（env/keyring/0600 文件）。带修复指引。"""


def default_db_path() -> Path:
    p = Path(os.environ.get("LC_VAULT_DB", str(Path.home() / ".lingclaude" / "vault.db")))
    return p.expanduser()


def default_key_file_path() -> Path:
    p = Path(
        os.environ.get("LC_VAULT_KEY_FILE", str(Path.home() / ".lingclaude" / "vault.key"))
    )
    return p.expanduser()


def _validate_root_key(raw: str) -> bytes:
    """64-hex 校验（弱口令防线：短口令/非 hex 直接拒绝）。"""
    raw = raw.strip()
    if len(raw) != _KEY_HEX_LEN:
        raise ValueError(
            f"LC_VAULT_KEY 必须是 {_KEY_HEX_LEN} 位 hex（secrets.token_hex(32) 产物），"
            f"当前长度 {len(raw)}。拒绝弱口令作根密钥。"
        )
    try:
        return bytes.fromhex(raw)
    except ValueError as e:
        raise ValueError("LC_VAULT_KEY 含非 hex 字符，拒绝。") from e


def _keyring_load() -> str:
    """V3 可选后端：根密钥经 Secret Service（D-Bus）托管。缺失/故障 → 空串跳过。

    依赖链：keyring 包（若装，走它）→ secretstorage 直连（本机实测可用，
    jeepney 总线）→ 空。有桌面环境的机器白拿一层登录会话隔离；纯 SSH/
    无 Secret Service 机器常态空串，降级链自动落 0600 文件（V1 主路径，
    行为不变）。服务名 lingclaude-vault、账号 root。

    威胁边界提醒（对齐模块 docstring）：Secret Service 保护的是「其他
    登录会话」读不到，**同会话内的 agent 进程读得到**——它不解决
    「agent 互读」威胁，只是让根密钥不落盘文件而已。
    """
    try:
        import keyring  # 可选依赖：装了优先走它（多后端兼容）

        got = keyring.get_password("lingclaude-vault", "root")
        if got:
            return str(got)
    except Exception:  # noqa: BLE001 — 可选后端任何故障不阻断主链
        pass
    try:
        import secretstorage

        conn = secretstorage.dbus_init()
        coll = secretstorage.get_default_collection(conn)
        if coll.is_locked():
            return ""  # 锁着的 keyring 不解锁（无人值守无交互），降级文件链
        for item in coll.search_items(
            {"service": "lingclaude-vault", "username": "root"}
        ):
            return str(item.get_secret().decode("utf-8"))
        return ""
    except Exception:  # noqa: BLE001
        return ""


def _keyring_save(root_hex: str) -> bool:
    """V3：根密钥存入 Secret Service。成功 True；无后端/失败 False（调用方落文件）。"""
    try:
        import keyring

        keyring.set_password("lingclaude-vault", "root", root_hex)
        return True
    except Exception:  # noqa: BLE001
        pass
    try:
        import secretstorage

        conn = secretstorage.dbus_init()
        coll = secretstorage.get_default_collection(conn)
        if coll.is_locked():
            return False
        # 同 service+username 只留一条：先删旧再存新
        for item in coll.search_items(
            {"service": "lingclaude-vault", "username": "root"}
        ):
            item.delete()
        coll.create_item(
            "lingclaude-vault:root",
            {"service": "lingclaude-vault", "username": "root"},
            root_hex.encode("utf-8"),
            replace=True,
        )
        return True
    except Exception:  # noqa: BLE001
        return False


# 2026-10-06 启动优化：进程级缓存根密钥。TaskRouter 对每个 provider
# 各 new 一个 Vault()，未缓存时每次 _load_root_key 都重走 keyring 后端
# 探测链（importlib.metadata 扫全机 entry_points + D-Bus 握手），lc 冷
# 启动被放大到 10s+（实测 keyring 探测单次 0.5~4s × 10 次）。进程内
# 语义不变——env/keyring/文件三链查找顺序、失败异常类型全保持，只是
# 同一进程内只解析一次。测试需改 env 时用 reset_root_key_cache()。
_ROOT_KEY_CACHE: bytes | None = None


def reset_root_key_cache() -> None:
    """清进程级根密钥缓存（测试/密钥轮换用，生产路径不调用）。"""
    global _ROOT_KEY_CACHE
    _ROOT_KEY_CACHE = None


def _load_root_key_uncached() -> bytes:
    """根密钥三链：env → keyring(可选) → 0600 文件。全空 → VaultLockedError。"""
    env_raw = os.environ.get(ENV_KEY_NAME, "")
    if env_raw.strip():
        return _validate_root_key(env_raw)

    try:
        kr_raw = _keyring_load()  # 边界契约：正常实现内部容错返回空串；
    except Exception:  # noqa: BLE001 — 纵深防御：实现若被换且会 raise，也降级不炸
        kr_raw = ""
    if kr_raw.strip():
        return _validate_root_key(kr_raw)

    key_file = default_key_file_path()
    if key_file.exists():
        try:
            return _validate_root_key(key_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise VaultLockedError(
                f"根密钥文件 {key_file} 存在但不可用：{e}。"
                f"修复：rm {key_file} 后重新 `python -m lingclaude.model.vault init`。"
            ) from e

    raise VaultLockedError(
        "vault 未解锁：三链全空（env LC_VAULT_KEY / keyring / "
        f"{default_key_file_path()}）。执行 `python -m lingclaude.model.vault init` "
        "生成根密钥，或 export LC_VAULT_KEY=<64-hex>。"
    )


def _load_root_key() -> bytes:
    """进程级缓存包装：同进程第二次起零 keyring/D-Bus/文件 I/O。"""
    global _ROOT_KEY_CACHE
    if _ROOT_KEY_CACHE is None:
        _ROOT_KEY_CACHE = _load_root_key_uncached()
    return _ROOT_KEY_CACHE


def _atomic_write_0600(path: Path, text: str) -> None:
    """原子写 + 权限先行（同 openrouter_oauth.save_key 的 fail-closed 模式）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    tmp = path.parent / f"{path.name}.tmp{os.getpid()}"
    tmp.write_text(text, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)


class Vault:
    """字段级加密 KV。用法：Vault().get("DEEPSEEK_API_KEY")。"""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db = Path(db_path) if db_path else default_db_path()
        self._aes: AESGCM | None = None  # 惰性：构造不解锁（/help、list 场景免 key）

    # -- 内部 -------------------------------------------------------------
    def _key(self) -> bytes:
        """惰性解锁：首次用到才装配 AESGCM（构造/list 场景免根密钥）。"""
        if self._aes is None:
            self._aes = AESGCM(_load_root_key())
        return self._aes

    def _conn(self) -> sqlite3.Connection:
        if self._db.parent and not self._db.parent.exists():
            self._db.parent.mkdir(parents=True, exist_ok=True)  # 全新部署首写即建目录
        first = not self._db.exists()
        conn = sqlite3.connect(str(self._db), timeout=5.0)
        conn.execute(_SCHEMA)
        conn.commit()
        if first:
            try:
                self._db.chmod(0o600)  # 纵深：密文库也收权限
            except OSError:
                pass
        return conn

    # -- 公开 API ----------------------------------------------------------
    def set(self, name: str, value: str) -> None:
        """写入/覆盖一条（加密落盘；同名覆盖，nonce 每次新随机）。"""
        if not value:
            raise ValueError("vault.set 拒绝空值（删除用 delete）。")
        aes = self._key()
        nonce = os.urandom(_NONCE_LEN)
        ct = aes.encrypt(nonce, value.encode("utf-8"), None)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO secrets(name, nonce, ciphertext, updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                "nonce=excluded.nonce, ciphertext=excluded.ciphertext, "
                "updated_at=excluded.updated_at",
                (name, nonce, ct, time.time()),
            )
            conn.commit()

    def get(self, name: str) -> str | None:
        """取明文；无此条/根密钥不符/密文被篡改 → None（调用方降级，不炸）。"""
        try:
            aes = self._key()
        except VaultLockedError:
            return None  # vault 锁着 = 没配 = 调用方走既有 env 链
        with self._conn() as conn:
            row = conn.execute(
                "SELECT nonce, ciphertext FROM secrets WHERE name=?", (name,)
            ).fetchone()
        if not row:
            return None
        try:
            return aes.decrypt(row[0], row[1], None).decode("utf-8")
        except InvalidTag:
            # 根密钥换了或密文被篡改：绝不能返回部分数据，也不 raise
            # （消费方在 key 兜底链里，raise 会打断装配）——log + None。
            import logging

            logging.getLogger(__name__).warning(
                "vault.get(%s) 解密失败（根密钥不匹配或密文被篡改）", name
            )
            return None

    def delete(self, name: str) -> bool:
        """删除条目。返回是否真的删了。"""
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM secrets WHERE name=?", (name,))
            conn.commit()
            return cur.rowcount > 0

    def list(self) -> list[dict]:
        """列条目元数据（名字/更新时间）——不解密，vault 锁着也能看。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT name, updated_at FROM secrets ORDER BY updated_at DESC"
            ).fetchall()
        return [{"name": r[0], "updated_at": r[1]} for r in rows]


def vault_init(db_path: Path | str | None = None) -> Path:
    """生成根密钥并初始化 vault。

    env 已有合法 LC_VAULT_KEY → 直接用（不落文件，env 优先语义）。
    否则生成 32B 随机 → 0600 写 ~/.lingclaude/vault.key，并提示 env 备选。
    返回根密钥实际落点（文件）或 None 的语义用 Path|None 表达。
    """
    env_raw = os.environ.get(ENV_KEY_NAME, "")
    if env_raw.strip():
        _validate_root_key(env_raw)  # 借库前先验，坏 key 立刻报
        v = Vault(db_path)
        v._conn().close()  # 建库建表
        return default_key_file_path()  # 未落文件；调用方以 exists() 判别

    root = _secrets.token_hex(32)
    # V3: Secret Service 可用 → 托管根密钥（盘上零文件）；失败/无后端 → 0600 文件
    if _keyring_save(root):
        v = Vault(db_path)
        v._conn().close()
        return default_key_file_path()  # 未落文件（exists()=False 判别）
    key_file = default_key_file_path()
    _atomic_write_0600(key_file, root + "\n")
    v = Vault(db_path)
    v._conn().close()
    return key_file


if __name__ == "__main__":  # python -m lingclaude.model.vault init|list
    import sys

    if len(sys.argv) >= 2 and sys.argv[1] == "init":
        where = vault_init()
        print(
            f"vault 已初始化。根密钥落点：{where if where.exists() else '(未落盘, env 注入)'}\n"
            f"备选：export {ENV_KEY_NAME}=$(cat {default_key_file_path()})"
        )
    elif len(sys.argv) >= 2 and sys.argv[1] == "list":
        for it in Vault().list():
            print(f"{it['name']:40s} updated={time.strftime('%Y-%m-%d %H:%M', time.localtime(it['updated_at']))}")
    else:
        print("用法: python -m lingclaude.model.vault init|list")
