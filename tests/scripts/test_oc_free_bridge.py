# -*- coding: utf-8 -*-
"""tests/scripts/test_oc_free_bridge.py -- oc_free_bridge.py 契约测试.

只测可脱离上游(opencode serve)独立验证的纯逻辑 + HTTP 层信封:
- 环境自隔离: setUpModule 先设 env 再 import bridge(模块级常量在 import 时读 env)
- 纯函数: build_prompt / text_of / parse_completion / envelope / normalize_model
- HTTP 层: Bearer 认证 / 错误信封 / 上游错误 502 映射(ThreadingHTTPServer 真实端口)
- 上游 mock: 函数级打桩(monkeypatch upstream), 不 mock socket

不改 oc_free_bridge.py 一行 -- 契约以当前实现为准。
"""
import importlib
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# 模块级常量在 import 时固化, 必须先设 env 再 import
os.environ.setdefault("OC_SERVE_URL", "http://127.0.0.1:41999")
os.environ.setdefault("OC_BRIDGE_HOST", "127.0.0.1")
os.environ.setdefault("OC_BRIDGE_PORT", "4100")
os.environ.setdefault("OC_BRIDGE_KEY", "test-key-123")
os.environ.setdefault("OC_BRIDGE_AGENT", "")
os.environ.setdefault("OC_BRIDGE_TIMEOUT", "180")
os.environ.setdefault("OC_BRIDGE_MAX_INFLIGHT", "8")
os.environ.setdefault("OC_BRIDGE_MODEL_TTL", "60")
os.environ.setdefault("OC_BRIDGE_MODEL_ID", "big-pickle")

import importlib.util

_SPEC = importlib.util.spec_from_file_location(
    "oc_free_bridge_under_test",
    os.path.join(REPO_ROOT, "scripts", "oc_free_bridge.py"),
)
bridge = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault("oc_free_bridge_under_test", bridge)
_SPEC.loader.exec_module(bridge)


# ---------------------------------------------------------------------------
# text_of: OpenAI content 三形态归一
# ---------------------------------------------------------------------------

def test_text_of_plain_string():
    assert bridge.text_of("hello") == "hello"


def test_text_of_none_is_empty():
    assert bridge.text_of(None) == ""


def test_text_of_non_dict_parts_skipped():
    content = ["junk", {"type": "text", "text": "a"}, {"type": "input_text", "text": "b"}]
    assert bridge.text_of(content) == "a\nb"


def test_text_of_legacy_content_key():
    content = [{"content": "legacy"}]
    assert bridge.text_of(content) == "legacy"


# ---------------------------------------------------------------------------
# build_prompt: 角色展平 + BRIDGE_RULES 注入
# ---------------------------------------------------------------------------

def _msg(role, text):
    return {"role": role, "content": text}


def test_build_prompt_flattens_roles():
    system, transcript = bridge.build_prompt(
        [
            _msg("system", "be terse"),
            _msg("user", "hi"),
            _msg("assistant", "hello"),
            _msg("user", "final question"),
        ]
    )
    assert "be terse" in system
    assert bridge.BRIDGE_RULES in system
    assert transcript == "User: hi\n\nAssistant: hello\n\nUser: final question"


def test_build_prompt_last_must_be_user():
    with pytest.raises(bridge.BridgeError) as ei:
        bridge.build_prompt([_msg("user", "q"), _msg("assistant", "a")])
    assert ei.value.status == 400


def test_build_prompt_rejects_unknown_role():
    with pytest.raises(bridge.BridgeError) as ei:
        bridge.build_prompt([_msg("function", "x")])
    assert ei.value.status == 400


def test_build_prompt_rejects_empty_messages():
    with pytest.raises(bridge.BridgeError):
        bridge.build_prompt([])


def test_build_prompt_merges_multiple_system_messages():
    system, _ = bridge.build_prompt([_msg("system", "s1"), _msg("system", "s2"), _msg("user", "u")])
    assert "s1" in system and "s2" in system


# ---------------------------------------------------------------------------
# parse_completion: 上游 parts -> OpenAI completion 字段
# ---------------------------------------------------------------------------

def _upstream_result(text, tokens=None, reason="stop", tool_parts=0):
    parts = [{"type": "text", "text": text}]
    parts += [{"type": "tool"}] * tool_parts
    parts.append({"type": "step-finish", "reason": reason})
    return {"info": {"tokens": tokens or {}}, "parts": parts}


def test_parse_completion_basic_usage():
    out = bridge.parse_completion(
        _upstream_result("answer", tokens={"input": 10, "output": 5})
    )
    assert out["content"] == "answer"
    assert out["finish_reason"] == "stop"
    assert out["usage"]["prompt_tokens"] == 10
    assert out["usage"]["completion_tokens"] == 5
    assert out["usage"]["total_tokens"] == 15


def test_parse_completion_tokens_cache_and_reasoning():
    tokens = {
        "input": 10,
        "output": 5,
        "reasoning": 3,
        "cache": {"read": 7, "write": 2},
    }
    usage = bridge.parse_completion(_upstream_result("x", tokens=tokens))["usage"]
    assert usage["prompt_tokens"] == 10 + 7 + 2
    assert usage["completion_tokens"] == 5 + 3
    assert usage["prompt_tokens_details"]["cached_tokens"] == 7


def test_parse_completion_finish_reason_length():
    out = bridge.parse_completion(_upstream_result("x", reason="length"))
    assert out["finish_reason"] == "length"


def test_parse_completion_empty_raises_502():
    with pytest.raises(bridge.BridgeError) as ei:
        bridge.parse_completion(_upstream_result(""))
    assert ei.value.status == 502


def test_parse_completion_upstream_model_error():
    result = {"info": {"error": {"data": {"message": "boom"}}, "tokens": {}}, "parts": []}
    with pytest.raises(bridge.BridgeError) as ei:
        bridge.parse_completion(result)
    assert ei.value.status == 502
    assert ei.value.etype == "upstream_model_error"


# ---------------------------------------------------------------------------
# envelope: 非流式对象 / 流式 chunks / usage chunk
# ---------------------------------------------------------------------------

_COMPLETION = {
    "content": "ok",
    "finish_reason": "stop",
    "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
}


def test_envelope_non_stream_shape():
    env = bridge.envelope("m", _COMPLETION, stream=False, include_usage=True)
    assert env["object"] == "chat.completion"
    assert env["model"] == "m"
    assert env["choices"][0]["message"]["content"] == "ok"
    assert env["choices"][0]["finish_reason"] == "stop"


def test_envelope_stream_three_chunks_plus_usage():
    chunks = bridge.envelope("m", _COMPLETION, stream=True, include_usage=True)
    assert [c["object"] for c in chunks] == ["chat.completion.chunk"] * 4
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert chunks[1]["choices"][0]["delta"]["content"] == "ok"
    assert chunks[2]["choices"][0]["finish_reason"] == "stop"
    assert chunks[3]["choices"] == []
    assert chunks[3]["usage"]["total_tokens"] == 3


def test_envelope_stream_without_usage_chunk():
    chunks = bridge.envelope("m", _COMPLETION, stream=True, include_usage=False)
    assert len(chunks) == 3


# ---------------------------------------------------------------------------
# normalize_model + model_catalog: 打桩 upstream
# ---------------------------------------------------------------------------

def test_normalize_model_strips_prefix(monkeypatch):
    monkeypatch.setattr(
        bridge, "model_catalog", lambda force=False: ({"m1": {}}, {"m1", "m2"})
    )
    assert bridge.normalize_model("opencode/m1") == "m1"


def test_normalize_model_unknown_rejected(monkeypatch):
    monkeypatch.setattr(
        bridge, "model_catalog", lambda force=False: ({"m1": {}}, {"m1"})
    )
    with pytest.raises(bridge.BridgeError) as ei:
        bridge.normalize_model("nope")
    assert ei.value.status == 400


def test_normalize_model_missing_rejected():
    with pytest.raises(bridge.BridgeError):
        bridge.normalize_model("  ")


def _provider_payload(cost_map):
    models = {
        mid: {"cost": {"input": ci, "output": co}, "limit": {"context": 1024}}
        for mid, (ci, co) in cost_map.items()
    }
    return {"providers": [{"id": "opencode", "models": models}]}


def test_model_catalog_free_filter_and_cache(monkeypatch):
    calls = []

    def fake_upstream(path, payload=None, method=None, timeout=None):
        calls.append(path)
        return _provider_payload(
            {"free1": (0, 0), "paid1": (1, 2), "free2": (0, 0)}
        )

    monkeypatch.setattr(bridge, "upstream", fake_upstream)
    with bridge._model_lock:
        bridge._model_cache.update({"at": 0.0, "free": {}, "all": set()})
    free, allids = bridge.model_catalog(force=True)
    assert sorted(free) == ["free1", "free2"]
    assert allids == {"free1", "paid1", "free2"}
    # TTL 内二次调用不触发 upstream
    n = len(calls)
    bridge.model_catalog()
    assert len(calls) == n


def test_model_catalog_skips_zero_cost_entry_only_when_both_zero(monkeypatch):
    def fake_upstream(path, payload=None, method=None, timeout=None):
        return _provider_payload({"half_free": (0, 1), "free": (0, 0)})

    monkeypatch.setattr(bridge, "upstream", fake_upstream)
    with bridge._model_lock:
        bridge._model_cache.update({"at": 0.0, "free": {}, "all": set()})
    free, _ = bridge.model_catalog(force=True)
    assert "half_free" not in free and "free" in free


# ---------------------------------------------------------------------------
# chat_completion: 入口守卫
# ---------------------------------------------------------------------------

def test_chat_completion_rejects_n_gt_1():
    with pytest.raises(bridge.BridgeError) as ei:
        bridge.chat_completion({"n": 2, "model": "m", "messages": [_msg("user", "u")]})
    assert ei.value.status == 400


def test_chat_completion_accepts_tools_but_text_only(monkeypatch):
    # tools 声明不拒绝(仅转纯文本上游), 与 docstring 契约一致
    seen = {}

    def fake_complete(mid, system, transcript):
        seen["transcript"] = transcript
        return "text-only"

    monkeypatch.setattr(bridge, "complete", fake_complete)
    monkeypatch.setattr(
        bridge, "model_catalog", lambda force=False: ({"m1": {}}, {"m1"})
    )
    mid, completion = bridge.chat_completion(
        {
            "model": "m1",
            "tools": [{"type": "function", "function": {"name": "f"}}],
            "messages": [_msg("user", "u")],
        }
    )
    assert mid == "m1" and completion == "text-only"
    assert "tools" not in seen["transcript"]


def test_chat_completion_saturated_returns_503(monkeypatch):
    monkeypatch.setattr(
        bridge, "model_catalog", lambda force=False: ({"m1": {}}, {"m1"})
    )
    # 打桩信号量: acquire 超时失败
    class FullSlots:
        def acquire(self, timeout=None):
            return False

        def release(self):
            pass

    monkeypatch.setattr(bridge, "_slots", FullSlots())
    with pytest.raises(bridge.BridgeError) as ei:
        bridge.chat_completion({"model": "m1", "messages": [_msg("user", "u")]})
    assert ei.value.status == 503
    assert ei.value.etype == "overloaded"


# ---------------------------------------------------------------------------
# complete: 一次性会话生命周期 + 超时 abort
# ---------------------------------------------------------------------------

def test_complete_happy_path_deletes_session(monkeypatch):
    session_calls = []

    def fake_upstream(path, payload=None, method=None, timeout=None):
        if path == "/session":
            return {"id": "ses_1"}
        if path == "/session/ses_1/message":
            return _upstream_result("ok", tokens={"input": 1, "output": 1})
        if path == "/session/ses_1":
            session_calls.append(method)
            return True
        raise AssertionError("unexpected path " + path)

    monkeypatch.setattr(bridge, "upstream", fake_upstream)
    out = bridge.complete("m1", "sys", "User: u")
    assert out["content"] == "ok"
    assert session_calls == ["DELETE"]


def test_complete_no_session_id_raises(monkeypatch):
    monkeypatch.setattr(bridge, "upstream", lambda *a, **k: {})
    with pytest.raises(bridge.BridgeError) as ei:
        bridge.complete("m1", "sys", "User: u")
    assert ei.value.status == 502


def test_complete_timeout_aborts_and_returns_504(monkeypatch):
    unblock = threading.Event()

    def fake_upstream(path, payload=None, method=None, timeout=None):
        if path == "/session":
            return {"id": "ses_2"}
        if path == "/session/ses_2/message":
            # 模拟模型卡死(等待工具审批): 阻塞到被 abort 解锁,
            # 让 join(15) 快速返回 -- 不削弱"首超时判定"路径
            unblock.wait(30)
            return {}
        if path == "/session/ses_2/abort":
            unblock.set()
            return True
        if path == "/session/ses_2":
            return True
        raise AssertionError(path)

    monkeypatch.setattr(bridge, "upstream", fake_upstream)
    monkeypatch.setattr(bridge, "UPSTREAM_TIMEOUT", 0.1)
    with pytest.raises(bridge.BridgeError) as ei:
        bridge.complete("m1", "sys", "User: u")
    assert ei.value.status == 504
    assert ei.value.etype == "timeout"


# ---------------------------------------------------------------------------
# HTTP 层: Bearer 认证 + 错误信封(真实端口, 上游打桩)
# ---------------------------------------------------------------------------

@pytest.fixture()
def http_server(monkeypatch):
    monkeypatch.setattr(bridge, "SERVE_URL", "http://127.0.0.1:41999")

    class _S(ThreadingHTTPServer):
        daemon_threads = True

    srv = _S(("127.0.0.1", 0), bridge.Handler)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    yield base
    srv.shutdown()
    srv.server_close()


def _post(base, body, key=bridge.API_KEY, path="/v1/chat/completions"):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if key:
        req.add_header("Authorization", "Bearer %s" % key)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_http_auth_rejects_wrong_key(http_server):
    status, body = _post(http_server, {"model": "m"}, key="wrong")
    assert status == 401
    assert body["error"]["type"] == "auth"


def test_http_404_unknown_path(http_server):
    status, body = _post(http_server, {}, path="/nope")
    assert status == 404
    assert body["error"]["type"] == "not_found"


def test_http_invalid_json_400(http_server, monkeypatch):
    req = urllib.request.Request(
        http_server + "/v1/chat/completions",
        data=b"{not-json",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + bridge.API_KEY},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=10)
    assert ei.value.code == 400


def test_http_upstream_error_maps_502(http_server, monkeypatch):
    monkeypatch.setattr(bridge, "API_KEY", "")  # 免认证

    def fake_upstream(path, payload=None, method=None, timeout=None):
        raise bridge.BridgeError(502, "upstream exploded", etype="upstream_error")

    monkeypatch.setattr(bridge, "upstream", fake_upstream)
    with bridge._model_lock:
        bridge._model_cache.update({"at": 0.0, "free": {"m1": {}}, "all": {"m1"}})
    status, body = _post(
        http_server, {"model": "m1", "messages": [_msg("user", "u")]}, key=None
    )
    assert status == 502
    assert body["error"]["type"] == "upstream_error"
    assert body["error"]["code"] == 502


def test_http_get_health_and_models(http_server, monkeypatch):
    monkeypatch.setattr(bridge, "API_KEY", "")

    def fake_upstream(path, payload=None, method=None, timeout=None):
        if path == "/global/health":
            return {"healthy": True, "version": "1.18.29"}
        return _provider_payload({"m1": (0, 0)})

    monkeypatch.setattr(bridge, "upstream", fake_upstream)
    with bridge._model_lock:
        bridge._model_cache.update({"at": 0.0, "free": {}, "all": set()})

    with urllib.request.urlopen(http_server + "/health", timeout=10) as resp:
        health = json.loads(resp.read())
    assert health["status"] == "ok"
    assert health["serve_version"] == "1.18.29"

    with urllib.request.urlopen(http_server + "/v1/models", timeout=10) as resp:
        models = json.loads(resp.read())
    assert [m["id"] for m in models["data"]] == ["m1"]
    assert models["data"][0]["owned_by"] == "opencode-zen-free"
    assert models["data"][0]["context_window"] == 1024
