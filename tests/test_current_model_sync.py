"""P-fix (2026-10-12): 每轮路由实走名同步 engine._current_model_name。

病根复盘（用户报障：两个 TUI 进程 toolbar 都显示 glm-5.3-flash，实际路由落点
是 mimo-v2.6-pro）：
  - tool_executor._resolve_model_config 每轮经 task_router.resolve() 拿到实走
    ModelConfig，但从不写 engine._current_model_name —— 该属性只有 F12f 降级
    路径（loop_body）写 → toolbar（repl._toolbar_snapshot 优先读它）恒回落
    provider._config.model 启动静态名（config.yaml 的 glm-5.3-flash）。
  - 修复：_resolve_model_config 的两个出口（pinned / 路由实走）同步该属性。
    语义与 wiring.py 对 _current_model_name 的定义一致：「运行时当前生效
    模型名」。context_window._extract_model_name 同源受益（窗口随真实模型）。
"""

from __future__ import annotations

from types import SimpleNamespace


class _FakeRouter:
    """task_router 依赖面桩：resolve 返回可编程的实走配置。"""

    def __init__(self, model: str, route_key: str = "coding"):
        self._model = model
        self._route_key = route_key

    def resolve(self, prompt: str):
        cfg = SimpleNamespace(
            model=self._model,
            api_key="sk-test",
            base_url="https://example.invalid/v1",
        )
        return cfg, self._route_key

    def get_default_provider_name(self) -> str:
        return "default"

    def get_provider_name(self, api_key, base_url) -> str:
        return "routed-provider"


class _FakeEngine:
    """_resolve_model_config 依赖面最小桩。"""

    def __init__(self, routed_model: str = "mimo-v2.6-pro", pinned: bool = False):
        self._current_model_name = ""  # wiring.py 装配初值
        self._model_config = SimpleNamespace(
            model="glm-5.3-flash",
            api_key="sk-cfg",
            base_url="https://cfg.invalid/v1",
            max_tokens=4096,
            temperature=0.7,
        )
        self._task_router = _FakeRouter(routed_model)
        self._model_router = None  # legacy 分支跳过
        self._behavior = SimpleNamespace(total_turns=0)  # >3 行为分支跳过
        self._dementia_detector = SimpleNamespace(
            diagnose=lambda: SimpleNamespace(dementia_index=0.0)
        )
        self._pinned = pinned
        self._pinned_model_config = SimpleNamespace(
            model="k3-256k",
            api_key="sk-pin",
            base_url="https://pin.invalid/v1",
            max_tokens=8192,
            temperature=0.5,
            system_prompt="",
        )

    def is_model_pinned(self) -> bool:
        return self._pinned


def test_routed_model_syncs_current_model_name():
    """路由实走名（mimo-v2.6-pro）落 _current_model_name，而非静态 glm-5.3-flash。"""
    from lingclaude.core.tool_executor import ToolExecutor

    eng = _FakeEngine(routed_model="mimo-v2.6-pro")
    te = ToolExecutor(eng)
    cfg, route_key = te._resolve_model_config("写个函数")
    assert cfg.model == "mimo-v2.6-pro"
    assert route_key == "coding"
    assert eng._current_model_name == "mimo-v2.6-pro", (
        f"_current_model_name 应同步路由实走名，实际: {eng._current_model_name!r}"
    )


def test_current_model_name_updates_across_rounds():
    """每轮跟随路由落点更新（第二轮换模型即换名，不留 stale）。"""
    from lingclaude.core.tool_executor import ToolExecutor

    eng = _FakeEngine(routed_model="mimo-v2.6-pro")
    te = ToolExecutor(eng)
    te._resolve_model_config("第一轮")
    assert eng._current_model_name == "mimo-v2.6-pro"

    eng._task_router._model = "k3-256k"  # 第二轮路由落点变化
    te._resolve_model_config("第二轮")
    assert eng._current_model_name == "k3-256k"


def test_pinned_model_syncs_current_model_name():
    """pin 分支同样同步（显示链 pinned 装饰由 status.py 渲染层负责）。"""
    from lingclaude.core.tool_executor import ToolExecutor

    eng = _FakeEngine(routed_model="mimo-v2.6-pro", pinned=True)
    te = ToolExecutor(eng)
    cfg, route_key = te._resolve_model_config("任意")
    assert cfg.model == "k3-256k"
    assert route_key == "pinned"
    assert eng._current_model_name == "k3-256k"


def test_toolbar_snapshot_prefers_current_model_name():
    """显示链回归：_toolbar_snapshot 优先 _current_model_name（修复后被每轮
    写入真实落点），不再恒显示 provider._config.model 启动静态名。"""
    from lingclaude.cli.status import StatusModel, toolbar_fragments

    s = StatusModel()
    s.model = "glm-5.3-flash"  # 旧行为：round_end 从 provider._config 写入的静态名
    s.runtime_model = "mimo-v2.6-pro"  # 修复后 snapshot 喂入的真实落点
    frag = toolbar_fragments(s)
    text = "".join(t for _, t in frag)
    assert "mimo-v2.6-pro" in text
    assert "glm-5.3-flash" not in text
