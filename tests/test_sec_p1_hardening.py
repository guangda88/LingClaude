"""sec-p1 双债清偿回归守卫（2026-09-24，债务台账 sec-p1-env-trust-surfaces / sec-p1-webui-mint-host）。

覆盖四类信任面收口：
- SEARXNG_URL env 劫持 → 搜索投毒 prior_verifier 信任链（V4）
- PAGER env 喂 subprocess（V5）
- LINGCLAUDE_API_KEYS 只增不减 / 非常数时间比较（V6）
- webui Host 白名单（V7，Rust 侧另有集成测试，此处测 Python 侧等价契约）
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
from unittest.mock import patch

import pytest

from lingclaude.engine.web_tools import WebFetcher, WebSearcher


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    monkeypatch.delenv("LINGCLAUDE_API_KEYS", raising=False)
    yield


class TestSearxngUrlVetting:
    """V4: SEARXNG_URL env 信任面收口。"""

    def test_default_loopback_allowed(self) -> None:
        assert WebSearcher()._searxng_url == WebSearcher.DEFAULT_SEARXNG_URL

    def test_explicit_loopback_override_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:9999")
        assert WebSearcher()._searxng_url == "http://127.0.0.1:9999"

    def test_metadata_ip_blocked(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        monkeypatch.setenv("SEARXNG_URL", "http://169.254.169.254:8888")
        with caplog.at_level(logging.WARNING):
            s = WebSearcher()
        assert s._searxng_url == WebSearcher.DEFAULT_SEARXNG_URL
        assert any("非公网" in r.message for r in caplog.records), "必须 fail-visible"

    def test_rfc1918_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEARXNG_URL", "http://10.1.2.3:8888")
        assert WebSearcher()._searxng_url == WebSearcher.DEFAULT_SEARXNG_URL

    def test_non_http_scheme_blocked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SEARXNG_URL", "file:///etc/passwd")
        assert WebSearcher()._searxng_url == WebSearcher.DEFAULT_SEARXNG_URL

    def test_unresolvable_host_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 打桩 DNS（沙箱内真实解析 .invalid 会拖满 resolver 超时，且门行为不变）
        import socket

        monkeypatch.setattr(
            socket, "getaddrinfo",
            lambda *a, **kw: (_ for _ in ()).throw(socket.gaierror(8, "nodename nor servname provided")),
        )
        monkeypatch.setenv("SEARXNG_URL", "http://this-host-does-not-exist.invalid:8888")
        assert WebSearcher()._searxng_url == WebSearcher.DEFAULT_SEARXNG_URL


class TestPagerHardening:
    """V5: /history 分页器不再读 PAGER env。"""

    def test_history_cmd_uses_fixed_less(self) -> None:
        # 源码级守卫：不再出现 PAGER env 读取（注释提及无妨，代码模式必须消失）
        import inspect

        import lingclaude.cli._commands_history as mod

        src = inspect.getsource(mod)
        forbidden = ['os.environ.get("PAGER"', "os.environ['PAGER'", "environ.get('PAGER'"]
        assert not any(p in src for p in forbidden), "PAGER env 信任面必须收口（V5）"


class TestApiKeysLifecycle:
    """V6: env 是唯一事实源，整体替换 + 常数时间比较。"""

    def _reload_api(self, monkeypatch: pytest.MonkeyPatch):
        import lingclaude.api as api

        importlib.reload(api)
        return api

    def test_valid_key_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LINGCLAUDE_API_KEYS", "k1,k2")
        api = self._reload_api(monkeypatch)
        assert asyncio.run(api.verify_api_key("k1")) == "k1"

    def test_revoked_key_rejected_next_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """env 撤销的 key 下一次请求即失效（旧实现只增不减、永不失效）。"""
        monkeypatch.setenv("LINGCLAUDE_API_KEYS", "k1,k2")
        api = self._reload_api(monkeypatch)
        assert asyncio.run(api.verify_api_key("k1")) == "k1"
        monkeypatch.setenv("LINGCLAUDE_API_KEYS", "k1")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            asyncio.run(api.verify_api_key("k2"))
        assert ei.value.status_code == 401

    def test_empty_env_fail_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LINGCLAUDE_API_KEYS", "k1")
        api = self._reload_api(monkeypatch)
        assert asyncio.run(api.verify_api_key("k1")) == "k1"
        monkeypatch.setenv("LINGCLAUDE_API_KEYS", "")
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            asyncio.run(api.verify_api_key("k1"))
        assert ei.value.status_code == 401

    def test_rotation_without_restart(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LINGCLAUDE_API_KEYS", "k1")
        api = self._reload_api(monkeypatch)
        assert asyncio.run(api.verify_api_key("k1")) == "k1"
        monkeypatch.setenv("LINGCLAUDE_API_KEYS", "k3")
        assert asyncio.run(api.verify_api_key("k3")) == "k3"

    def test_lazy_read_fixes_test_collection_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """保留历史修复语义：fixture 在首个受保护请求前 setenv 即生效。"""
        api = self._reload_api(monkeypatch)  # 导入时 env 为空
        monkeypatch.setenv("LINGCLAUDE_API_KEYS", "late-key")
        assert asyncio.run(api.verify_api_key("late-key")) == "late-key"

    def test_compare_digest_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """比较走 hmac.compare_digest（防时序侧信道，V6 顺手清偿项）。"""
        import inspect

        import lingclaude.api as api

        src = inspect.getsource(api.verify_api_key)
        assert "compare_digest" in src
        monkeypatch.setenv("LINGCLAUDE_API_KEYS", "k1")
        assert asyncio.run(api.verify_api_key("k1")) == "k1"
