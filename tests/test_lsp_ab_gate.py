"""LSP A/B 闸门测试（装表扫尾 2026-09-23）。

覆盖：三态语义（off 零变化 / control 下移 / treatment 记账）、非法 env fail-open、
记账内容（success/失败/异常均落账）、注册表状态断言。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingclaude.core.types import Result
from lingclaude.engine.lsp_ab import (
    AB_ENV_KEY,
    LspAbRecorder,
    apply_lsp_ab_gate,
    read_ab_group,
)
from lingclaude.engine.tool_registration import SPECS, register_all_tools


class _FakeRegistry:
    """最小注册表替身：只模拟 lsp 相关面（unregister/get/register_handler）。"""

    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.tools: set[str] = {"lsp", "read", "bash"}
        self.unregistered: list[str] = []

    def unregister(self, name: str) -> None:
        self.unregistered.append(name)
        self.tools.discard(name)

    def get(self, name: str):
        if name not in self.tools:
            return Result.fail(f"Tool not found: {name}", code="NOT_FOUND")
        tool_def = type("TD", (), {"handler_name": "_lsp_handler"})()
        return Result.ok(tool_def)

    def register_handler(self, handler_name: str, handler) -> None:
        self.handlers[handler_name] = handler

    def get_handler(self, handler_name: str):
        return self.handlers.get(handler_name)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(AB_ENV_KEY, raising=False)


class TestReadGroup:
    def test_default_off(self):
        assert read_ab_group({}) == "off"

    def test_valid_groups(self, monkeypatch):
        monkeypatch.setenv(AB_ENV_KEY, "control")
        assert read_ab_group() == "control"
        monkeypatch.setenv(AB_ENV_KEY, "TREATMENT")
        assert read_ab_group() == "treatment"

    def test_invalid_falls_back_off(self, monkeypatch):
        monkeypatch.setenv(AB_ENV_KEY, "bogus")
        assert read_ab_group() == "off"


class TestGate:
    def test_off_is_noop(self):
        reg = _FakeRegistry()
        assert apply_lsp_ab_gate(reg, "s1") is None
        assert "lsp" in reg.tools
        assert reg.unregistered == []

    def test_control_unregisters_lsp_only(self, monkeypatch):
        monkeypatch.setenv(AB_ENV_KEY, "control")
        reg = _FakeRegistry()
        assert apply_lsp_ab_gate(reg, "s1") is None
        assert reg.unregistered == ["lsp"]
        assert "lsp" not in reg.tools
        assert "read" in reg.tools  # 其他工具不受牵连

    def test_treatment_wraps_handler(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv(AB_ENV_KEY, "treatment")
        reg = _FakeRegistry()
        reg.handlers["_lsp_handler"] = _ok_handler
        recorder = apply_lsp_ab_gate(reg, "s1")
        assert recorder is not None
        assert "_lsp_handler" in reg.handlers  # 已被包一层

    def test_treatment_missing_handler_fail_open(self, monkeypatch):
        monkeypatch.setenv(AB_ENV_KEY, "treatment")
        reg = _FakeRegistry()  # 没挂 handler
        recorder = apply_lsp_ab_gate(reg, "s1")
        assert recorder is None
        assert "lsp" in reg.tools  # 工具保留，只是不记账


class TestRecorder:
    @pytest.mark.asyncio
    async def test_success_tally(self, tmp_path: Path):
        rec = LspAbRecorder("sess-a", runs_dir=tmp_path)
        wrapped = rec.wrap(_ok_handler)
        result = await wrapped(command="goto_def", file_path="a.py", line=1, character=0)
        assert result.success
        assert rec.stats.lsp_calls == 1
        assert rec.stats.lsp_ok == 1
        assert rec.stats.by_command == {"goto_def": 1}
        lines = (tmp_path / "treatment" / "sess-a.jsonl").read_text().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["command"] == "goto_def"
        assert entry["ok"] is True

    @pytest.mark.asyncio
    async def test_fail_and_exception_tally(self, tmp_path: Path):
        rec = LspAbRecorder("sess-b", runs_dir=tmp_path)
        await rec.wrap(_fail_handler)(command="hover")
        with pytest.raises(RuntimeError):
            await rec.wrap(_boom_handler)(command="search")
        assert rec.stats.lsp_calls == 2
        assert rec.stats.lsp_ok == 0
        assert rec.stats.lsp_errors == 2  # Result.fail 与异常都算 error
        lines = (tmp_path / "treatment" / "sess-b.jsonl").read_text().splitlines()
        assert len(lines) == 2

    def test_snapshot_shape(self, tmp_path: Path):
        rec = LspAbRecorder("sess-c", runs_dir=tmp_path)
        snap = rec.snapshot()
        assert snap["group"] == "treatment"
        assert snap["session_id"] == "sess-c"
        assert snap["lsp_calls"] == 0


class TestSpecIntegrity:
    def test_lsp_spec_exists_with_handler_attr(self):
        lsp_specs = [s for s in SPECS if s.name == "lsp"]
        assert len(lsp_specs) == 1
        assert lsp_specs[0].handler_attr == "_lsp_handler"


async def _ok_handler(**kwargs):
    return Result.ok("def-here")


async def _fail_handler(**kwargs):
    return Result.fail("no server", code="LSP_DOWN")


async def _boom_handler(**kwargs):
    raise RuntimeError("lsp process exploded")
