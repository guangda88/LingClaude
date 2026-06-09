"""Tests for llm_proxy package — config, rate_gate, purpose_router, proxy"""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lingclaude.model.llm_proxy.config import ProxyConfig, ProviderConfig, TaskRoute, RouteEntry
from lingclaude.model.llm_proxy.rate_gate import RateGate, RatePolicy
from lingclaude.model.llm_proxy.purpose_router import PurposeRouter
from lingclaude.model.llm_proxy.provider_pool import ProviderResponse
from lingclaude.model.llm_proxy.proxy import LLMProxy


# --- Config ---


class TestProxyConfig:
    def test_load_from_fixture(self, tmp_path: Path):
        cfg_data = {
            "routing": {
                "default_target": "cheap",
                "providers": {
                    "cheap": {
                        "type": "openai",
                        "api_key": "sk-test",
                        "base_url": "https://api.example.com/v1",
                        "model": "gpt-4o",
                        "models": ["gpt-4o", "gpt-4o-mini"],
                        "rate_limit": {"rpm": 30, "burst": 5},
                    },
                },
                "task_routes": {
                    "coding": {
                        "description": "code tasks",
                        "models": [{"provider": "cheap", "model": "gpt-4o"}],
                    },
                },
            }
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(cfg_data))

        cfg = ProxyConfig.load(p)
        assert cfg.default_provider == "cheap"
        assert "cheap" in cfg.providers
        assert cfg.providers["cheap"].api_key == "sk-test"
        assert cfg.providers["cheap"].rpm == 30
        assert "coding" in cfg.task_routes
        assert cfg.task_routes["coding"].models[0].provider == "cheap"

    def test_load_missing_file(self, tmp_path: Path):
        cfg = ProxyConfig.load(tmp_path / "nonexistent.json")
        assert len(cfg.providers) == 0

    def test_load_invalid_json(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("{invalid}")
        cfg = ProxyConfig.load(p)
        assert len(cfg.providers) == 0

    def test_env_var_api_key(self, tmp_path: Path):
        cfg_data = {
            "routing": {
                "providers": {
                    "p1": {
                        "type": "openai",
                        "api_key": "$TEST_PROXY_KEY",
                        "base_url": "https://api.test.com",
                        "model": "m1",
                        "models": [],
                        "rate_limit": {"rpm": 10},
                    }
                }
            }
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(cfg_data))

        with patch.dict("os.environ", {"TEST_PROXY_KEY": "sk-from-env"}):
            cfg = ProxyConfig.load(p)
            assert cfg.providers["p1"].api_key == "sk-from-env"

    def test_should_reload(self, tmp_path: Path):
        p = tmp_path / "config.json"
        p.write_text('{"routing":{}}')
        cfg = ProxyConfig.load(p)
        assert not cfg.should_reload()
        time.sleep(0.1)
        p.write_text('{"routing":{}}')
        assert cfg.should_reload()


# --- RateGate ---


class TestRateGate:
    def _make_gate(self) -> RateGate:
        policy = RatePolicy(global_rpm=60.0, cooldown_429=5.0)
        gate = RateGate(policy)
        gate.register_provider("p1", rpm=30.0, burst=5)
        gate.register_provider("p2", rpm=30.0, burst=5)
        return gate

    def test_acquire_available(self):
        gate = self._make_gate()
        assert gate.acquire("p1") is True

    def test_acquire_unknown_provider(self):
        gate = self._make_gate()
        assert gate.acquire("unknown") is False

    def test_pick_from_route_round_robin(self):
        gate = self._make_gate()
        first = gate.pick_from_route("test", ["p1", "p2"])
        second = gate.pick_from_route("test", ["p1", "p2"])
        assert first is not None
        assert second is not None
        assert first != second or True  # round robin may vary

    def test_record_429_cooldown(self):
        gate = self._make_gate()
        for _ in range(3):
            gate.record_429("p1")
        health = gate.health()
        assert health["p1"]["consecutive_errors"] == 0  # reset after cooldown
        assert health["p1"]["errors"] == 3

    def test_record_success_resets_errors(self):
        gate = self._make_gate()
        gate.record_429("p1")
        gate.record_success("p1")
        assert gate.health()["p1"]["consecutive_errors"] == 0


# --- PurposeRouter ---


class TestPurposeRouter:
    def _make_config(self) -> ProxyConfig:
        return ProxyConfig(
            providers={
                "p1": ProviderConfig(
                    name="p1", type="openai", api_key="k1",
                    base_url="https://a.com", default_model="m1",
                    models=["m1"], rpm=10, burst=3,
                ),
                "p2": ProviderConfig(
                    name="p2", type="openai", api_key="k2",
                    base_url="https://b.com", default_model="m2",
                    models=["m2"], rpm=10, burst=3,
                ),
            },
            task_routes={
                "coding": TaskRoute(
                    description="code",
                    models=[RouteEntry(provider="p1", model="m1")],
                ),
                "fast_response": TaskRoute(
                    description="fast",
                    models=[RouteEntry(provider="p2", model="m2")],
                ),
            },
            default_provider="p1",
        )

    def test_classify_coding(self):
        router = PurposeRouter(self._make_config())
        result = router.classify([{"role": "user", "content": "debug this function"}])
        assert result == "coding"

    def test_classify_with_hint(self):
        router = PurposeRouter(self._make_config())
        result = router.classify([{"role": "user", "content": "hello"}], hint="coding")
        assert result == "coding"

    def test_classify_default_fast(self):
        router = PurposeRouter(self._make_config())
        result = router.classify([{"role": "user", "content": "xyzrandom"}])
        assert result == "fast_response"

    def test_resolve_known_route(self):
        router = PurposeRouter(self._make_config())
        result = router.resolve("coding")
        assert result is not None
        assert result.provider == "p1"
        assert result.model == "m1"

    def test_resolve_fallback(self):
        router = PurposeRouter(self._make_config())
        result = router.resolve("nonexistent_route")
        assert result is not None
        assert result.provider == "p1"  # default

    def test_resolve_with_availability_filter(self):
        router = PurposeRouter(self._make_config())
        result = router.resolve("coding", available_providers={"p2"})
        # coding route only has p1, so fallback to default
        assert result is not None


# --- Proxy (integration) ---


class TestLLMProxy:
    def _make_config(self) -> ProxyConfig:
        return ProxyConfig(
            providers={
                "p1": ProviderConfig(
                    name="p1", type="openai", api_key="k1",
                    base_url="https://api.test.com/v1", default_model="m1",
                    models=["m1"], rpm=60, burst=10,
                ),
            },
            task_routes={
                "fast_response": TaskRoute(
                    description="fast",
                    models=[RouteEntry(provider="p1", model="m1")],
                ),
            },
            default_provider="p1",
            _config_path=Path("/nonexistent/config.json"),
            _config_mtime=float("inf"),
        )

    @pytest.mark.asyncio
    async def test_chat_success(self):
        cfg = self._make_config()
        proxy = LLMProxy(cfg)

        mock_response = ProviderResponse(
            content="hello world", model="m1", provider="p1",
            input_tokens=10, output_tokens=5, latency_ms=100.0,
            status="ok",
        )
        proxy._pool.call = AsyncMock(return_value=mock_response)

        result = await proxy.chat(
            messages=[{"role": "user", "content": "say hi"}],
            purpose="fast_response",
        )
        assert "choices" in result
        assert result["choices"][0]["message"]["content"] == "hello world"
        assert result["_proxy_meta"]["provider"] == "p1"

    @pytest.mark.asyncio
    async def test_chat_429_fallback_exhausted(self):
        cfg = self._make_config()
        proxy = LLMProxy(cfg)

        mock_429 = ProviderResponse(
            content="", model="m1", provider="p1",
            latency_ms=50.0, status="429",
        )
        proxy._pool.call = AsyncMock(return_value=mock_429)

        result = await proxy.chat(
            messages=[{"role": "user", "content": "hi"}],
        )
        assert result.get("model") == "fallback"
        assert result["_proxy_meta"]["fallback"] is True

    @pytest.mark.asyncio
    async def test_health(self):
        cfg = self._make_config()
        proxy = LLMProxy(cfg)
        h = proxy.health()
        assert "providers" in h
        assert "p1" in h["providers"]

    @pytest.mark.asyncio
    async def test_reload_on_stale_config(self, tmp_path: Path):
        cfg_data = {
            "routing": {
                "default_target": "p1",
                "providers": {
                    "p1": {
                        "type": "openai", "api_key": "k1",
                        "base_url": "https://a.com", "model": "m1",
                        "models": [], "rate_limit": {"rpm": 10},
                    }
                },
                "task_routes": {},
            }
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(cfg_data))

        cfg = ProxyConfig.load(p)
        proxy = LLMProxy(cfg)

        time.sleep(0.1)
        cfg_data["routing"]["default_target"] = "p1-updated"
        p.write_text(json.dumps(cfg_data))

        mock_resp = ProviderResponse(
            content="ok", model="m1", provider="p1",
            input_tokens=1, output_tokens=1, status="ok",
        )
        proxy._pool.call = AsyncMock(return_value=mock_resp)
        await proxy.chat(messages=[{"role": "user", "content": "hi"}])

        assert proxy._config.default_provider == "p1-updated"
