# tests/test_prefix_cache_boundary.py
"""P0-3（2026-09-21，全 15 家精读 §3.2 cc 式缓存边界行）CI 断言。

验收：
- _BASE_PROMPT 以 __DYNAMIC_BOUNDARY__ 边界行结尾（前缀/动态分界显式化）
- build_adaptive_system_prompt 对任意动态入参字节级一致（冻结前缀）
- assert_prefix_stable() 无参可直接断言（CI 调用面）
- 动态段（build_dynamic_system_suffix）仍正常产出 SESSION_CONTEXT（行为不回退）
"""
from __future__ import annotations

import pytest

from lingclaude.core.system_prompt_builder import (
    _BASE_PROMPT,
    assert_prefix_stable,
    build_adaptive_system_prompt,
    build_dynamic_system_suffix,
    DYNAMIC_BOUNDARY_MARKER,
)


def _fakes():
    from lingclaude.core.system_prompt_builder import (
        _FakeBM, _FakeLM, _FakeMeta, _FakeDD,
    )
    return dict(
        behavior=_FakeBM(), layered_memory=_FakeLM(), meta_cognition=_FakeMeta(),
        messages=["prev"], session_cache_hits=0, dementia_detector=_FakeDD(),
        project_index=None, tool_call_count=0,
    )


def test_boundary_marker_at_end():
    assert _BASE_PROMPT.rstrip().endswith(DYNAMIC_BOUNDARY_MARKER)


def test_prefix_byte_stable_across_queries():
    kw = _fakes()
    a = build_adaptive_system_prompt(**kw, current_query="q1")
    b = build_adaptive_system_prompt(**_fakes(), current_query="完全不同的 q2")
    assert a == b == _BASE_PROMPT


def test_assert_prefix_stable_passes():
    assert_prefix_stable()  # 无参，断言失败即抛 AssertionError


def test_dynamic_suffix_still_produces_session_context():
    suffix = build_dynamic_system_suffix(**_fakes(), current_query="x")
    assert "\n\n# SESSION_CONTEXT\n" in suffix
    # 动态段不含边界行（边界只在前缀末尾，动态段纯 tail）
    assert DYNAMIC_BOUNDARY_MARKER not in suffix
