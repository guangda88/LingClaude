"""L7/L10 PreToolUse hook 集成测试

覆盖 4 场景：read-only / normal edit / agenda role overlap / governance boundary
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HOOK = Path("/home/ai/lingclaude/.lingclaude/hooks/pre_tool_use.py")

# 测试文件调起 subprocess 跑 hook，全局 audit_log 等共享资源
# 在 xdist 并行下会出现 race condition，因此标记为单线程执行
pytestmark = pytest.mark.no_xdist


def _run_hook(context: dict, audit_log: Path | None = None) -> dict:
    env = os.environ.copy()
    env["L7_CONTEXT_JSON"] = json.dumps(context)
    if audit_log:
        env["L7_HOOK_AUDIT_LOG"] = str(audit_log)

    result = subprocess.run(
        [sys.executable, str(HOOK)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd="/home/ai/lingclaude",
        env=env,
    )
    last_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    return {
        "rc": result.returncode,
        "stdout_json": json.loads(last_line) if last_line else {},
    }


class TestHookIntegration:
    def test_read_only_passes(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            audit = Path(f.name)
        try:
            r = _run_hook({"tool_name": "view"}, audit)
            assert r["rc"] == 0
            assert r["stdout_json"].get("blocked") is False
            assert "agents_md_summary" in r["stdout_json"]["context_keys"]
        finally:
            audit.unlink(missing_ok=True)

    def test_normal_edit_passes(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            audit = Path(f.name)
        try:
            r = _run_hook({"tool_name": "edit", "action": "edit"}, audit)
            assert r["rc"] == 0
            assert r["stdout_json"].get("blocked") is False
        finally:
            audit.unlink(missing_ok=True)

    def test_agenda_role_overlap_blocks(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            audit = Path(f.name)
        try:
            r = _run_hook(
                {
                    "tool_name": "vote",
                    "action": "vote_rule_change",
                    "agenda_owner": "lingclaude",
                    "convener": "lingclaude",
                    "host": "lingan",
                },
                audit,
            )
            assert r["rc"] == 1
            assert r["stdout_json"]["blocked"] is True
            assert r["stdout_json"]["hook"] == "check_role_boundary"
            assert "重叠" in r["stdout_json"]["reason"]
        finally:
            audit.unlink(missing_ok=True)

    def test_governance_boundary_violation_blocks(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            audit = Path(f.name)
        try:
            r = _run_hook(
                {
                    "tool_name": "vote",
                    "action": "vote_rule_change",
                    "agenda_owner": "lingyan",
                    "convener": "lingresearch",
                    "host": "lingan",
                    "proposal_owner": "lingyan",
                },
                audit,
            )
            assert r["rc"] == 1
            assert r["stdout_json"]["blocked"] is True
        finally:
            audit.unlink(missing_ok=True)

    def test_valid_vote_passes(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            audit = Path(f.name)
        try:
            r = _run_hook({"tool_name": "vote", "action": "vote"}, audit)
            assert r["rc"] == 0
            assert r["stdout_json"].get("blocked") is False
        finally:
            audit.unlink(missing_ok=True)

    def test_bash_passes(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            audit = Path(f.name)
        try:
            r = _run_hook({"tool_name": "bash", "action": "bash"}, audit)
            assert r["rc"] == 0
            assert r["stdout_json"].get("blocked") is False
        finally:
            audit.unlink(missing_ok=True)

    def test_audit_log_written(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            audit = Path(f.name)
        try:
            _run_hook({"tool_name": "view"}, audit)
            content = audit.read_text()
            assert "inject_agents_md_summary ok" in content
        finally:
            audit.unlink(missing_ok=True)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])