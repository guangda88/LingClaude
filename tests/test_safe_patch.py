"""safe_patch 防呆三样的变异验证级回归锁。

来源：2026-10-07 3 次 heredoc 引号损坏 + 1 次锚点失配事故复盘。
变异验证标准（沿用 e6a18ca 惯例）：移除防线 → FAIL；恢复防线 → PASS。

运行：pytest tests/test_safe_patch.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from safe_patch import safe_patch  # noqa: E402


def _make_target(tmp_path: Path) -> Path:
    f = tmp_path / "target.py"
    f.write_text(
        "VALUE = 'headless': True\n"  # 故意的语法错误形：用于回滚验证
        "def hello():\n    return 'ok'\n",
        encoding="utf-8",
    )
    return f


def test_anchor_zero_hit_aborts_before_write(tmp_path):
    """防线①：锚点 0 命中必须中止且文件分毫未动（当日 ^SESSION_FILE 事故）。

    变异验证：若去掉 0 命中止步，补丁会静默落盘 → 本测试 FAIL。
    """
    f = _make_target(tmp_path)
    before = f.read_text(encoding="utf-8")
    rc = safe_patch(
        str(f),
        [{"old": "ANCHOR_THAT_DOES_NOT_EXIST", "new": "x", "why": "0命中"}],
    )
    assert rc == 3, "0 命中必须返回 3（中止）"
    assert f.read_text(encoding="utf-8") == before, "文件必须分毫未动"
    assert not list(tmp_path.glob("*.bak_*")), "中止发生在备份前，不应产生备份"


def test_anchor_multi_hit_aborts(tmp_path):
    """防线①补强：锚点命中 >1 次同样拒绝写盘（避免误伤重复代码块）。"""
    f = tmp_path / "dup.py"
    f.write_text("x = 1\nx = 1\n", encoding="utf-8")
    rc = safe_patch(str(f), [{"old": "x = 1", "new": "y = 2", "why": "双命中"}])
    assert rc == 3
    assert f.read_text(encoding="utf-8") == "x = 1\nx = 1\n"


def test_compile_failure_rolls_back(tmp_path):
    """防线③：写盘后 py_compile 失败必须自动回滚（当日 __main__ 行引号损坏事故）。

    变异验证：若去掉 py_compile 环节，语法损坏文件会静默落盘 → 本测试 FAIL。
    """
    f = _make_target(tmp_path)
    original = f.read_text(encoding="utf-8")
    rc = safe_patch(
        str(f),
        [
            {
                "old": "VALUE = 'headless': True",
                "new": "VALUE = 'broken': True: oops",  # 制造语法错误
                "why": "坏补丁",
            },
            {
                "old": "    return 'ok'",
                "new": "    return 'still-ok'",  # 本身无害，但整文件语法已坏
                "why": "好补丁",
            },
        ],
    )
    assert rc == 4, "验证失败必须返回 4"
    assert f.read_text(encoding="utf-8") == original, "必须回滚到原件"
    backups = list(tmp_path.glob("*.bak_*"))
    assert len(backups) == 1, "回滚用的备份必须存在"


def test_happy_path_applies_and_verifies(tmp_path):
    """正路：唯一锚点 + 合法补丁 → 落盘且验证通过。"""
    f = tmp_path / "good.py"
    f.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    rc = safe_patch(
        str(f),
        [
            {
                "old": "    return a + b",
                "new": "    return a + b + 0",
                "why": "合法改写",
            }
        ],
    )
    assert rc == 0
    assert "return a + b + 0" in f.read_text(encoding="utf-8")
