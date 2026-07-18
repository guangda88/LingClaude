# ═══════════════════════════════════════════════
# 灵族(LingFamily) — 内部机密
# ═══════════════════════════════════════════════
# 本文件包含灵族核心技术资产。
# 未经授权，不得外传、复制、逆向工程。
# 仅限灵族成员（12子+智桥+授权对外项目）访问。
# ═══════════════════════════════════════════════

"""
灵元测试薄主干 — 灵元V1.0

主干 = 1个契约 + 3个操作
  契约: TestCase = {name, input, expected, verdict, actual, error}
  出入: run(test, fn) → test          # 执行被测代码
  校验: check(test, eq?) → test       # 比较实际与预期
  聚合: report(cases) → summary       # 汇总所有测试结果

插片:
  parameterized, fixture, coverage, mock, golden_file, ci_reporter, ...
  通过 LACP 声明，不写在主干里。
"""

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class TestCase:
    """灵元测试契约 — 一个测试就是一个 TestCase"""
    __test__ = False  # 防止 pytest 将 dataclass 误收集为测试类
    name: str
    input: Any
    expected: Any
    verdict: bool | None = None
    actual: Any | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def run(tc: TestCase, fn: Callable[[Any], Any]) -> TestCase:
    """出入：执行被测函数，将结果写回 tc.actual"""
    try:
        tc.actual = fn(tc.input)
    except Exception as e:
        tc.error = str(e)
        tc.actual = None
    return tc


def check(tc: TestCase, *,
          eq: Callable[[Any, Any], bool] | None = None) -> TestCase:
    """校验：比较实际输出与预期输出，结果写回 tc.verdict

    eq 参数允许自定义比较器（如模糊匹配、近似比较、异常类型匹配）。
    默认使用 == 精确比较。
    """
    comparator = eq or (lambda a, e: a == e)
    if tc.error is not None:
        tc.verdict = False
    else:
        try:
            tc.verdict = bool(comparator(tc.actual, tc.expected))
        except Exception as e:
            tc.verdict = False
            tc.error = f"check raised: {e}"
    return tc


def report(cases: list[TestCase]) -> dict[str, Any]:
    """聚合：汇总所有测试结果"""
    total = len(cases)
    passed = sum(1 for c in cases if c.verdict is True)
    failed = total - passed
    failures = [c for c in cases if c.verdict is not True]
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "passed_all": failed == 0,
        "failures": [
            {
                "name": c.name,
                "expected": c.expected,
                "actual": c.actual,
                "error": c.error,
            }
            for c in failures
        ],
    }


# ─── pytest adapter ─────────────────────────────────
# 不改主干，即插即用。让 TestCase 能跑在现有 1600+ pytest 体系中。

def pytest_case(tc: TestCase, fn: Callable[[Any], Any]) -> None:
    """把 TestCase 跑成 pytest 用例。

    用法:
        def test_trigger():
            cases = [TestCase(name="硬化", input="硬化规则", expected=True), ...]
            for tc in cases:
                pytest_case(tc, lambda p: L5ConversationLoop().should_trigger(p))
    """
    import pytest  # noqa: F401 — 仅在 pytest 上下文中调用

    tc = run(tc, fn)
    tc = check(tc)
    assert tc.verdict, (
        f"[{tc.name}] "
        f"{tc.error or f'expected={tc.expected!r} actual={tc.actual!r}'}"
    )