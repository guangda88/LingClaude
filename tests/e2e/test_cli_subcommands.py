"""E2E: CLI 子命令 happy/error 路径(F1/F3/F10 验证)."""
from __future__ import annotations

import json

import pytest

from tests.e2e.conftest import requires_mcp

pytestmark = requires_mcp


def test_help_top_level(cli_runner):
    """--help 列出全部子命令(含 unknowns — F1 修复验证)。"""
    r = cli_runner.run_help()
    assert r.returncode == 0
    assert "unknowns" in r.stdout


def test_run_no_args_shows_welcome(cli_runner, monkeypatch):
    monkeypatch.setenv("LINGCLAUDE_BUS_LISTENER", "0")
    r = cli_runner.run("run", timeout=15.0)
    assert "灵克" in r.stdout or "灵克" in r.stderr


@pytest.mark.skip(reason="F12 链路修通后 run <prompt> 会真实调用本地模型(耗时不可控),"
                         "不适合作为 CI smoke;smoke 语义已由 test_run_no_args_shows_welcome 覆盖")
def test_run_with_prompt_quick_exit(cli_runner, monkeypatch):
    monkeypatch.setenv("LINGCLAUDE_BUS_LISTENER", "0")
    r = cli_runner.run("run", "hello world", timeout=30.0)
    combined = r.stdout + r.stderr
    assert "Traceback (most recent call last)" not in combined or "no api_key" in combined.lower()


@pytest.mark.parametrize("sub", ["optimize", "analyze", "session", "knowledge", "daemon",
                                 "metrics", "governance-audit", "webui", "unknowns"])
def test_subcommand_help(cli_runner, sub):
    r = cli_runner.run_subcommand_help(sub)
    assert r.returncode == 0
    assert "usage:" in r.stdout.lower() or "options:" in r.stdout.lower()


def test_session_list_no_sessions(cli_runner):
    r = cli_runner.run("session", "list", timeout=15.0)
    assert r.returncode == 0


def test_session_delete_requires_id(cli_runner):
    r = cli_runner.run("session", "delete", timeout=15.0)
    assert r.returncode == 1
    assert "required" in r.stdout.lower() or "session ID" in r.stdout


def test_knowledge_stats(cli_runner):
    r = cli_runner.run("knowledge", "stats", timeout=15.0)
    assert r.returncode == 0


def test_knowledge_search_requires_keyword(cli_runner):
    r = cli_runner.run("knowledge", "search", timeout=15.0)
    assert r.returncode == 1
    assert "required" in r.stdout.lower()


def test_daemon_status(cli_runner):
    r = cli_runner.run("daemon", "status", timeout=15.0)
    assert "Traceback" not in (r.stdout + r.stderr) or r.returncode == 0


def test_metrics_stats(cli_runner):
    r = cli_runner.run("metrics", "stats", timeout=15.0)
    assert "Traceback" not in (r.stdout + r.stderr) or r.returncode == 0


def test_metrics_categories(cli_runner):
    r = cli_runner.run("metrics", "categories", timeout=15.0)
    assert "Traceback" not in (r.stdout + r.stderr) or r.returncode == 0


def test_governance_audit_uses_env(cli_runner, tmp_path, monkeypatch):
    """F3 验证:governance-audit 通过 LINGCLAUDE_PROPOSALS_FILE env 选路径。"""
    fake = tmp_path / "fake_proposals.json"
    fake.write_text('{"proposals": []}', encoding="utf-8")
    monkeypatch.setenv("LINGCLAUDE_PROPOSALS_FILE", str(fake))
    r = cli_runner.run("governance-audit", timeout=15.0)
    assert "Traceback" not in (r.stdout + r.stderr)


def test_governance_audit_missing_path_exits_clean(cli_runner):
    r = cli_runner.run(
        "governance-audit",
        "--proposals-file", "/nonexistent/path/proposals.json",
        timeout=15.0,
    )
    assert r.returncode == 1


def test_unknowns_list_no_plugins(cli_runner, isolate_filesystem):
    r = cli_runner.run("unknowns", "list", timeout=15.0)
    assert r.returncode == 0
    assert "(no known unknowns declared)" in r.stdout or r.stdout.strip() == ""


def test_unknowns_list_with_fake_manifest(cli_runner, tmp_path):
    fake_dir = tmp_path / ".lacp" / "plugins"
    fake_dir.mkdir(parents=True, exist_ok=True)
    (fake_dir / "demo.yaml").write_text(
        "known_unknowns:\n  - claim: '测试unknown'\n    category: test\n",
        encoding="utf-8",
    )
    r = cli_runner.run("unknowns", "list", "--manifest-dir", str(fake_dir), timeout=15.0)
    assert r.returncode == 0
    assert "测试unknown" in r.stdout


def test_unknowns_list_json(cli_runner, isolate_filesystem):
    r = cli_runner.run("unknowns", "list", "--json", timeout=15.0)
    assert r.returncode == 0
    if r.stdout.strip():
        assert isinstance(json.loads(r.stdout), list)


def test_unknowns_add_prints_snippet(cli_runner):
    r = cli_runner.run(
        "unknowns", "add",
        "--plugin", "demo", "--claim", "需要补 OAuth", "--severity", "warn",
        timeout=15.0,
    )
    assert r.returncode == 0
    assert "需要补 OAuth" in r.stdout
    assert "known_unknowns:" in r.stdout


def test_unknowns_resolve_not_found(cli_runner, isolate_filesystem):
    r = cli_runner.run(
        "unknowns", "resolve",
        "--plugin", "demo", "--claim-substring", "不存在的 claim",
        timeout=15.0,
    )
    assert r.returncode == 1
    assert "No unknown" in r.stderr or "No unknown" in r.stdout


def test_webui_binary_built_or_skip():
    """F10 验证:binary 已 build(只断言存在+像真二进制,不查 mtime 防 flake)。"""
    from lingclaude.cli.app import _find_webui_binary

    binary = _find_webui_binary()
    if binary is None:
        pytest.skip("F10: webui-server 二进制未 build。cargo build --release")
    assert binary.is_file()
    assert binary.stat().st_size > 1000
