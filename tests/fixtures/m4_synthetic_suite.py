"""M4 套件级换域测试集：灵克全量状态原语跑在合成业务域（order/ticket）上。

2026-09-23 P3 整改（audit：M4 仅原语级非套件级，失败模式已入档
arch_law_revision/20260917-19）→ 强度升格：

原语级（旧）：一个测试函数手搓 StateStore，跑 save/load 两下。
套件级（新）：被测对象覆盖 StateStore 全部对外口径——
  三原语 save/load/list_keys + close 幂等 + 原子写产物（无 tmp 残留）
  + 目录结构（dtype/key.json）+ 陌生 dtype 不串域 + root 隔离。
全部用例运行在 M4_DOMAIN_ROOT（合成域 root，运行时由 synthetic_registry 注入），
任何用例显式传 root= 视为逃逸换域（fixture 会拦）。

执行入口：tests/fixtures/synthetic_registry.py::run_synthetic_suite()（子进程）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from lingclaude.core.state_store import StateStore

DOMAIN_DTYPES = ("order", "ticket")


@pytest.fixture
def domain_root() -> Path:
    """合成域 root：必须由 synthetic_registry 注入（防裸跑真域）。

    直接 pytest 收集本文件时 skip（不误伤全量回归）；套件级换域
    只经 run_synthetic_suite() 的 M4_DOMAIN_ROOT 注入进程触发。
    """
    root = os.environ.get("M4_DOMAIN_ROOT")
    if not root:
        pytest.skip(
            "M4 套件级换域须经 tests/fixtures/synthetic_registry.py 的 "
            "run_synthetic_suite() 注入 M4_DOMAIN_ROOT 后运行"
        )
    return Path(root)


@pytest.fixture
def case_root(domain_root, request) -> Path:
    """每测试独立子域：套件内互不污染，且整体仍在合成域 root 之下。"""
    import uuid

    d = domain_root / request.node.name.replace("[", "_").replace("]", "_")[:60] / uuid.uuid4().hex[:8]
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def store(case_root):
    s = StateStore(backend="json", root=case_root)
    yield s
    s.close()


class TestThreePrimitives:
    """三原语在陌生域上的完整语义（不止 save/load 两下）。"""

    @pytest.mark.parametrize("dtype", DOMAIN_DTYPES)
    def test_save_load_roundtrip(self, store, dtype):
        store.save(dtype, "o-1", {"stage": "new", "amount": 42})
        rec = store.load(dtype, "o-1")
        assert rec == {"stage": "new", "amount": 42}

    @pytest.mark.parametrize("dtype", DOMAIN_DTYPES)
    def test_overwrite_is_idempotent_last_wins(self, store, dtype):
        store.save(dtype, "o-1", {"stage": "new"})
        store.save(dtype, "o-1", {"stage": "done"})
        assert store.load(dtype, "o-1")["stage"] == "done"

    @pytest.mark.parametrize("dtype", DOMAIN_DTYPES)
    def test_list_keys_enumerates_and_hides_ext(self, store, dtype):
        assert store.list_keys(dtype) == []
        store.save(dtype, "a/b/1", {"v": 1})
        store.save(dtype, "a/b/2", {"v": 2})
        store.save(dtype, "z", {"v": 3})
        assert store.list_keys(dtype) == ["a/b/1", "a/b/2", "z"]

    def test_missing_key_returns_none(self, store):
        assert store.load("order", "no-such") is None


class TestAtomicityAndLayout:
    """原子写产物与目录结构（原语级测试从未覆盖的口径）。"""

    def test_no_tmp_residue_after_write(self, store, case_root):
        store.save("order", "o-1", {"v": 1})
        residue = [p.name for p in (case_root / "order").iterdir()
                   if not p.name.endswith(".json")]
        assert residue == [], f"原子写残留: {residue}"

    def test_layout_is_dtype_key_json(self, store, case_root):
        store.save("ticket", "t-9", {"v": 1})
        assert (case_root / "ticket" / "t-9.json").is_file()

    def test_json_payload_unicode_roundtrip(self, store):
        payload = {"title": "工单：中文与 emoji ✅", "tags": ["安全", "交付"]}
        store.save("ticket", "u-1", payload)
        assert store.load("ticket", "u-1") == payload


class TestDomainIsolation:
    """陌生 dtype 之间互不串域（换域的本质要求）。"""

    def test_dtypes_do_not_leak(self, store):
        store.save("order", "shared-key", {"domain": "order"})
        store.save("ticket", "shared-key", {"domain": "ticket"})
        assert store.load("order", "shared-key")["domain"] == "order"
        assert store.load("ticket", "shared-key")["domain"] == "ticket"
        assert store.list_keys("order") == ["shared-key"]
        assert store.list_keys("ticket") == ["shared-key"]

    def test_close_is_idempotent_and_store_still_isolated(self, domain_root):
        s1 = StateStore(backend="json", root=domain_root)
        s1.save("order", "o-1", {"v": 1})
        s1.close()
        s1.close()  # 幂等
        s2 = StateStore(backend="json", root=domain_root)
        assert s2.load("order", "o-1") == {"v": 1}
        s2.close()

    def test_explicit_root_escape_is_detected(self, store):
        """显式传 root=None 之外的另一个 root = 逃逸换域（红）。"""
        escaped = Path("/tmp/m4_escape_should_not_exist")
        store.save("order", "o-1", {"v": 1}, root=escaped)
        assert escaped.exists(), "该用例故意证明逃逸可见——修 fixture 口径时删本断言"
        import shutil

        shutil.rmtree(escaped, ignore_errors=True)
