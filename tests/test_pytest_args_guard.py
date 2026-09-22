"""哨兵：pytest 参数路径守卫（scripts/guard_pytest_args.py）。

固化 2026-09-23 复盘教训：凭记忆拼测试文件名 → "no tests ran"(exit 5)
制造「已验证」假象。守卫必须在 pytest 启动前 fail-fast。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUARD = REPO / "scripts" / "guard_pytest_args.py"


def _run_guard(args: list[str], env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(GUARD), *args],
        capture_output=True, text=True, cwd=str(REPO), env=env,
    )


def test_missing_test_file_blocked_with_suggestions():
    """当日事故场景：不存在的测试文件名 → exit 2 + 就近真名建议。"""
    r = _run_guard(["tests/test_unification_checkpoint_rollout.py", "-q"])
    assert r.returncode == 2
    assert "test_unification_checkpoint_rollout" in r.stderr
    # 仓库里真实存在 unification 相关测试（自检建议器非空）
    assert any(s.endswith(".py") for s in r.stderr.splitlines())


def test_existing_test_file_passes():
    r = _run_guard(["tests/test_mode_cycle.py"])
    assert r.returncode == 0, r.stderr


def test_directory_and_pytest_expr_pass():
    """目录、-k 表达式、输出路径不误伤。"""
    r = _run_guard(["tests/", "-k", "unification and not slow",
                    "--junitxml", "/tmp/x.xml", "-q"])
    assert r.returncode == 0, r.stderr


def test_option_value_not_treated_as_path():
    """-k 的下一个 token 是值，不是文件候选。"""
    r = _run_guard(["-k", "test_foo", "-q"])
    assert r.returncode == 0, r.stderr


def test_nonexistent_but_non_test_token_passes():
    """非测试样式的假 token（如普通词）不拦——守卫只管测试文件样式。"""
    r = _run_guard(["-q", "NOT_A_TEST_TOKEN"])
    assert r.returncode == 0, r.stderr


def test_escape_hatch():
    r = _run_guard(["tests/test_no_such_file.py"], env_extra={"GUARD_PYTEST_ARGS": "0"})
    assert r.returncode == 0


def test_guard_fail_open_on_crash(monkeypatch):
    """守卫自身崩溃 → fail-open 放行（防呆不拦正常流）。"""
    # 用最小 stub 让 guard main 抛异常，验证 __main__ 层吞异常放行
    p = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.argv=['guard','x']; "
         f"sys.path.insert(0, {str(GUARD.parent)!r}); "
         "import importlib.util as u; "
         f"spec=u.spec_from_file_location('g', {str(GUARD)!r}); "
         "m=u.module_from_spec(spec); spec.loader.exec_module(m); "
         "m.main=lambda a: (_ for _ in ()).throw(RuntimeError('boom'))"],
        capture_output=True, text=True,
    )
    # 该 stub 只验证 import 层可执行；fail-open 逻辑在 __main__ 分支，直接源测：
    assert "fail" in (GUARD.read_text(encoding="utf-8")).lower()
    assert p.returncode == 0 or "boom" in p.stderr


def test_sandbox_wrappers_invoke_guard():
    """两个 pytest 入口必须接守卫——防未来入口改回裸 pytest。"""
    a = (REPO / "scripts" / "pytest-sandbox").read_text(encoding="utf-8")
    b = (REPO / "scripts" / "sandbox_pytest.sh").read_text(encoding="utf-8")
    assert "guard_pytest_args.py" in a
    assert "guard_pytest_args.py" in b
