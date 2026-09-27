"""registry_loader（AgentSeam 启动装配器）守卫测试。

锚点：
- 装载完整性：plugins/agents 下全部非豁免插件 register 后 SeamRegistry.AGENT 可见；
- 幂等：重复 load_all 不产生副作用（同名覆盖语义）；
- fail-soft：单插件 register 抛异常不阻断其余装载，失败入 J4 record；
- 豁免显式：EXEMPT_DIRS 不装载且不报 failed（J5：逃逸显式）；
- hermetic：不依赖真实外部服务（装载只 import+register，不发网络请求）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lingclaude.core.seam import SeamRegistry, SeamType
from lingclaude.plugins.agents import registry_loader


@pytest.fixture()
def loaded_registry():
    """执行一次真实全量装载（import+register 无网络依赖，hermetic）。"""
    report = registry_loader.load_all()
    yield report
    # 清理：把本轮注册的 AGENT 缝卸掉，避免污染其他测试的注册表视图
    for key in report["registered"]:
        try:
            SeamRegistry.unregister(SeamType.AGENT, key)
        except Exception:  # noqa: BLE001 —— 清理尽力而为
            pass


def test_load_all_registers_majority(loaded_registry):
    """全量装载：绝大多数插件应成功（当前账面 27 装载 / 24 注册）。"""
    r = loaded_registry
    assert not r["failed"], f"不应有装载失败: {r['failed']}"
    assert len(r["registered"]) >= 20, f"注册数异常: {len(r['registered'])}"
    # 核心插片必须在册
    for key in ("agent/ghidra", "agent/lingxi", "agent/lc-guard"):
        assert key in r["registered"], f"核心插片 {key} 未注册"


def test_exempt_dirs_explicit(loaded_registry):
    """豁免必须显式登记（proj_family_meeting 缺 register 入口），不进 failed。"""
    r = loaded_registry
    assert "proj_family_meeting" in r["exempt"]
    assert "proj_family_meeting" not in r["failed"]
    assert "proj_family_meeting" not in r["loaded"]


def test_load_all_idempotent(loaded_registry):
    """幂等：第二轮装载成功数一致、注册集合不变（同名覆盖语义）。"""
    r1 = loaded_registry
    before = set(registry_loader.registered_agents())
    r2 = registry_loader.load_all()
    after = set(registry_loader.registered_agents())
    assert len(r2["loaded"]) == len(r1["loaded"])
    assert not r2["failed"]
    assert before == after


def test_failure_recorded_j4(tmp_path):
    """fail-soft：坏插件不阻断好插件，且失败入 J4 record（agent_registry_load）。

    注：装载器按包名固定导入 lingclaude.plugins.agents.<dir>.plugin，
    故坏/好插件必须建在真实包目录下（测试后清理），临时目录无法被导入。
    """
    from lingclaude.core.state_store import StateStore

    store = StateStore(backend="json", root=tmp_path / "agent_runs")
    bad_root = registry_loader.AGENTS_ROOT
    bad = bad_root / "zz_bad_plugin_test"
    good = bad_root / "zz_good_plugin_test"
    try:
        bad.mkdir(exist_ok=True)
        (bad / "plugin.py").write_text(
            "def register(registry):\n"
            "    raise RuntimeError('boom for test')\n",
            encoding="utf-8",
        )
        good.mkdir(exist_ok=True)
        (good / "plugin.py").write_text(
            "def register(registry):\n"
            "    from lingclaude.core.seam import SeamRegistry, SeamType\n"
            "    SeamRegistry.register(SeamType.AGENT, 'agent/test-good-loader', object())\n",
            encoding="utf-8",
        )

        r = registry_loader.load_all(store=store, root=bad_root)
        assert "zz_bad_plugin_test" in r["failed"], "坏插件必须进 failed"
        assert "zz_good_plugin_test" in r["loaded"], "坏插件不得阻断好插件（fail-soft）"
        # J4：failed 与 succeeded 都有 record 可查
        assert store.load("agent_registry_load", "load:zz_bad_plugin_test") is not None
        assert store.load("agent_registry_load", "load:zz_good_plugin_test") is not None
    finally:
        # 清理：临时目录 + 测试注册的缝
        import shutil
        shutil.rmtree(bad, ignore_errors=True)
        shutil.rmtree(good, ignore_errors=True)
        SeamRegistry.unregister(SeamType.AGENT, "agent/test-good-loader")


def test_registered_agents_view():
    """对账视图：registered_agents() 返回排序后的 AGENT 缝 key 列表。"""
    keys = registry_loader.registered_agents()
    assert isinstance(keys, list)
    assert keys == sorted(keys)
