"""context_window 查表测试（2026-09-28）。"""

from lingclaude.core.context_window import (
    _DEFAULT_FALLBACK,
    known_window,
    register_window,
    resolve_context_window,
)


class _FakeCfg:
    def __init__(self, model="", provider="", context_window_tokens=None, max_budget_tokens=None):
        self.context_window_tokens = context_window_tokens
        self.max_budget_tokens = max_budget_tokens
        self.model = type("M", (), {"model": model, "provider": provider})()


class _FakeEngine:
    def __init__(self, model="", provider="", context_window_tokens=None, max_budget_tokens=None,
                 current_model_name="", provider_cfg_model="", provider_cfg_provider=""):
        self.config = _FakeCfg(model, provider, context_window_tokens, max_budget_tokens)
        self._current_model_name = current_model_name
        self._provider = None
        if provider_cfg_model or provider_cfg_provider:
            self._provider = type("P", (), {
                "_config": type("C", (), {"model": provider_cfg_model, "provider": provider_cfg_provider})()
            })()


def test_model_lookup_wins_over_config():
    """模型查表最高优先（当前生效模型的真实窗口是 toolbar 语义权威）。"""
    eng = _FakeEngine(model="glm-5.3-flash", context_window_tokens=500_000)
    assert resolve_context_window(eng) == 1_000_000  # 查表 1M 覆盖 config 500K


def test_config_fallback_when_lookup_misses():
    """查表未命中时显式配置兜底。"""
    eng = _FakeEngine(model="totally-unknown-model", context_window_tokens=500_000)
    assert resolve_context_window(eng) == 500_000


def test_model_lookup_glm():
    """glm-5.3-flash 查表命中 1M。"""
    eng = _FakeEngine(model="glm-5.3-flash")
    assert resolve_context_window(eng) == 1_000_000


def test_model_lookup_minimax():
    """MiniMax M3.1 查表命中 1M，M2.7 命中 204.8K。"""
    eng = _FakeEngine(model="MiniMax-M3.1-Flash-Preview")
    assert resolve_context_window(eng) == 1_000_000
    eng = _FakeEngine(model="minimax-m2.7")
    assert resolve_context_window(eng) == 204_800


def test_current_model_name_overrides_config():
    """_current_model_name（降级后同步）优先于 config.model.model。"""
    eng = _FakeEngine(model="glm-5.3-flash", current_model_name="minimax-m2.7")
    assert resolve_context_window(eng) == 204_800


def test_provider_fallback():
    """模型名未命中时按 provider 前缀兜底。"""
    eng = _FakeEngine(model="unknown-model-xyz", provider="glm")
    assert resolve_context_window(eng) == 1_000_000


def test_unknown_fallback():
    """模型名+provider 均未命中 → 128K 全局兜底。"""
    eng = _FakeEngine(model="totally-unknown", provider="totally-unknown")
    assert resolve_context_window(eng) == _DEFAULT_FALLBACK


def test_no_model_no_provider():
    """无模型无 provider → 128K。"""
    eng = _FakeEngine()
    assert resolve_context_window(eng) == _DEFAULT_FALLBACK


def test_known_window_api():
    """公开查询接口。"""
    assert known_window("glm-5.3-flash") == 1_000_000
    assert known_window("nonexistent") is None


def test_register_window():
    """动态注册新模型。"""
    register_window("test-model-x", 999_999)
    assert known_window("test-model-x") == 999_999
    eng = _FakeEngine(model="test-model-x")
    assert resolve_context_window(eng) == 999_999


def test_case_insensitive():
    """模型名大小写不敏感。"""
    eng = _FakeEngine(model="GLM-5.3-Flash")
    assert resolve_context_window(eng) == 1_000_000
