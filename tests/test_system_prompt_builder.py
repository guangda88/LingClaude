"""tests for system_prompt_builder — SESSION_CONTEXT 注入 + 扩容基础规则.

背景: 对标生产级 agent 提示词（参考 asgeirtj/system_prompts_leaks, CC0-1.0）
补齐会话环境注入（cwd/日期/git 摘要）与工作纪律条款。
纪律: 构建器任何环境采集失败都不得让 build_adaptive_system_prompt 崩溃。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from lingclaude.core.system_prompt_builder import (
    _BASE_PROMPT,
    _build_session_context,
    build_adaptive_system_prompt,
    build_dynamic_system_suffix,
)


class _DummyBehavior:
    hallucination_risk = 0.0
    frustration_rate = 0.0
    tool_error_rate = 0.0
    corrections_received = 0
    total_turns = 0
    tool_use_rate = 0.0
    tool_error_count = 0
    auto_sub_agent_threshold = 0  # 测试中显式关闭 R8 提示


class _DummyMemory:
    def inject_common_to_prompt(self) -> str:
        return ""

    def build_context_injection(self, current_query: str) -> str:
        return ""


class _DummyMeta:
    def get_system_prompt_injection(self) -> str:
        return ""


class _DummyDementia:
    class _Diag:
        intervention_prompt = None

    def diagnose(self) -> "_DummyDementia._Diag":
        return self._Diag()


def _build(messages: list[str] | None = None) -> str:
    return build_adaptive_system_prompt(
        behavior=_DummyBehavior(),
        layered_memory=_DummyMemory(),
        meta_cognition=_DummyMeta(),
        messages=messages or ["测试问题"],
        session_cache_hits=0,
        dementia_detector=_DummyDementia(),
        project_index=None,
        tool_call_count=0,
    )


# ---------- 基础规则扩容 ----------


def test_base_prompt_has_work_discipline():
    assert "工作纪律" in _BASE_PROMPT
    for frag in ("7. ", "8. ", "9. ", "10. ", "11. "):
        assert frag in _BASE_PROMPT


def test_base_prompt_keeps_legacy_fragments():
    # 旧规则不被破坏（test_message_builder.py 锁定的片段）
    assert _BASE_PROMPT.startswith("你是灵克")
    assert "核心规则" in _BASE_PROMPT
    assert "用中文回答" in _BASE_PROMPT


def test_path_discipline_rule_present():
    # 幻觉回声防疫条款
    assert "禁止凭记忆或他人汇报转述" in _BASE_PROMPT


# ---------- SESSION_CONTEXT 注入 ----------


def test_session_context_in_git_repo(tmp_path: Path):
    # 注: 不做 git add——对象库写入依赖 /dev/urandom, 前台沙箱不可用。
    # 未跟踪文件(untracked)同样出现在 porcelain 输出, 足以驱动分支+脏文件逻辑。
    untracked_marker = "?" * 2
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    import os

    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        block = _build_session_context()
    finally:
        os.chdir(old)
    assert "# SESSION_CONTEXT" in block
    assert "当前目录" in block
    assert "当前日期" in block
    assert "git 分支" in block
    assert untracked_marker in block


def test_session_context_non_git_dir(tmp_path: Path):
    import os

    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        block = _build_session_context()
    finally:
        os.chdir(old)
    # 非 git 目录: cwd/日期仍在, git 摘要优雅缺席
    assert "当前目录" in block
    assert "当前日期" in block
    assert "git 分支" not in block


def test_session_context_dirty_files_capped(tmp_path: Path):
    untracked_marker = "?" * 2
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for i in range(15):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    import os

    old = os.getcwd()
    os.chdir(tmp_path)
    try:
        block = _build_session_context()
    finally:
        os.chdir(old)
    assert "未提交改动" in block
    # 上限 10 条（未跟踪状态行）
    dirty_lines = [
        ln for ln in block.splitlines() if ln.strip().startswith(untracked_marker)
    ]
    assert len(dirty_lines) <= 10


# ---------- 端到端组装 ----------


def test_build_prompt_smoke_with_context():
    # P0-3 拆分后：基础规则在冻结前缀（_BASE_PROMPT），SESSION_CONTEXT 在动态尾随块
    prompt = _build()
    assert prompt.startswith("你是灵克")
    assert "__DYNAMIC_BOUNDARY__" in prompt  # 前缀/动态分界行
    suffix = build_dynamic_system_suffix(
        behavior=_DummyBehavior(),
        layered_memory=_DummyMemory(),
        meta_cognition=_DummyMeta(),
        messages=["测试问题"],
        session_cache_hits=0,
        dementia_detector=_DummyDementia(),
        project_index=None,
    )
    assert "# SESSION_CONTEXT" in suffix
    # 动态段不渗入前缀：前缀体不含 SESSION_CONTEXT（缓存边界语义）
    assert "# SESSION_CONTEXT" not in prompt


def test_build_prompt_r8_not_triggered_at_zero():
    prompt = _build()
    assert "R8 提示" not in prompt


def test_build_prompt_r8_triggered():
    import lingclaude.core.system_prompt_builder as spb

    class _B(_DummyBehavior):
        auto_sub_agent_threshold = 3

    # P0-3 拆分后 R8 推荐语随动态 extras 走 build_dynamic_system_suffix
    prompt = spb.build_dynamic_system_suffix(
        behavior=_B(),
        layered_memory=_DummyMemory(),
        meta_cognition=_DummyMeta(),
        messages=["问题"],
        session_cache_hits=0,
        dementia_detector=_DummyDementia(),
        project_index=None,
        tool_call_count=10,
    )
    assert "R8 提示" in prompt


# ---------- 双胞胎防复发 ----------


def test_message_builder_base_prompt_is_alias():
    from lingclaude.core.message_builder import MessageBuilder

    assert MessageBuilder.BASE_PROMPT is _BASE_PROMPT
