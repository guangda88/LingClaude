# -*- coding: utf-8 -*-
"""tests/scripts/test_sync_opencode_free.py -- sync_opencode_free.py 契约测试.

核心语义(只增不删 + 幂等 + 备份):
- opencode models CLI 输出 -> opencode/*free* 行 -> {m}@opencode 键
- 已存在 key 跳过(幂等), 新增写入前先备份 routes.json
- 模板缺失/CLI 失败 -> 中止返回非零
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SPEC = importlib.util.spec_from_file_location(
    "sync_opencode_free_under_test",
    os.path.join(REPO_ROOT, "scripts", "sync_opencode_free.py"),
)
so = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("sync_opencode_free_under_test", so)
_SPEC.loader.exec_module(so)

_TPL = {
    "upstream_model": "nemotron-3.5-lightning-free",
    "tier": "free",
    "provider": "opencode",
}


@pytest.fixture()
def routes(monkeypatch, tmp_path):
    rp = tmp_path / "routes.json"
    rp.write_text(json.dumps({so.TPL_KEY: dict(_TPL)}), encoding="utf-8")
    monkeypatch.setattr(so, "ROUTES", rp)
    return rp


def _cli_out(*models):
    lines = ["opencode/%s" % m for m in models]
    return subprocess.CompletedProcess(
        ["opencode", "models"], returncode=0, stdout="\n".join(lines), stderr=""
    )


def test_new_free_models_added_with_backup(routes, monkeypatch):
    monkeypatch.setattr(
        so.subprocess, "run", lambda *a, **k: _cli_out("m-free-a", "m-free-b")
    )
    assert so.main() == 0
    data = json.loads(routes.read_text())
    assert "m-free-a@opencode" in data and "m-free-b@opencode" in data
    assert data["m-free-a@opencode"]["upstream_model"] == "m-free-a"
    assert data[so.TPL_KEY] == _TPL  # 模板不动
    # 备份存在且是旧内容
    baks = list(routes.parent.glob("routes.json.bak.sync_*"))
    assert len(baks) == 1
    assert json.loads(baks[0].read_text()) == {so.TPL_KEY: dict(_TPL)}


def test_idempotent_existing_keys_skipped(routes, monkeypatch):
    routes.write_text(
        json.dumps({so.TPL_KEY: dict(_TPL), "m-free-a@opencode": {"upstream_model": "m-free-a"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(so.subprocess, "run", lambda *a, **k: _cli_out("m-free-a", "m-free-b"))
    assert so.main() == 0
    data = json.loads(routes.read_text())
    assert data["m-free-a@opencode"] == {"upstream_model": "m-free-a"}  # 未被覆盖
    assert "m-free-b@opencode" in data  # 只增
    # 有新增(1个) -> 应有备份, 且备份是写入前的旧内容
    baks = list(routes.parent.glob("routes.json.bak.sync_*"))
    assert len(baks) == 1
    assert json.loads(baks[0].read_text()) == {
        so.TPL_KEY: dict(_TPL),
        "m-free-a@opencode": {"upstream_model": "m-free-a"},
    }


def test_no_new_models_writes_nothing(routes, monkeypatch):
    before = routes.read_text()
    monkeypatch.setattr(so.subprocess, "run", lambda *a, **k: _cli_out())
    assert so.main() == 0
    assert routes.read_text() == before


def test_cli_failure_returns_1(routes, monkeypatch):
    monkeypatch.setattr(
        so.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess([], returncode=1, stdout="", stderr="boom"),
    )
    assert so.main() == 1


def test_missing_template_aborts(routes, monkeypatch):
    routes.write_text(json.dumps({}), encoding="utf-8")  # 模板被删
    monkeypatch.setattr(so.subprocess, "run", lambda *a, **k: _cli_out("m-free-a"))
    assert so.main() == 1


def test_non_free_lines_ignored(routes, monkeypatch):
    out = subprocess.CompletedProcess(
        [], returncode=0, stdout="opencode/gpt-x\nother/claude-free\nopencode/free-one\n", stderr=""
    )
    monkeypatch.setattr(so.subprocess, "run", lambda *a, **k: out)
    assert so.main() == 0
    data = json.loads(routes.read_text())
    assert "gpt-x@opencode" not in data
    assert "free-one@opencode" in data
