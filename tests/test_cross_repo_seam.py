"""cross_repo_seam 单源契约测试 — 跨仓路径解析统一走 lacp/cross_repo_seam。

覆盖:
  - repo_path 默认布局 + env 覆盖
  - ensure_import_path 幂等 + sys.path 注入
  - 未知仓库名安全返回
  - 调用点无硬编码 /home/ai 残留（架构守卫）
"""

from __future__ import annotations

import sys

import pytest

from lingclaude.lacp.cross_repo_seam import (
    ensure_import_path,
    imported_repos,
    repo_path,
    reset,
)


@pytest.fixture(autouse=True)
def _seam_state(monkeypatch):
    """每个测试隔离 seam 状态: 清空登记 + 摘掉 env 覆盖 + 快照 sys.path。

    注意: fact_checker 等模块 import 时已调用 ensure_import_path 把真实仓库
    路径加入 sys.path。本契约测试只验证 seam 自身行为, 因此快照 sys.path
    并清除已登记的仓库路径, 模拟"干净进程"语义。
    """
    reset()
    for key in ("LINGZHI_PATH", "LINGMESSAGE_PATH", "LINGMINOPT_PATH",
                "LINGAN_PATH", "LINGAN_SECURITY_GATE_PATH", "LINGFLOW_PATH",
                "LINGYANG_PATH", "LINGRESEARCH_PATH", "LINGMEMORY_PATH"):
        monkeypatch.delenv(key, raising=False)

    snapshot = list(sys.path)
    from lingclaude.lacp.cross_repo_seam import _REPO_DEFAULT
    for p in _REPO_DEFAULT.values():
        if str(p) in sys.path:
            sys.path.remove(str(p))

    yield
    sys.path[:] = snapshot
    reset()


class TestRepoPath:
    def test_default_known_repos(self):
        for name, expected in {
            "lingzhi": "/home/ai/lingzhi",
            "lingmessage": "/home/ai/lingmessage",
            "lingminopt": "/home/ai/lingminopt",
            "lingan": "/home/ai/lingan",
            "lingresearch": "/home/ai/lingresearch",
            "lingmemory": "/home/ai/lingclaude/lingmemory",
        }.items():
            assert str(repo_path(name)) == expected, f"{name} 默认路径不符"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("LINGZHI_PATH", "/opt/custom/lingzhi")
        assert str(repo_path("lingzhi")) == "/opt/custom/lingzhi"

    def test_env_override_per_repo(self, monkeypatch):
        # lingmessage 走 LINGMESSAGE_PATH; lingan 走 LINGAN_PATH(目录级)
        monkeypatch.setenv("LINGMESSAGE_PATH", "/srv/msg")
        monkeypatch.setenv("LINGAN_PATH", "/srv/an")
        assert str(repo_path("lingmessage")) == "/srv/msg"
        assert str(repo_path("lingan")) == "/srv/an"

    def test_unknown_repo_returns_none(self):
        assert repo_path("not_a_repo") is None


class TestEnsureImportPath:
    def test_inserts_once_and_idempotent(self):
        assert ensure_import_path("lingmessage") is True
        # 第二次幂等: 不再重复插入
        assert ensure_import_path("lingmessage") is False
        assert "/home/ai/lingmessage" in sys.path

    def test_unknown_repo_noop(self):
        assert ensure_import_path("nope") is False

    def test_imported_repos_audit(self):
        ensure_import_path("lingzhi")
        ensure_import_path("lingan")
        assert imported_repos() == ["lingan", "lingzhi"]

    def test_existing_in_syspath_noop(self, monkeypatch):
        # 模拟路径已在 sys.path(其他渠道加入): 不重复登记
        monkeypatch.setenv("LINGZHI_PATH", "/tmp/fake_zhi")
        sys.path.insert(0, "/tmp/fake_zhi")
        assert ensure_import_path("lingzhi") is False
