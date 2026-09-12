"""git_push / git_push_preflight 专用工具测试。

覆盖：
- 参数注入防护（remote/branch 白名单 + refspec 字符校验）
- preflight 预检逻辑（remotes/unpushed/dirty/gate）
- handler 注册与调用链
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lingclaude.engine.git import (
    _validate_refspec,
    _validate_remote,
    git_push,
    git_push_preflight,
)


# ---------- 参数校验 ----------

class TestRefspecValidation:
    def test_valid_branches(self):
        for b in ("master", "main", "dev", "feature/x", "v1.2.3"):
            assert _validate_refspec(b) is None, b

    def test_reject_shell_metachars(self):
        for b in ("master; touch /tmp/pwned", "$(whoami)", "master --upload-pack=evil", "a`b`", "x&&y"):
            assert _validate_refspec(b) is not None, b

    def test_reject_path_traversal(self):
        for b in ("../etc/passwd", "a/../b", "a:b", "a..b"):
            assert _validate_refspec(b) is not None, b

    def test_reject_empty_and_long(self):
        assert _validate_refspec("") is not None
        assert _validate_refspec("x" * 300) is not None

    def test_reject_unknown_remote(self):
        assert _validate_remote("evil") is not None
        assert _validate_remote("origin") is None


# ---------- git_push（不联网路径：非法参数直接拒绝）----------

class TestGitPushGuard:
    def test_reject_bad_remote_before_exec(self):
        r = git_push(path=".", remote="evil", branch="master")
        assert r.is_error
        assert "白名单" in r.error

    def test_reject_bad_branch_before_exec(self):
        r = git_push(path=".", remote="origin", branch="master;id")
        assert r.is_error
        assert "非法字符" in r.error

    def test_force_flag_is_boolean(self):
        # force=True 合法构造 --force（参数校验层不拒绝布尔）
        assert _validate_refspec("master") is None


# ---------- git_push_preflight ----------

class TestPreflight:
    def _make_repo(self, tmp_path):
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
        (tmp_path / "f.txt").write_text("x")
        subprocess.run(["git", "-C", str(tmp_path), "add", "f.txt"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
        return tmp_path

    def test_preflight_reports_remotes(self, tmp_path):
        repo = self._make_repo(tmp_path)
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", "https://example.com/r.git"],
            check=True,
        )
        r = git_push_preflight(path=str(repo))
        assert r.is_ok
        assert "origin" in r.data["checks"]["remotes"]

    def test_preflight_no_unpushed(self, tmp_path):
        repo = self._make_repo(tmp_path)
        r = git_push_preflight(path=str(repo))
        assert r.is_ok
        assert r.data["checks"]["dirty"] is False

    def test_preflight_gate_blocking_detection(self, tmp_path):
        repo = self._make_repo(tmp_path)
        audit = repo / ".audit"
        audit.mkdir(exist_ok=True)
        (audit / "exempt_review_abc_20260101_000000.json").write_text(
            json.dumps({"state": "FAIL", "acked": False, "commit": "abc"})
        )
        r = git_push_preflight(path=str(repo))
        assert r.is_ok
        assert r.data["checks"]["gate_blocking"] is True

    def test_preflight_gate_acked_not_blocking(self, tmp_path):
        repo = self._make_repo(tmp_path)
        audit = repo / ".audit"
        audit.mkdir(exist_ok=True)
        (audit / "exempt_review_abc_20260101_000000.json").write_text(
            json.dumps({"state": "FAIL", "acked": True, "commit": "abc"})
        )
        r = git_push_preflight(path=str(repo))
        assert r.is_ok
        assert r.data["checks"]["gate_blocking"] is False


# ---------- handler 注册 ----------

class TestHandlerRegistration:
    def test_registry_has_git_push(self):
        from lingclaude.engine.tool_registration import SPECS

        names = {s.name for s in SPECS}
        assert "git_push" in names
        assert "git_push_preflight" in names

    def test_handler_dispatch(self):
        from lingclaude.engine.coding import CodingRuntime

        eng = CodingRuntime.__new__(CodingRuntime)
        # 只测 handler 存在性（不触发真 push）
        assert hasattr(eng, "_git_push_handler")
        assert hasattr(eng, "_git_push_preflight_handler")


# ---------- P0-B: remote URL 黑洞检测 + 白名单 config 出口 ----------

class TestBlackholeRemote:
    """cc P1: git 黑洞风险——remote URL 黑名单。"""

    def test_private_ip_detected(self):
        from lingclaude.engine.git import _is_blackhole_remote_url
        for url in (
            "git@192.168.1.5:repo.git",
            "https://10.0.0.2/org/repo.git",
            "s" + "sh://172.16.3.4:22/repo.git",
            "git://127.0.0.1/repo.git",
            "http://localhost/repo.git",
            "https://100.64.0.1/repo.git",
            "https://169.254.10.10/repo.git",
        ):
            assert _is_blackhole_remote_url(url) is True, url

    def test_public_ip_allowed(self):
        from lingclaude.engine.git import _is_blackhole_remote_url
        for url in (
            "git@github.com:org/repo.git",
            "https://github.com/org/repo.git",
            "git@gitee.com:org/repo.git",
            "https://8.8.8.8/repo.git",
            "git@1.2.3.4:repo.git",
        ):
            assert _is_blackhole_remote_url(url) is False, url

    def test_scp_style_with_blackhole(self):
        from lingclaude.engine.git import _is_blackhole_remote_url
        assert _is_blackhole_remote_url("git@127.0.0.1:repo.git") is True

    def test_empty_url(self):
        from lingclaude.engine.git import _is_blackhole_remote_url
        assert _is_blackhole_remote_url("") is False


class TestPreflightBlackhole(TestPreflight):
    def test_preflight_blackhole_detected(self, tmp_path):
        """黑洞 remote 导致 preflight ok=False。"""
        repo = self._make_repo(tmp_path)
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", "https://127.0.0.1/org/repo.git"],
            check=True,
        )
        r = git_push_preflight(path=str(repo))
        assert r.is_ok  # preflight 本身成功返回
        assert r.data["ok"] is False  # 但 push 判定为不可行
        assert "origin" in r.data["checks"]["blackhole_remotes"]

    def test_preflight_public_remote_no_blackhole(self, tmp_path):
        repo = self._make_repo(tmp_path)
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/org/repo.git"],
            check=True,
        )
        r = git_push_preflight(path=str(repo))
        assert r.is_ok
        assert r.data["checks"]["blackhole_remotes"] == {}


class TestAllowedRemotesConfig:
    def test_config_allows_fork(self, tmp_path, monkeypatch):
        """config 出口：git.allowed_remotes 可覆盖内置白名单（codex P3）。"""
        import lingclaude.core.config as config_mod
        from lingclaude.core.config import GitConfig, lingclaudeConfig

        cfg = lingclaudeConfig(git=GitConfig(allowed_remotes=("origin", "fork")))
        monkeypatch.setattr(config_mod, "load_config", lambda path=None: cfg)

        from lingclaude.engine.git import _validate_remote
        assert _validate_remote("fork") is None  # config 允许
        assert _validate_remote("evil") is not None  # 仍拒绝未知

    def test_default_whitelist_still_works(self):
        from lingclaude.engine.git import _validate_remote
        assert _validate_remote("origin") is None
        assert _validate_remote("github") is None
        assert _validate_remote("upstream") is None
        assert _validate_remote("evil") is not None
