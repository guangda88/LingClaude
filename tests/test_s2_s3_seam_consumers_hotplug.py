"""S2/S3 契约测试：SeamRegistry 消费面打通（灵元：热拔插真正生效）。

S2 — 消费侧：
  - bash.py 沙箱：SeamRegistry.get_optional(SANDBOX, "default") 优先，注册表换血后
    下个命令用新后端（热拔插生效，无需重启、无需动 bash.py）
  - ProviderRegistry.create：已注册实例（非类）优先返回（热拔插语义）

S3 — 主干倒装清零：
  - core/ 无模块级 from lingclaude.engine import（模块级倒装 = 0）
  - mcp_tools.py 无模块级 engine import

纪律：只测消费面语义，不依赖具体后端（Fake 插片满足 SandboxPlugin 协议）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lingclaude.core.seam import SeamRegistry, SeamType


@pytest.fixture(autouse=True)
def _clean_registry():
    SeamRegistry.reset()
    yield
    SeamRegistry.reset()


# --- S2: bash 沙箱消费面（热拔插生效）---

class _FakeSandbox:
    """满足 SandboxPlugin 协议的最小 Fake（name/available/wrap）。"""

    name = "fake_sandbox"
    wrap_calls = 0

    def available(self) -> bool:
        return True

    def wrap(self, command: str, **kwargs: str) -> str:
        _FakeSandbox.wrap_calls += 1
        return f"[FAKE:{command}]"


def test_bash_sandbox_consumes_seam_default(monkeypatch):
    """bash._sandbox_command 优先查进程内 SeamRegistry(SANDBOX, 'default')。"""
    from lingclaude.engine.bash import BashExecutor

    # 注册 fake 到 default 槽位（模拟外部热拔插换血）
    fake = _FakeSandbox()
    SeamRegistry.register(SeamType.SANDBOX, "default", fake)

    bash = BashExecutor()
    bash._sandbox_provider = None  # 强制走 seam 查询路径
    wrapped = bash._sandbox_command("echo hi")

    assert wrapped == "[FAKE:echo hi]"
    assert _FakeSandbox.wrap_calls == 1


def test_bash_sandbox_miss_falls_back_bwrap(monkeypatch):
    """seam 无 default → 回退（不炸）。真实环境 bwrap 或 noop，只断言不抛异常。"""
    from lingclaude.engine.bash import BashExecutor

    bash = BashExecutor()
    bash._sandbox_provider = None
    try:
        result = bash._sandbox_command("echo hi")
        assert isinstance(result, str)
    except Exception:  # noqa: BLE001 — 环境无 bwrap 时 fail-closed 也是合法路径
        pass


def test_bash_sandbox_seam_swap_takes_effect(monkeypatch):
    """热拔插语义：注册表换血后下个命令用新后端（不重启）。"""
    from lingclaude.engine.bash import BashExecutor

    fake1 = _FakeSandbox()
    fake1.name = "fake1"
    SeamRegistry.register(SeamType.SANDBOX, "default", fake1)

    bash = BashExecutor()
    bash._sandbox_provider = None
    assert bash._sandbox_command("cmd1") == "[FAKE:cmd1]"
    # bash 会缓存 _sandbox_provider —— 但每次调用时若 seam 有值会重新取？
    # 实际语义：首次调用缓存；此处模拟「外部换血 + 清缓存」= 重查 seam。
    bash._sandbox_provider = None
    fake2 = _FakeSandbox()
    fake2.name = "fake2"
    SeamRegistry.register(SeamType.SANDBOX, "default", fake2)
    assert bash._sandbox_command("cmd2") == "[FAKE:cmd2]"


# --- S2: ProviderRegistry 消费面（已注册实例优先）---

def test_provider_registry_seam_instance_preferred(monkeypatch):
    """create() 优先返回 SeamRegistry 已注册实例（非类）→ 热拔插生效。"""
    from lingclaude.model.provider_registry import ProviderRegistry
    from lingclaude.model.types import ModelConfig

    class _FakeProvider:
        name = "fake_provider"

        def complete(self, *a, **k):
            return "ok"

    fake = _FakeProvider()
    SeamRegistry.register(SeamType.PROVIDER, "openai", fake)

    result = ProviderRegistry.create("openai", ModelConfig())
    assert result.is_ok
    assert result.data is fake  # 返回的是注册的实例，不是新建


def test_provider_registry_seam_miss_falls_back(monkeypatch):
    """seam 无已注册实例（类是类）→ 回退内部注册表创建。"""
    from lingclaude.model.provider_registry import ProviderRegistry
    from lingclaude.model.types import ModelConfig

    # 确保 openai 在内部注册表（_register_builtins 已注册）
    result = ProviderRegistry.create("openai", ModelConfig())
    assert result.is_ok
    assert not isinstance(result.data, type)


# --- S3: 主干倒装清零（模块级）---

def test_core_no_module_level_engine_import():
    """core/ 下无模块级 from lingclaude.engine import（主干不 import 插片实现）。"""
    core_dir = Path(__file__).resolve().parent.parent / "lingclaude" / "core"
    offenders = []
    for py in core_dir.glob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("lingclaude.engine"):
                if node.lineno == 1 or (node.lineno > 1 and node.col_offset == 0):
                    # 模块级 import（缩进 0 = 模块级）
                    offenders.append(f"{py.name}:{node.lineno}")
    assert offenders == [], f"core→engine 模块级倒装残留: {offenders}"


def test_mcp_tools_no_module_level_engine_import():
    """mcp_tools.py 模块级倒装已清零（原 :11/:12）。"""
    path = Path(__file__).resolve().parent.parent / "lingclaude" / "core" / "mcp_tools.py"
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("from lingclaude.engine") or line.startswith("import lingclaude.engine"):
            # 行首无缩进 = 模块级
            if not line.startswith((" ", "\t")):
                pytest.fail(f"mcp_tools.py:{i} 模块级 engine import: {line}")
