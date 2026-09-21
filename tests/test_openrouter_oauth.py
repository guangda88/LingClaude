# tests/test_openrouter_oauth.py
"""openrouter_oauth 插片测试（E7：先测后码纪律）。

覆盖：PKCE 对生成（RFC 7636 形状）、auth URL 构造、exchange fail-closed
（网络故障不假成功）、凭据仓 0600 + 惰性注入 + 注销、免费模型合并幂等。
全程离线（urllib 层用 monkeypatch 替换，不打真网；回调服务器用真端口）。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

from lingclaude.model import openrouter_oauth as orx


# ── PKCE 形状 ──

def test_pkce_pair_shape():
    verifier, challenge = orx.make_pkce_pair()
    # RFC 7636: verifier 43-128 字符，unreserved 字符集
    assert 43 <= len(verifier) <= 128
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    assert set(verifier) <= allowed
    # challenge = BASE64URL(SHA256(verifier))，S256 无填充
    expect = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    assert challenge == expect


def test_pkce_pair_unique():
    a, b = orx.make_pkce_pair(), orx.make_pkce_pair()
    assert a[0] != b[0]


def test_auth_url_contains_contract_params():
    _, challenge = orx.make_pkce_pair()
    url = orx.build_auth_url(challenge, 45678)
    assert url.startswith("https://openrouter.ai/auth?")
    assert f"code_challenge={challenge}" in url
    assert "code_challenge_method=S256" in url
    assert "callback_url=http%3A%2F%2F127.0.0.1%3A45678%2Fcallback" in url


# ── exchange fail-closed ──

def test_exchange_network_error_is_fail_closed(monkeypatch):
    def boom(req, timeout):
        raise OSError("network down")
    monkeypatch.setattr(orx.urllib.request, "urlopen", boom)
    r = orx.exchange_code("some-code", "some-verifier")
    assert r.ok is False and "network down" in r.error


def test_exchange_http_error_contains_detail(monkeypatch):
    class FakeErr(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("url", 400, "bad", {}, None)

    def fake(req, timeout):
        raise FakeErr()
    # HTTPError.read 需要 body —— 换个更直接的：urlopen 抛带 body 的 HTTPError
    import io
    err = urllib.error.HTTPError("url", 400, "Bad Request", {},
                                 io.BytesIO(b'{"error":"invalid_code"}'))
    monkeypatch.setattr(orx.urllib.request, "urlopen", lambda req, timeout: (_ for _ in ()).throw(err))
    r = orx.exchange_code("bad", "v")
    assert r.ok is False and "400" in r.error and "invalid_code" in r.error


def test_exchange_missing_key_field(monkeypatch):
    class R:
        def read(self):
            return json.dumps({"nope": 1}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(orx.urllib.request, "urlopen", lambda req, timeout: R())
    r = orx.exchange_code("c", "v")
    assert r.ok is False and "key" in r.error


def test_exchange_success(monkeypatch):
    class R:
        def read(self):
            return json.dumps({"key": "sk-or-v1-test123"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(orx.urllib.request, "urlopen", lambda req, timeout: R())
    r = orx.exchange_code("c", "v")
    assert r.ok is True and r.key == "sk-or-v1-test123"


# ── 凭据仓 ──

@pytest.fixture()
def creds_home(tmp_path, monkeypatch):
    """凭据仓重定向到临时目录 + 隔离 env。"""
    d = tmp_path / "credentials"
    monkeypatch.setattr(orx, "_CREDENTIALS_DIR", d)
    monkeypatch.setattr(orx, "_KEY_FILE", d / "openrouter_key.json")
    monkeypatch.delenv(orx.ENV_KEY_NAME, raising=False)
    yield d
    monkeypatch.delenv(orx.ENV_KEY_NAME, raising=False)


def test_save_load_key_roundtrip(creds_home):
    p = orx.save_key("sk-or-v1-abc")
    assert p.exists()
    assert orx.load_saved_key() == "sk-or-v1-abc"
    # 0600：owner 读写，组/其他无权限
    assert (p.stat().st_mode & 0o777) == 0o600


def test_load_saved_key_missing_returns_empty(creds_home):
    assert orx.load_saved_key() == ""


def test_ensure_env_key_injects_from_store(creds_home):
    assert orx.ensure_env_key() is False  # 无凭据
    orx.save_key("sk-or-v1-xyz")
    assert orx.ensure_env_key() is True
    assert os.environ[orx.ENV_KEY_NAME] == "sk-or-v1-xyz"


def test_ensure_env_key_keeps_existing(creds_home):
    os.environ[orx.ENV_KEY_NAME] = "already-there"
    assert orx.ensure_env_key() is True  # 不覆盖已有 env


def test_clear_saved_key(creds_home):
    orx.save_key("sk-or-v1-x")
    os.environ[orx.ENV_KEY_NAME] = "sk-or-v1-x"
    assert orx.clear_saved_key() is True
    assert orx.load_saved_key() == ""
    assert orx.ENV_KEY_NAME not in os.environ


# ── 免费模型发现 + 合并 ──

def test_fetch_free_models_parses_and_filters(monkeypatch):
    payload = {"data": [
        {"id": "meta/llama-3:free"},
        {"id": "openai/gpt-4o"},
        {"id": "qwen/qwen-2:free"},
        {"id": "no-id"},          # 非 dict id 缺失 → 跳过
    ]}

    class R:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(orx.urllib.request, "urlopen", lambda req, timeout: R())
    free = orx.fetch_free_models()
    assert free == ["meta/llama-3:free", "qwen/qwen-2:free"]


def test_fetch_free_models_network_fail_returns_none(monkeypatch):
    monkeypatch.setattr(orx.urllib.request, "urlopen",
                        lambda req, timeout: (_ for _ in ()).throw(OSError("down")))
    assert orx.fetch_free_models() is None


def test_merge_free_models_idempotent(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"routing": {"providers": {"openrouter": {
        "api_key": "", "base_url": "", "type": "openai",
        "model": "", "models": ["vendor/kept-not-free"],
    }}}}), encoding="utf-8")
    monkeypatch.setattr(orx, "_LINGCODE_CONFIG", cfg)

    r1 = orx.merge_free_models_into_lingcode(["a/b:free", "c/d:free"])
    assert r1 == {"added": 2, "total": 3}
    # 幂等：重复合并不新增
    r2 = orx.merge_free_models_into_lingcode(["a/b:free", "c/d:free"])
    assert r2 == {"added": 0, "total": 3}
    # 既有非免费条目保留
    data = json.loads(cfg.read_text())
    assert "vendor/kept-not-free" in data["routing"]["providers"]["openrouter"]["models"]


def test_merge_free_models_missing_config_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(orx, "_LINGCODE_CONFIG", tmp_path / "nope.json")
    assert orx.merge_free_models_into_lingcode(["a:free"]) == {"added": 0, "total": 0}


# ── 本地回调服务器（真端口，授权模拟：线程内直接 GET /callback） ──

def test_callback_waiter_roundtrip():
    waiter = orx.CallbackWaiter(auth_url="")
    verifier, challenge = orx.make_pkce_pair()
    waiter.start()
    # start 后用真实端口重构造 URL（authorize() 同款两步）
    waiter.auth_url = orx.build_auth_url(challenge, waiter.port)
    try:
        url = waiter.auth_url
        assert f"127.0.0.1:{waiter.port}/callback" in urllib.parse.unquote(url) or \
               "callback_url=" in url
        # 模拟浏览器回调
        threading.Thread(
            target=lambda: urllib.request.urlopen(
                f"http://127.0.0.1:{waiter.port}/callback?code=AUTHCODE123", timeout=5),
            daemon=True).start()
        waiter.wait(timeout=10)
        assert waiter.code == "AUTHCODE123"
    finally:
        try:
            waiter._server.server_close()
        except Exception:
            pass


def test_callback_waiter_error_path():
    waiter = orx.CallbackWaiter(auth_url="")
    waiter.start()
    try:
        threading.Thread(
            target=lambda: urllib.request.urlopen(
                f"http://127.0.0.1:{waiter.port}/callback?state=x", timeout=5),
            daemon=True).start()
        waiter.wait(timeout=10)
        assert waiter.code is None and waiter.error
    finally:
        try:
            waiter._server.server_close()
        except Exception:
            pass


def test_authorize_flow_with_fake_browser(monkeypatch, creds_home):
    """端到端：起回调 → 假浏览器带 code 回调 → exchange 假成功 → 落盘+env。"""
    monkeypatch.setattr(orx, "exchange_code",
                        lambda code, verifier, timeout=15.0: orx.ExchangeResult(ok=True, key="sk-or-v1-e2e"))
    # authorize 内部轮询 0.2s —— 假浏览器稍等回调端口起来
    import time as _t

    def fake_browser():
        for _ in range(50):
            try:
                if waiter._server and waiter.port:
                    break
            except Exception:
                pass
            _t.sleep(0.05)

    # authorize 是阻塞等回调 5min —— 用线程提前注入 code
    result_holder = {}

    def run_authorize():
        result_holder["r"] = orx.authorize()

    # 先拦住 HTTPServer 构造拿端口：直接跑 authorize，回调由定时器注入
    # authorize() 内部起服务器 → 我们没法提前知道端口 → monkeypatch HTTPServer 记录端口
    ports = []

    orig_server_init = orx.HTTPServer.__init__

    def spy_init(self, addr, handler):
        orig_server_init(self, addr, handler)
        ports.append(self.server_address[1])

    monkeypatch.setattr(orx.HTTPServer, "__init__", spy_init)
    th = threading.Thread(target=run_authorize, daemon=True)
    th.start()
    deadline = _t.monotonic() + 10
    while not ports and _t.monotonic() < deadline:
        _t.sleep(0.05)
    assert ports, "回调服务器未在 10s 内起来"
    urllib.request.urlopen(f"http://127.0.0.1:{ports[0]}/callback?code=E2ECODE", timeout=5)
    th.join(timeout=10)
    r = result_holder.get("r")
    assert r is not None and r.ok is True and r.key == "sk-or-v1-e2e"
    # 落盘 + env 双生效
    assert orx.load_saved_key() == "sk-or-v1-e2e"
    assert os.environ[orx.ENV_KEY_NAME] == "sk-or-v1-e2e"
