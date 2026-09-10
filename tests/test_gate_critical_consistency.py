"""仓库级门禁 critical 集合一致性 — 防回归测试。

背景（2026-09-11, b7300fe 事故）:
- pre-commit 拦截判定只认 [L1:SECRET], 而 generate_audit_record 的
  critical 集合含 SHELL_INJECT/SQL_INJECT/PATH_TRAVERSE —
  审计 JSON 标 FAIL 的提交被钩子放行入库（b7300fe 实证）。
- 修复: ling_audit_lib.CRITICAL_*_TOKENS 为单一事实源, pre-commit /
  generate_audit_record / 重审计 / audit_L1 tag / post-commit 撤销
  五处消费点全部收编, 禁止内联令牌元组。
- 本测试从源码层面锁死: 任何重新内联 critical 清单的改动都会 fail。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / ".git" / "hooks"))

import ling_audit_lib as lib  # noqa: E402

# ── 单一事实源本体 ──


def test_critical_constants_defined_in_lib():
    assert "[L1:SHELL_INJECT]" in lib.CRITICAL_L1_TOKENS
    assert "[L1:SQL_INJECT]" in lib.CRITICAL_L1_TOKENS
    assert "[L1:PATH_TRAVERSE]" in lib.CRITICAL_L1_TOKENS
    assert "[L1:SECRET]" in lib.CRITICAL_L1_TOKENS
    assert lib.CRITICAL_L0_TOKENS == ("[L0:SECRET]",)
    assert set(lib.CRITICAL_L2_TOKENS) == {"[L2:DEP_MISSING]", "[L2:DEP_BROKEN]"}


def test_generate_audit_record_shell_inject_fails(tmp_path, monkeypatch):
    """SHELL_INJECT 必须 FAIL — b7300fe 被放行的场景不得复现。"""
    monkeypatch.chdir(tmp_path)
    record = lib.generate_audit_record(
        staged_files=["scripts/x.py"],
        l0=[],
        l1=["[L1:SHELL_INJECT] scripts/x.py — subprocess.run(shell=True) 可导致Shell注入"],
        l2=[],
        tests_passed=True,
        repo_name=None,
        file_hashes={},
        key=b"k" * 32,
    )
    assert record["status"] == "FAIL"
    assert record["pre_commit_passed"] is False


def test_generate_audit_record_noncritical_finding_passes(tmp_path, monkeypatch):
    """非 critical 的 L1 finding 不阻断（WARN 语义保留）。"""
    monkeypatch.chdir(tmp_path)
    record = lib.generate_audit_record(
        staged_files=["scripts/x.py"],
        l0=[],
        l1=["[L1:DEAD_CODE] scripts/x.py — 存在未使用函数（警告级）"],
        l2=[],
        tests_passed=True,
        repo_name=None,
        file_hashes={},
        key=b"k" * 32,
    )
    assert record["status"] == "PASS"
    assert record["pre_commit_passed"] is True


# ── 源码级防漂移: 消费方禁止内联 critical 令牌 ──


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_pre_commit_consumes_constants_not_inline_tokens():
    src = _src(REPO / ".git" / "hooks" / "pre-commit")
    assert "CRITICAL_L1_TOKENS" in src, "pre-commit 必须引用单一事实源常量"
    for token in ("SHELL_INJECT", "SQL_INJECT", "PATH_TRAVERSE"):
        assert f'"{token}"' not in src, (
            f"pre-commit 重新内联了 {token} 判定 — 请改用 ling_audit_lib.CRITICAL_*_TOKENS"
        )


def test_post_commit_consumes_constants_not_inline_tokens():
    src = _src(REPO / ".git" / "hooks" / "post-commit")
    assert "CRITICAL_L1_TOKENS" in src, "post-commit 撤销判定必须引用单一事实源常量"
    for token in ("SHELL_INJECT", "SQL_INJECT", "PATH_TRAVERSE"):
        assert f'"{token}"' not in src, (
            f"post-commit 重新内联了 {token} 判定 — 请改用 CRITICAL_*_TOKENS"
        )


def test_lib_inline_critical_tuple_eradicated():
    """lib 自身（generate_audit_record/重审计/audit_L1 tag）不得再有内联令牌元组。"""
    import re

    src = _src(REPO / ".git" / "hooks" / "ling_audit_lib.py")
    # AST 级检查: 除三个定义赋值外, 任何以 critical 令牌为值的字符串字面量即违规
    import ast as _ast

    tree = _ast.parse(src)
    # 定义赋值语句的行号范围（模块顶层）— 这些是合法的单一事实源本体
    def_lines: set[int] = set()
    for node in tree.body:
        if isinstance(node, _ast.Assign) and any(
            isinstance(t, _ast.Name) and t.id.startswith("CRITICAL_") for t in node.targets
        ):
            def_lines.update(range(node.lineno, getattr(node, "end_lineno", node.lineno) + 1))
    violation = None
    for node in _ast.walk(tree):
        # ast.Module 无 lineno（walk 含根节点），getattr 缺省值使其安全跳过
        if violation or getattr(node, "lineno", -1) in def_lines:
            continue
        if isinstance(node, _ast.Constant) and isinstance(node.value, str):
            for token in ("[L0:SECRET]", "[L1:SHELL_INJECT]", "[L1:SQL_INJECT]",
                          "[L1:PATH_TRAVERSE]", "[L2:DEP_MISSING]", "[L2:DEP_BROKEN]"):
                if token in node.value:
                    violation = (node.lineno, token)
                    break
    assert violation is None, (
        f"ling_audit_lib:{violation[0]} 出现第二个 {violation[1]} 内联点"
        " — 必须引用 CRITICAL_*_TOKENS（docstring/注释不计）"
    )


# ── health_inspect: socket 创建异常不崩（bwrap --unshare-net 实测场景）──


def test_tcp_probe_degrades_on_socket_create_failure(monkeypatch):
    """scripts/ 非 import 包 → spec_from_file_location 加载（test_health_rtt 同款惯例）。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "health_inspect_gate_test", REPO / "scripts" / "health_inspect.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def _boom(*a, **kw):
        raise PermissionError("[Errno 1] Operation not permitted")

    monkeypatch.setattr(mod.socket, "socket", _boom)
    assert mod._tcp_probe(8765) is None, "socket() 抛 PermissionError 时应返回 None 而非崩掉"
