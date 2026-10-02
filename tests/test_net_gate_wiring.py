"""M 期接线测试：execution_domain 网络闸 → llm_proxy httpx 强制点（2026-10-02）。

覆盖面（对齐 .atomcode/M_wiring_plan.md 四条语义）：
  1. gate 单元三向：None(未激活)→True / True→True / False→False
  2. gate 永不 raise：net_allowed 抛异常 → fail-open 放行
  3. call() 端到端：激活+域外 → status="net_blocked"，client 零接触
  4. call() 放行路径：激活+域内 → 真实触达 client.post
  5. call_stream() deny → 单个 error 块，且置于重试循环外（单块非双块）
  6. peer fallback deny → {"error": "net_blocked"} dict 契约
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import httpx
import pytest

from lingclaude.core import execution_domain as ed
import lingclaude.model.llm_proxy.provider_pool as pp
from lingclaude.model.llm_proxy.provider_pool import ProviderPool


# ---------------------------------------------------------------- helpers

def _activate(monkeypatch, allow: list[str], scope: str = "/home/ai/lingclaude"):
    """激活 net_allowlist（走 ed 真实解析链）。"""
    def _load():
        return {"net_allowlist": {"rules": [{"scope": scope, "allow": allow}]}}
    monkeypatch.setattr(ed, "_load_policy", _load)


def _deactivate(monkeypatch):
    monkeypatch.setattr(ed, "_load_policy", lambda: {})


# ------------------------------------------------------------ 1-2. gate 单元

def test_gate_three_verdicts(monkeypatch):
    cases = [(None, True), (True, True), (False, False)]
    for verdict, expect in cases:
        monkeypatch.setattr(pp, "net_allowed", lambda u, v=verdict: v)
        assert pp._net_gate("https://api.example.com/x") is expect


def test_gate_never_raises(monkeypatch):
    def _boom(url):
        raise RuntimeError("guard exploded")
    monkeypatch.setattr(pp, "net_allowed", _boom)
    assert pp._net_gate("https://api.example.com/x") is True  # fail-open


# ------------------------------------------------------------ 3-4. call 端到端

def test_call_denied_out_of_domain(monkeypatch):
    _activate(monkeypatch, ["api.github.com"])

    async def _post_never(*a, **k):  # pragma: no cover — 被拦不应到达
        raise AssertionError("client.post must not be reached when denied")

    pool = ProviderPool()
    monkeypatch.setattr(pool._client, "post", _post_never)
    resp = asyncio.run(pool.call(
        api_key="test-key", base_url="https://evil.example.com",
        model="m", messages=[{"role": "user", "content": "hi"}],
    ))
    assert resp.status == "net_blocked"
    assert resp.content == "" and resp.input_tokens == 0


def test_call_allowed_in_domain_reaches_client(monkeypatch):
    _activate(monkeypatch, ["api.github.com"])

    async def _fake_post(self, url, **kwargs):
        class _R:
            status_code = 200
            text = ""
            def json(self):
                return {"model": "m", "choices": [
                    {"message": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {}}
        return _R()

    pool = ProviderPool()
    monkeypatch.setattr(type(pool._client), "post", _fake_post)
    resp = asyncio.run(pool.call(
        api_key="test-key", base_url="https://api.github.com",
        model="m", messages=[{"role": "user", "content": "hi"}],
    ))
    assert resp.status == "ok" and resp.content == "ok"


# ------------------------------------------------------------ 5. stream deny

def test_stream_denied_single_error_chunk(monkeypatch):
    _activate(monkeypatch, ["api.github.com"])
    pool = ProviderPool()

    async def _collect():
        chunks = []
        async for ch in pool.call_stream(
            api_key="k", base_url="https://evil.example.com",
            model="m", messages=[{"role": "user", "content": "hi"}],
        ):
            chunks.append(ch)
        return chunks

    chunks = asyncio.run(_collect())
    assert len(chunks) == 1  # 循环外单块——重试不改变确定性裁决
    assert chunks[0].finish_reason == "error" and chunks[0].delta == ""


# ------------------------------------------------------------ 6. peer fallback

def test_peer_fallback_denied_dict_contract(monkeypatch):
    _activate(monkeypatch, ["api.github.com"])
    from lingclaude.model.llm_proxy.proxy import LLMProxy
    proxy_obj = LLMProxy.__new__(LLMProxy)  # 绕开配置加载，仅测闸逻辑
    proxy_obj._peer_url = "https://peer.example.com"  # deny 先于 URL 构造也需属性在

    result = asyncio.run(proxy_obj._call_peer(
        messages=[{"role": "user", "content": "hi"}],
        purpose="test", caller="unit",
    ))
    assert result["error"] == "net_blocked"


# ------------------------------------------------------------ 回归：未激活零行为

def test_inactive_no_behavior_change(monkeypatch):
    _deactivate(monkeypatch)

    called = {}
    async def _fake_post(self, url, **kwargs):
        called["url"] = url

        class _R:
            status_code = 200
            text = ""
            def json(self):
                return {"model": "m", "choices": [
                    {"message": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {}}
        return _R()

    pool = ProviderPool()
    monkeypatch.setattr(type(pool._client), "post", _fake_post)
    resp = asyncio.run(pool.call(
        api_key="k", base_url="https://any.example.com",
        model="m", messages=[{"role": "user", "content": "hi"}],
    ))
    assert resp.status == "ok" and called["url"].startswith("https://any.example.com")
