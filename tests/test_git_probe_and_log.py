"""git 插件 J4 record 化 + gitea 白名单 + 三 remote 连通探针 防回归测试。

覆盖（2026-09-18 立项收缩版：不建 git-MCP，扩既有实现）：
- 白名单：gitea 进入 allowed_remotes（engine 与 config 两处同源）
- J4：GitPlugin 每次调用入 git_tool_log（成功/失败都记），可 query 可回放
- 探针：DNS→TCP→协议分层 + record 入账 + stale 时效标注（铁律 8 时效域）

测试全部离线确定（127.0.0.1 / example.invalid / file:// 注入），不出网。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from lingclaude.core.config import GitConfig, lingclaudeConfig
from lingclaude.core.state_store import StateStore
from lingclaude.engine.git import _validate_remote
from lingclaude.plugins.tools.git.plugin import GIT_TOOL_LOG, GitPlugin
from lingclaude.plugins.tools.git.remote_probe import (
    RECORD_TYPE,
    probe_all_remotes,
    probe_remote,
    read_probe_record,
)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# ---------- 白名单：gitea 补位 ----------

class TestGiteaWhitelist:
    def test_gitea_allowed_in_engine(self):
        assert _validate_remote("gitea") is None

    def test_gitea_allowed_in_config_default(self):
        assert "gitea" in GitConfig().allowed_remotes

    def test_config_from_dict_default_contains_gitea(self):
        cfg = lingclaudeConfig.from_dict({})
        assert "gitea" in cfg.git.allowed_remotes

    def test_evil_still_rejected(self):
        assert _validate_remote("evil") is not None


# ---------- J4：调用事件全 record 化 ----------

def _make_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "f.txt").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "f.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    return tmp_path


class TestToolCallLogging:
    def test_failed_call_recorded(self, tmp_path):
        store = StateStore(root=tmp_path / "state")
        plugin = GitPlugin(store=store)
        result = plugin.execute(name="git_status", path=str(tmp_path / "not-a-repo"))
        assert isinstance(result, dict) and "error" in result
        keys = store.list_keys(GIT_TOOL_LOG, root=tmp_path / "state")
        assert len(keys) == 1
        rec = store.load(GIT_TOOL_LOG, keys[0], root=tmp_path / "state")
        assert rec["tool"] == "git_status"
        assert rec["ok"] is False
        assert "error" in rec and rec["error"]
        assert "duration_ms" in rec and "at" in rec

    def test_success_call_recorded(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        state_root = tmp_path / "state"
        plugin = GitPlugin(store=StateStore(root=state_root))
        result = plugin.execute(name="git_status", path=str(repo))
        assert isinstance(result, dict) and "error" not in result
        keys = StateStore(root=state_root).list_keys(GIT_TOOL_LOG, root=state_root)
        rec = StateStore(root=state_root).load(GIT_TOOL_LOG, keys[0], root=state_root)
        assert rec["ok"] is True
        assert rec["error"] == ""

    def test_each_call_one_record(self, tmp_path):
        state_root = tmp_path / "state"
        plugin = GitPlugin(store=StateStore(root=state_root))
        plugin.execute(name="git_status", path=str(tmp_path))
        plugin.execute(name="git_status", path=str(tmp_path))
        keys = StateStore(root=state_root).list_keys(GIT_TOOL_LOG, root=state_root)
        assert len(keys) == 2  # 两次调用两条账，键不互撞

    def test_exception_path_recorded_and_raised(self, tmp_path, monkeypatch):
        state_root = tmp_path / "state"
        plugin = GitPlugin(store=StateStore(root=state_root))

        def boom(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "lingclaude.plugins.tools.git.plugin.git_status", boom)
        with pytest.raises(RuntimeError):
            plugin.execute(name="git_status", path=str(tmp_path))
        keys = StateStore(root=state_root).list_keys(GIT_TOOL_LOG, root=state_root)
        rec = StateStore(root=state_root).load(GIT_TOOL_LOG, keys[0], root=state_root)
        assert rec["ok"] is False
        assert "RuntimeError" in rec["error"]


# ---------- 三 remote 连通探针（离线确定） ----------

class TestRemoteProbe:
    def test_dns_fail_layers(self):
        # example.invalid 是 RFC 保留域，必 NXDOMAIN → DNS 层失败，TCP 跳过
        r = probe_remote("https://example.invalid/r.git")
        assert r["ok"] is False
        assert r["layers"]["dns"]["ok"] is False
        assert r["layers"]["tcp"]["skipped"] is True

    def test_tcp_fail_on_loopback_refused(self):
        # 127.0.0.1 DNS 必解析；低位端口连接必拒绝（无监听）→ TCP 层失败
        r = probe_remote("https://127.0.0.1:1/r.git")
        assert r["layers"]["dns"]["ok"] is True
        assert r["layers"]["tcp"]["ok"] is False

    def test_unparseable_url(self):
        r = probe_remote("::::")
        assert r["ok"] is False
        assert "parse" in r["layers"]

    def test_scp_style_url_classified(self):
        r = probe_remote("git@github.com:guangda88/LingClaude.git")
        assert r["scheme"] == "ssh"
        assert r["host"] == "github.com"
        assert r["port"] == 22

    def test_probe_all_saves_records(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "blackhole", "https://127.0.0.1/r.git"],
            check=True,
        )
        state_root = tmp_path / "state"
        out = probe_all_remotes(str(repo), store=StateStore(root=state_root),
                                root=state_root)
        assert out["count"] == 1
        res = out["remotes"]["blackhole"]
        assert res["blackhole"] is True
        assert res["stale"] is False
        assert res["timestamp"]
        rec = read_probe_record("blackhole", store=StateStore(root=state_root),
                                root=state_root)
        assert rec is not None
        assert rec["stale"] is False

    def test_stale_flag_on_old_record(self, tmp_path):
        state_root = tmp_path / "state"
        store = StateStore(root=state_root)
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        store.save(RECORD_TYPE, "origin", {"timestamp": old_ts, "ok": True},
                   root=state_root)
        rec = read_probe_record("origin", store=store, root=state_root,
                                fresh_seconds=3600.0)
        assert rec["stale"] is True


# ---------- manifest 单源一致性 ----------

def test_manifest_declares_probe_tool():
    manifest_path = os.path.join(
        REPO, "lingclaude", "plugins", "tools", "git", "manifest.plugin.json")
    with open(manifest_path) as fh:
        manifest = json.load(fh)
    assert "git_probe_remotes" in manifest["provides"]
