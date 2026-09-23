"""M4 套件级换域 fixture 的合成域注册表（2026-09-23 P3 整改）。

铁律 M4 条文：「主干原语在从未见过的业务域上原样跑通」，强度要求为
**套件级**——不是在真实业务域上再测一遍，而是把整个 StateStore 依赖面
（save/load/list_keys 三原语 + 原子写 + 目录结构）放到一个人造业务域上
完整跑一遍。人造域沿用灵元诞生同款 order/ticket（与灵克业务零交集）。

用法（子进程内）：
    from tests.fixtures.synthetic_registry import run_synthetic_suite
    failures = run_synthetic_suite()
    assert failures == []

run_synthetic_suite() 用 pytest 在子进程内收集执行
tests/fixtures/m4_synthetic_suite.py（合成域上的守卫级测试集），
返回失败数。子进程隔离的动机：换域 root 用环境变量注入（M4_DOMAIN_ROOT），
避免污染本进程已 import 的 StateStore 单例状态——换域必须换到底。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 人造业务域 dtype（与灵克业务零交集；StateStore 对 dtype 是不透明字符串键）
SYNTHETIC_DTYPES = ("order", "ticket")

SYNTHETIC_SUITE = Path(__file__).parent / "m4_synthetic_suite.py"


def run_synthetic_suite(pytest_args: list[str] | None = None) -> int:
    """在合成域 root 上跑完整测试集，返回失败数。

    本函数设计为在被测子进程/独立进程中调用：
      1. 造一次性合成域 root（tmp，跑完即删）
      2. M4_DOMAIN_ROOT 环境变量注入 —— m4_synthetic_suite 内所有 StateStore
         缺省构造都读它，等效于「整套状态面被搬到陌生域」
      3. pytest.main 收集执行 m4_synthetic_suite.py
    """
    import pytest

    root = tempfile.mkdtemp(prefix="m4_domain_")
    os.environ["M4_DOMAIN_ROOT"] = root
    try:
        args = [str(SYNTHETIC_SUITE), "-q", "--no-header", "-p", "no:cacheprovider"]
        if pytest_args:
            args.extend(pytest_args)
        return int(pytest.main(args))
    finally:
        os.environ.pop("M4_DOMAIN_ROOT", None)
        import shutil

        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(0 if run_synthetic_suite() == 0 else 1)
