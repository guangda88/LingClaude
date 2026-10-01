"""Tier 0 env 收窄（P1b/T1）——env_guard 单元测试（hermetic，不碰真实 env）。"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from lingclaude.engine.env_guard import filter_env, is_credential_var


class TestIsCredentialVar:
    def test_hits_known_real_names(self) -> None:
        # 全部命中：本机真实存在过的凭证变量名形态
        for name in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "OPENAI_API_KEY",
            "LC_PROXY_SHARED_SECRET",
            "LINGCLAUDE_CREDENTIAL_POOL_KEYS",
            "LC_VAULT_KEY",
            "MINIMAX_GROUP_ID",  # 含 ID 不命中？——不含模式词，应不命中
            "DATABASE_PASSWORD",
            "AWS_SECRET_ACCESS_KEY",
        ):
            if name == "MINIMAX_GROUP_ID":
                assert not is_credential_var(name)
            else:
                assert is_credential_var(name), name

    def test_benign_exceptions(self) -> None:
        # 路径型变量保留（剥了断 ssh/gpg 功能）
        for name in ("SSH_AUTH_SOCK", "SSH_AGENT_PID", "GPG_AGENT_INFO",
                     "GNOME_KEYRING_CONTROL"):
            assert not is_credential_var(name), name

    def test_non_credential_names_pass(self) -> None:
        for name in ("PATH", "HOME", "LANG", "TERM", "SHELL", "USER",
                     "LC_ALL", "EDITOR", "MINIMAX_GROUP_ID"):
            assert not is_credential_var(name), name


class TestFilterEnv:
    def test_strips_credentials_keeps_rest(self) -> None:
        src = {
            "PATH": "/usr/bin",
            "HOME": "/home/ai",
            "MY_API_KEY": "sk-123",
            "AUTH_TOKEN_X": "t",
        }
        out = filter_env(src)
        assert out == {"PATH": "/usr/bin", "HOME": "/home/ai"}

    def test_allow_overrides_deny(self) -> None:
        src = {"MY_API_KEY": "sk-123", "PATH": "/bin"}
        out = filter_env(src, allow=("MY_API_KEY",))
        assert out == {"MY_API_KEY": "sk-123", "PATH": "/bin"}

    def test_benign_vars_survive(self) -> None:
        src = {"SSH_AUTH_SOCK": "/run/user/1000/keyring/ssh", "SECRET_X": "s"}
        out = filter_env(src)
        assert "SSH_AUTH_SOCK" in out
        assert "SECRET_X" not in out

    def test_input_not_mutated(self) -> None:
        src = {"A_KEY": "v", "PATH": "/bin"}
        filter_env(src)
        assert src == {"A_KEY": "v", "PATH": "/bin"}

    def test_none_uses_os_environ(self) -> None:
        # 真实 env 上跑：输出必须严格小于等于输入集合
        out = filter_env()
        assert set(out) <= set(os.environ)
        leaked = [k for k in out if is_credential_var(k)]
        assert leaked == []

    def test_e2e_subprocess_env_isolation(self) -> None:
        """端到端：过滤后的 env 传给子进程，凭证不可见，良性变量可见。"""
        env = filter_env({
            "PATH": os.environ.get("PATH", "/usr/bin"),
            "LEAKY_TOKEN": "supersecret",
            "SAFE_VAR": "hello",
        })
        r = subprocess.run(
            [sys.executable, "-c",
             "import os,sys;sys.exit(0 if 'LEAKY_TOKEN' in os.environ else 1)"],
            env=env, capture_output=True, text=True,
        )
        assert r.returncode != 0  # LEAKY_TOKEN 不可见
        r2 = subprocess.run(
            [sys.executable, "-c",
             "import os,sys;sys.exit(0 if os.environ.get('SAFE_VAR')=='hello' else 1)"],
            env=env, capture_output=True, text=True,
        )
        assert r2.returncode == 0  # SAFE_VAR 正常透传


class TestWiring:
    def test_bash_session_wired(self) -> None:
        """bash 持久 shell 与 MCP client 源码已接 filter_env（防回归锚）。"""
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "lingclaude" / "engine"
        for fname in ("bash_session.py", "mcp_client.py", "bash.py"):
            src = (root / fname).read_text(encoding="utf-8")
            assert "filter_env()" in src or "filter_env(" in src, fname
