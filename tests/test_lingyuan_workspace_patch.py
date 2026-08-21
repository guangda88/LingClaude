import json
import sys
from pathlib import Path

import pytest

LINGMINOPT = Path("/home/ai/lingminopt")
if str(LINGMINOPT) not in sys.path:
    sys.path.insert(0, str(LINGMINOPT))
LINGCLAUDE_ROOT = Path(__file__).resolve().parents[1]
if str(LINGCLAUDE_ROOT) not in sys.path:
    sys.path.insert(0, str(LINGCLAUDE_ROOT))

from lingclaude.lingyuan_patches import (  # noqa: E402
    disable_workspace_autoinject,
    enable_for_repo,
    enable_workspace_autoinject,
    is_enabled,
)
from lingyuan.datalog import (  # noqa: E402
    PATH_DATALOG_DIR,
    _resolve_workspace,
    events_today,
    write_event,
)


@pytest.fixture
def tmp_datalog_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("lingyuan.datalog.PATH_DATALOG_DIR", tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def reset_patch():
    disable_workspace_autoinject()
    yield
    disable_workspace_autoinject()


def test_resolve_workspace_matches_datalog_semantics():
    assert _resolve_workspace(None) == _resolve_workspace(None)
    assert _resolve_workspace("") is None
    assert _resolve_workspace("/explicit") == "/explicit"


def test_enable_disable_round_trip(tmp_datalog_dir, monkeypatch):
    monkeypatch.chdir(tmp_datalog_dir)
    assert enable_workspace_autoinject() is True
    assert is_enabled() is True
    assert enable_workspace_autoinject() is False
    assert disable_workspace_autoinject() is True
    assert is_enabled() is False


def test_autoinject_writes_data_workspace(tmp_datalog_dir, monkeypatch):
    monkeypatch.chdir(tmp_datalog_dir)
    enable_workspace_autoinject()
    evt = write_event(
        event_type="l1_act.exit",
        data={"n_layers": 1},
        session_id="s1",
    )
    assert evt["data"]["workspace"] == str(tmp_datalog_dir)
    raw = (tmp_datalog_dir / events_today()[0]["ts"].split("T")[0]).with_suffix(".jsonl") if False else next(tmp_datalog_dir.glob("*.jsonl"))
    row = json.loads(raw.read_text().strip())
    assert row["data"]["workspace"] == str(tmp_datalog_dir)


def test_autoinject_does_not_overwrite_explicit(tmp_datalog_dir, monkeypatch):
    monkeypatch.chdir(tmp_datalog_dir)
    enable_workspace_autoinject()
    evt = write_event(
        event_type="l1_act.exit",
        data={"workspace": "/override"},
        session_id="s1",
    )
    assert evt["data"]["workspace"] == "/override"


def test_explicit_empty_workspace_disables_autoinject(tmp_datalog_dir, monkeypatch):
    monkeypatch.chdir(tmp_datalog_dir)
    enable_workspace_autoinject()
    evt = write_event(
        event_type="l7.memorized",
        data={"key": "model"},
        session_id="",
        workspace="",
    )
    assert "workspace" not in evt["data"]


def test_enable_for_repo_chdirs(tmp_datalog_dir, monkeypatch, tmp_path):
    monkeypatch.setattr("lingyuan.datalog.PATH_DATALOG_DIR", tmp_path)
    enable_for_repo(tmp_path)
    assert Path.cwd() == tmp_path
    assert is_enabled() is True
    evt = write_event(event_type="opt.test", data={"x": 1}, session_id="")
    assert evt["data"]["workspace"] == str(tmp_path)


def test_enable_for_repo_rejects_missing(monkeypatch):
    monkeypatch.chdir(LINGMINOPT)
    with pytest.raises(FileNotFoundError):
        enable_for_repo("/this/does/not/exist")


def test_idempotent_workspace_events(tmp_datalog_dir, monkeypatch):
    monkeypatch.chdir(tmp_datalog_dir)
    enable_workspace_autoinject()
    write_event("opt.dup", {"task_id": "t1"}, "s1")
    write_event("opt.dup", {"task_id": "t1"}, "s1")
    assert len(events_today()) == 1
