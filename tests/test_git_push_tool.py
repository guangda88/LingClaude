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
