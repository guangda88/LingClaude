# tests/test_state_query.py
"""state_query 自省门面测试（闭合期②最小切片）。

E7 门禁：全部离线可重复——功能用 tmp 根目录，不碰真实 ~/.lingclaude；
真实账本连通性为 skipif 附加抽查（环境缺失自动跳过，不假红）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from lingclaude.engine.state_query import StateQuery  # noqa: E402


@pytest.fixture()
def ledger_root(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "k1.json").write_text('{"a": 1}', encoding="utf-8")
    (tmp_path / "alpha" / "nested").mkdir()
    (tmp_path / "alpha" / "nested" / "k2.json").write_text('{"b": 2}', encoding="utf-8")
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "k3.json").write_text('{"c": 3}', encoding="utf-8")
    return tmp_path


def test_query_functionality(ledger_root):
    q = StateQuery(root=ledger_root)
    assert q.record_types() == ["alpha", "beta"]
    assert q.list_keys("alpha") == ["k1", "nested/k2"]
    assert q.record("alpha", "k1") == {"a": 1}
    assert q.record("alpha", "nope") is None
    assert q.summary() == {"alpha": 2, "beta": 1}


def test_readonly_discipline():
    """出生登记观察期指标 3 的守卫：门面不得暴露任何写路径。

    用 AST 检查真实代码（docstring 提及写原语名不算违规）：
    - 不允许调用 .save(...) / .delete(...) 方法
    - 不允许引用 _atomic_write_json（主干写原语）
    - 不允许定义 save/delete/remove 公开方法
    """
    import ast

    q = StateQuery()
    for forbidden in ("save", "delete", "remove"):
        assert not hasattr(q, forbidden), f"只读违规：StateQuery 暴露 {forbidden}"

    src_path = _PROJECT_ROOT / "lingclaude" / "engine" / "state_query.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in ("save", "delete"):
                raise AssertionError(
                    f"只读违规：state_query.py:{node.lineno} 调用写方法 .{func.attr}()")
        if isinstance(node, ast.Name) and node.id == "_atomic_write_json":
            raise AssertionError(
                f"只读违规：state_query.py:{node.lineno} 引用主干写原语")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name not in ("save", "delete", "remove"), \
                f"只读违规：定义了写方法 {node.name}"


def test_empty_root(tmp_path):
    q = StateQuery(root=tmp_path)
    assert q.record_types() == []
    assert q.summary() == {}


_REAL_ROOT = Path.home() / ".lingclaude" / "state"


@pytest.mark.skipif(not _REAL_ROOT.is_dir(), reason="真实账本根不存在（环境相关，跳过）")
def test_real_ledger_connectivity():
    q = StateQuery()
    rts = q.record_types()
    assert rts, "真实账本 record_types 不应为空"
