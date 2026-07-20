"""灵元测试薄主干 - 自测。
验证 TestCase / run / check / report 三个操作的正确性。
"""
import sys
sys.path.insert(0, "/home/ai/lingclaude")
from lingmemory.test_engine import TestCase, run, check, report


def test_run_pure_function():
    """run: 纯函数执行"""
    tc = TestCase(name="add", input=(1, 2), expected=3)
    tc = run(tc, lambda x: x[0] + x[1])
    assert tc.actual == 3, f"Expected 3, got {tc.actual}"
    assert tc.error is None
    print("  ✅ test_run_pure_function")


def test_check_pass():
    """check: 通过"""
    tc = TestCase(name="pass", input=1, expected=1)
    tc = run(tc, lambda x: x)
    tc = check(tc)
    assert tc.verdict is True
    print("  ✅ test_check_pass")


def test_check_fail():
    """check: 失败"""
    tc = TestCase(name="fail", input=1, expected=2)
    tc = run(tc, lambda x: x)
    tc = check(tc)
    assert tc.verdict is False
    print("  ✅ test_check_fail")


def test_run_exception():
    """run: 函数抛异常时 error 被记录"""
    tc = TestCase(name="exception", input=None, expected=None)

    def crash(x):
        raise ValueError("boom")

    tc = run(tc, crash)
    assert tc.error is not None
    assert "boom" in tc.error
    print("  ✅ test_run_exception")


def test_check_on_exception():
    """check: 异常后 verdict 为 False"""
    tc = TestCase(name="except_check", input=None, expected=None)

    def crash(x):
        raise RuntimeError("fail")

    tc = run(tc, crash)
    tc = check(tc)
    assert tc.verdict is False
    assert tc.error is not None
    print("  ✅ test_check_on_exception")


def test_custom_comparator():
    """check: 自定义比较器"""
    tc = TestCase(name="fuzzy", input=0.1, expected=0.1)

    def approx(a, b):
        return abs(a - b) < 0.01

    tc = run(tc, lambda x: x + 0.001)
    tc = check(tc, eq=approx)
    assert tc.verdict is True
    print("  ✅ test_custom_comparator")


def test_report_all_pass():
    """report: 全部通过"""
    cases = [
        TestCase(name="a", input=1, expected=1, verdict=True),
        TestCase(name="b", input=2, expected=2, verdict=True),
    ]
    r = report(cases)
    assert r["total"] == 2
    assert r["passed"] == 2
    assert r["failed"] == 0
    assert r["passed_all"] is True
    print("  ✅ test_report_all_pass")


def test_report_with_failures():
    """report: 有失败"""
    cases = [
        TestCase(name="a", input=1, expected=1, verdict=True),
        TestCase(name="b", input=2, expected=3, verdict=False),
    ]
    r = report(cases)
    assert r["total"] == 2
    assert r["passed"] == 1
    assert r["failed"] == 1
    assert r["passed_all"] is False
    assert len(r["failures"]) == 1
    assert r["failures"][0]["name"] == "b"
    print("  ✅ test_report_with_failures")


def test_check_raised_exception():
    """check: comparator 自身抛异常时 graceful 处理"""
    tc = TestCase(name="bad_eq", input=1, expected=1)

    def broken_eq(a, b):
        raise TypeError("comparator broken")

    tc = run(tc, lambda x: x)
    tc = check(tc, eq=broken_eq)
    assert tc.verdict is False
    assert "check raised" in (tc.error or "")
    print("  ✅ test_check_raised_exception")


def test_metadata_storage():
    """TestCase.metadata 可附加任意数据"""
    tc = TestCase(name="meta", input=1, expected=1, metadata={"tags": ["smoke"]})
    assert tc.metadata["tags"] == ["smoke"]
    print("  ✅ test_metadata_storage")


if __name__ == "__main__":
    tests = [
        test_run_pure_function,
        test_check_pass,
        test_check_fail,
        test_run_exception,
        test_check_on_exception,
        test_custom_comparator,
        test_report_all_pass,
        test_report_with_failures,
        test_check_raised_exception,
        test_metadata_storage,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ {t.__name__}: {e}")
            failed += 1
    print(f"\n{'='*40}")
    print(f"  Total: {len(tests)}, Passed: {passed}, Failed: {failed}")
    sys.exit(0 if failed == 0 else 1)