"""Tier 0 env 收窄：子进程环境变量凭证过滤（中心 helper）。

威胁模型（P1b/T1，2026-10-02 实测）：
  lc 主进程 env 含大量凭证（provider keys / proxy3 token / MCP tokens，
  单机实测 59 个 *KEY*/*TOKEN*/*SECRET* 型变量）。所有子进程
  （bash 持久 shell / 外部 agent / MCP server）默认整继承——
  任一子进程被注入或失控，即等于全部凭证泄漏。
  vault（P1b-V1）只管静态存储，管不了 env 继承这条活通道。

方案：deny-list——spawn 前剥离凭证模式变量，其余全保留。
  不用 allow-list（bash 是任意命令通道，白名单缺变量会以千奇百怪的方式挂，
  且 SSH_AUTH_SOCK 等非凭证但必要的变量无法枚举穷尽）。
  显式需要凭证的子进程（外部 agent 调 proxy3）用 allow 参数点名加回——
  最小暴露面，且在调用点可审计。

用法：
    from lingclaude.engine.env_guard import filter_env
    env = filter_env(os.environ)                          # bash / 通用子进程
    env = filter_env(os.environ, allow=("ANTHROPIC_AUTH_TOKEN",
                                        "ANTHROPIC_BASE_URL"))  # 外部 agent
"""
from __future__ import annotations

import os
from collections.abc import Mapping

# 凭证模式（对变量名做大写子串匹配）。剥这些，其余保留。
# 已知真实凭证名全部命中：*_API_KEY / *_AUTH_TOKEN / *_SECRET /
# *_PASSWORD / LINGCLAUDE_CREDENTIAL_POOL_KEYS / LC_VAULT_KEY …
_CREDENTIAL_PATTERNS: tuple[str, ...] = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH",
)

# 命中模式但内容是路径/socket 而非密钥的良性变量（剥了会断功能）。
# 原则：例外名单只收「值为路径」的，永不收「值为密钥」的。
_BENIGN_EXCEPTIONS: frozenset[str] = frozenset({
    "SSH_AUTH_SOCK",       # ssh-agent socket 路径（剥了断 git ssh 认证）
    "SSH_AGENT_PID",
    "GNOME_KEYRING_CONTROL",  # keyring socket 目录
    "GPG_AGENT_INFO",      # legacy gpg-agent socket 描述
})


def is_credential_var(name: str) -> bool:
    """判定变量名是否命中凭证模式（良性例外除外）。"""
    if name in _BENIGN_EXCEPTIONS:
        return False
    upper = name.upper()
    return any(p in upper for p in _CREDENTIAL_PATTERNS)


def filter_env(
    env: Mapping[str, str] | None = None,
    allow: tuple[str, ...] = (),
) -> dict[str, str]:
    """返回剥掉凭证型变量后的 env 副本（不修改入参）。

    allow: 显式放行名单（精确名，大小写敏感）——用于确需凭证的子进程
    （如外部 agent 的 proxy3 认证三件套）。allow 赢过 deny。
    """
    if env is None:
        env = os.environ
    allow_set = set(allow)
    return {
        k: v
        for k, v in env.items()
        if allow_set and k in allow_set or not is_credential_var(k)
    }
