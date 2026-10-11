#!/usr/bin/env python3
"""OpenAI-compatible bridge in front of Huawei CodeArts 码道 free models.

The CodeArts web client ("每日1000万免费AI算力" benefit pool) exposes the four
free models (deepseek-v4-flash-0731 / glm-5.3-flash / deepseek-v4-pro-0813 /
deepseek-v4.1-flash) only through its agent API, which is session/SSE based and
authenticated by browser cookies + a rotating `cftk` header — there is no plain
OpenAI endpoint reachable without the internal `opengw` app credential.  This
bridge drives that same agent API so proxy3 free-pool routes can consume the
models.

Endpoints (loopback only):
  GET  /health              -> credential + upstream readiness
  GET  /v1/models           -> the free models (live gateway config, static fallback)
  POST /v1/chat/completions -> OpenAI chat completions (stream and non-stream)

Credential source (in order):
  1. live Chrome over CDP (default http://127.0.0.1:9228) via Network.getAllCookies
     (must be the full set — several required cookies are HttpOnly and invisible
     to document.cookie)
  2. cached file ~/.llm_proxy/codearts_cookies.json (written by this bridge)

Upstream flow (verified 2026-10-09):
  POST   /chat/v1/cloudagent/sessions                       -> {"session_id": ...}
  POST   /chat/codebaseservice/v1/cloudagent/sessions/{id}/messages  (SSE)
  DELETE /chat/v1/cloudagent/sessions/{id}                  -> cleanup
Headers on cloudagent paths need `Agent-Type: CodeBase`; the messages call needs
`maas-type: benefit` to draw from the free daily quota (cost = 0).

Daily claim (verified 2026-10-09): the 10M-token benefit is granted per day and
the official clients call
  POST /chat/codebaseservice/v1/cloudagent/benefit/claim   body {}   (idempotent)
on login.  This bridge calls it automatically — at startup, on the first request
after each local-day rollover, and every CODEARTS_BRIDGE_CLAIM_INTERVAL seconds —
so the pool keeps working without anyone opening the CodeArts client that day.
  GET  /chat/codebaseservice/v1/cloudagent/benefit/tokens/balance   -> quota/used
"""

import json
import os
import random
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("CODEARTS_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CODEARTS_BRIDGE_PORT", "4110"))
API_KEY = os.environ.get("CODEARTS_BRIDGE_KEY", "").strip()
UPSTREAM_BASE = os.environ.get("CODEARTS_UPSTREAM_BASE", "https://devcloud.cn-north-4.huaweicloud.com").rstrip("/")
UPSTREAM_HOST = urllib.parse.urlparse(UPSTREAM_BASE).hostname or "devcloud.cn-north-4.huaweicloud.com"
CDP_URL = os.environ.get("CODEARTS_CDP_URL", "http://127.0.0.1:9228").rstrip("/")
CRED_FILE = os.path.expanduser(os.environ.get("CODEARTS_CRED_FILE", "~/.llm_proxy/codearts_cookies.json"))
UPSTREAM_TIMEOUT = float(os.environ.get("CODEARTS_BRIDGE_TIMEOUT", "180"))
MAX_INFLIGHT = int(os.environ.get("CODEARTS_BRIDGE_MAX_INFLIGHT", "2"))
CRED_TTL = float(os.environ.get("CODEARTS_BRIDGE_CRED_TTL", "300"))
MODEL_CACHE_TTL = float(os.environ.get("CODEARTS_BRIDGE_MODEL_TTL", "600"))
CLAIM_INTERVAL = float(os.environ.get("CODEARTS_BRIDGE_CLAIM_INTERVAL", "21600"))
UA = os.environ.get(
    "CODEARTS_BRIDGE_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
)

STATIC_MODELS = {
    "deepseek-v4-flash-0731": {"context_window": 1048576, "max_tokens": 393216, "sort": 10},
    "glm-5.3-flash": {"context_window": 1048576, "max_tokens": 131072, "sort": 20},
    "deepseek-v4-pro-0813": {"context_window": 1000000, "max_tokens": 393216, "sort": 30},
    "deepseek-v4.1-flash": {"context_window": 1000000, "max_tokens": 384000, "sort": 40},
}

BRIDGE_RULES = (
    "你现在作为一个纯文本补全 API 被另一个程序调用，不是在被人类对话。"
    "不要调用任何工具，不要描述工具，不要声称执行过工具，不要读写文件，不要反问。"
    "下面的对话以纯文本形式给出，历史轮次带 User:/Assistant: 前缀。"
    "只回答最后一条 User: 的内容，之前的内容都只作为上下文。"
    "直接输出答案正文，不要任何前言、不要复述问题、不要输出 markdown 代码围栏以外的解释。"
)

ACTION_TIMEOUT = 60
_slots = threading.Semaphore(MAX_INFLIGHT)

_cred_lock = threading.Lock()
_cred = {"at": 0.0, "cookie": "", "cftk": ""}

_model_lock = threading.Lock()
_model_cache = {"at": 0.0, "models": {}}

_claim_lock = threading.Lock()
_claim = {"at": 0.0, "day": "", "ok": False, "info": {}}


def log(msg):
    sys.stderr.write("[codearts-bridge %s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stderr.flush()


class BridgeError(Exception):
    def __init__(self, status, message, etype="bridge_error"):
        super().__init__(message)
        self.status = status
        self.message = message
        self.etype = etype


# ---------------------------------------------------------------- credentials

def _cdp_cookies():
    """Pull the full cookie jar from the logged-in Chrome over CDP."""
    import websocket  # websocket-client; kept local so the module imports without it

    with urllib.request.urlopen(CDP_URL + "/json/list", timeout=8) as resp:
        targets = json.loads(resp.read().decode())
    pages = [t for t in targets if t.get("type") == "page" and "webSocketDebuggerUrl" in t]
    pick = next((t for t in pages if UPSTREAM_HOST in (t.get("url") or "")), None)
    if pick is None:
        pick = next((t for t in pages if "/chat" in (t.get("url") or "")), None) or (pages[0] if pages else None)
    if pick is None:
        raise BridgeError(503, "no CDP page target available on %s" % CDP_URL, etype="cred_unavailable")
    ws = websocket.create_connection(
        pick["webSocketDebuggerUrl"], timeout=8, suppress_origin=True, enable_multithread=True
    )
    try:
        ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
        deadline = time.time() + 8
        while time.time() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                return msg.get("result", {}).get("cookies", []) or []
        raise BridgeError(503, "CDP getAllCookies timed out", etype="cred_unavailable")
    finally:
        ws.close()


def _build_cookie_header(cookies):
    host = UPSTREAM_HOST

    def match(c):
        d = (c.get("domain") or "").lstrip(".")
        return bool(d) and (host == d or host.endswith("." + d))

    best = {}
    for c in cookies:
        if not match(c):
            continue
        name = c.get("name")
        if not name:
            continue
        if name not in best or len(c.get("domain") or "") > len(best[name].get("domain") or ""):
            best[name] = c
    header = "; ".join("%s=%s" % (n, c.get("value", "")) for n, c in best.items())
    cftk = (best.get("devclouddevuibjtcftk") or {}).get("value", "")
    return header, cftk


def _read_cred_file():
    try:
        with open(CRED_FILE) as f:
            data = json.load(f)
        if data.get("cookie") and data.get("cftk"):
            return data["cookie"], data["cftk"]
    except (OSError, ValueError):
        pass
    return "", ""


def _write_cred_file(cookie, cftk):
    try:
        os.makedirs(os.path.dirname(CRED_FILE), exist_ok=True)
        tmp = CRED_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"cookie": cookie, "cftk": cftk, "ts": int(time.time())}, f)
        os.replace(tmp, CRED_FILE)
    except OSError as exc:
        log("warn: cred file write failed: %s" % exc)


def get_credentials(force=False):
    now = time.time()
    with _cred_lock:
        fresh = _cred["cookie"] and _cred["cftk"] and (now - _cred["at"] < CRED_TTL)
        if fresh and not force:
            return _cred["cookie"], _cred["cftk"]
    cookie, cftk, src = "", "", ""
    try:
        cookie, cftk = _build_cookie_header(_cdp_cookies())
        src = "cdp"
    except Exception as exc:  # noqa: BLE001 - fall back to file
        log("warn: CDP credential fetch failed (%s), trying file" % exc)
    if not (cookie and cftk):
        cookie, cftk = _read_cred_file()
        src = "file" if cookie and cftk else ""
    if not (cookie and cftk):
        with _cred_lock:
            if _cred["cookie"] and _cred["cftk"]:
                return _cred["cookie"], _cred["cftk"]  # stale but better than nothing
        raise BridgeError(503, "no CodeArts credentials (CDP %s + %s)" % (CDP_URL, CRED_FILE), etype="cred_unavailable")
    with _cred_lock:
        _cred.update({"at": now, "cookie": cookie, "cftk": cftk})
    if src == "cdp":
        _write_cred_file(cookie, cftk)
    log("credentials refreshed via %s (cookie=%d chars, cftk=%s)" % (src, len(cookie), cftk[:8]))
    return cookie, cftk


def _cred_refresher():
    while True:
        time.sleep(CRED_TTL)
        try:
            get_credentials(force=True)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- daily benefit

_BALANCE_PATH = "/chat/codebaseservice/v1/cloudagent/benefit/tokens/balance"
_CLAIM_PATH = "/chat/codebaseservice/v1/cloudagent/benefit/claim"


def benefit_balance(cookie, cftk):
    data = _json("GET", _BALANCE_PATH, cookie, cftk, timeout=15)
    return data.get("result") or {}


def claim_benefit(force=False):
    """Claim today's 10M free-token benefit (idempotent POST {}).

    Runs at most once per CLAIM_INTERVAL, plus once on the first call of a new
    local day (so the pool self-heals across midnight without a client login).
    Returns (ok, info) where info carries the latest quota snapshot.
    """
    now = time.time()
    today = time.strftime("%Y%m%d")
    with _claim_lock:
        fresh = (
            _claim["at"] and _claim["day"] == today and (now - _claim["at"] < CLAIM_INTERVAL)
        )
        if fresh and not force:
            return _claim["ok"], _claim["info"]
    ok, info = False, {}
    try:
        cookie, cftk = get_credentials()
        data = _json("POST", _CLAIM_PATH, cookie, cftk, {}, timeout=20)
        ok = data.get("error_code") == "0000" or data.get("code") == 0
        info = dict(data.get("result") or {})
        try:
            info.update(benefit_balance(cookie, cftk))
        except Exception:  # noqa: BLE001 - balance is best-effort decoration
            pass
        log(
            "benefit claim %s: day=%s used=%s/%s balance=%s"
            % (ok and "ok" or "failed", today, info.get("daily_tokens_used"),
               info.get("daily_token_limit"), info.get("total_balance"))
        )
    except Exception as exc:  # noqa: BLE001
        log("warn: benefit claim failed: %s" % exc)
    with _claim_lock:
        _claim.update({"at": now, "day": today, "ok": ok, "info": info})
    return ok, info


def _claim_refresher():
    while True:
        time.sleep(CLAIM_INTERVAL)
        try:
            claim_benefit(force=True)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- upstream api

def _headers(cookie, cftk, accept="application/json"):
    return {
        "Cookie": cookie,
        "cftk": cftk,
        "x-requested-with": "XMLHttpRequest",
        "x-codearts-doer-scenario-type": "web",
        "x-auth-token": "",
        "Content-Type": "application/json",
        "Accept": accept,
        "Referer": UPSTREAM_BASE + "/chat/home",
        "User-Agent": UA,
    }


def _http(method, path, cookie, cftk, payload=None, accept="application/json", timeout=ACTION_TIMEOUT, agent_type=None, extra_headers=None):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
    headers = _headers(cookie, cftk, accept)
    if agent_type:
        headers["Agent-Type"] = agent_type
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(UPSTREAM_BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw


def _json(method, path, cookie, cftk, payload=None, accept="application/json", timeout=ACTION_TIMEOUT, agent_type=None):
    raw = _http(method, path, cookie, cftk, payload, accept, timeout, agent_type=agent_type)
    if not raw:
        return {}
    try:
        return json.loads(raw.decode())
    except ValueError:
        raise BridgeError(502, "upstream %s %s returned non-JSON" % (method, path))


def gateway_models(force=False):
    now = time.time()
    with _model_lock:
        if not force and _model_cache["models"] and now - _model_cache["at"] < MODEL_CACHE_TTL:
            return _model_cache["models"]
    models = {}
    try:
        cookie, cftk = get_credentials()
        data = _json("GET", "/chat/codebaseservice/v1/cloudagent/benefit/gateway/config", cookie, cftk, timeout=20)
        for m in (data.get("result", {}) or {}).get("models", []) or []:
            mid = m.get("model_id") or m.get("modelId")
            if mid:
                models[mid] = {
                    "context_window": m.get("context_window"),
                    "max_tokens": m.get("max_tokens"),
                    "sort": m.get("sort", 99),
                }
    except Exception as exc:  # noqa: BLE001
        log("warn: gateway config fetch failed (%s), using static list" % exc)
    if not models:
        models = dict(STATIC_MODELS)
    with _model_lock:
        _model_cache.update({"at": now, "models": models})
    return models


# ---------------------------------------------------------------- completion


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("text", "input_text") and isinstance(part.get("text"), str):
                chunks.append(part["text"])
            elif isinstance(part.get("content"), str):
                chunks.append(part["content"])
        return "\n".join(c for c in chunks if c)
    return "" if content is None else str(content)


def build_prompt(messages):
    if not isinstance(messages, list) or not messages:
        raise BridgeError(400, "'messages' must be a non-empty array")
    systems, turns = [], []
    for msg in messages:
        if not isinstance(msg, dict):
            raise BridgeError(400, "each message must be an object")
        role = msg.get("role")
        body = text_of(msg.get("content"))
        if role in ("system", "developer"):
            if body:
                systems.append(body)
        elif role in ("user", "assistant", "tool"):
            if body:
                turns.append((role, body))
        else:
            raise BridgeError(400, "unsupported message role %r" % role)
    if not turns or turns[-1][0] != "user":
        raise BridgeError(400, "last message must have role 'user'")
    lines = []
    for role, body in turns:
        label = {"user": "User", "assistant": "Assistant", "tool": "Tool"}[role]
        lines.append("%s: %s" % (label, body))
    header = "\n\n".join(systems + [BRIDGE_RULES])
    return header, "\n\n".join(lines)


def parse_sse(raw_text):
    """Return (content, usage, finish_reason) from a CodeArts SSE body."""
    event = None
    content, usage, reason = [], {}, "stop"
    for line in raw_text.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            if not line.strip():
                event = None
            continue
        payload = line[5:].strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        if event == "message":
            piece = data.get("content")
            if isinstance(piece, str) and piece:
                content.append(piece)
        elif event == "done":
            usage = data.get("tokens") or {}
            reason = data.get("reason") or reason
        elif event == "error":
            raise BridgeError(502, "upstream error: %s" % (data.get("message") or data), etype="upstream_model_error")
    return "".join(content).strip(), usage, reason


def complete(mid, header, transcript):
    cookie, cftk = get_credentials()
    try:
        claim_benefit()  # no-op unless CLAIM_INTERVAL elapsed or a new local day
    except Exception:  # noqa: BLE001 - never block a completion on claim
        pass
    cookie, cftk = get_credentials()
    created = _json("POST", "/chat/v1/cloudagent/sessions", cookie, cftk, {}, timeout=30, agent_type="CodeBase")
    sid = (created.get("result") or {}).get("session_id")
    if not sid:
        raise BridgeError(502, "upstream session creation returned no id: %s" % json.dumps(created)[:200])

    body = {
        "content": (header + "\n\n" + transcript).strip(),
        "model_id": mid,
        "repos": [],
    }
    path = "/chat/codebaseservice/v1/cloudagent/sessions/%s/messages" % sid
    result, errors = [], []

    def call():
        try:
            raw = _http("POST", path, cookie, cftk, body, accept="text/event-stream",
                        timeout=UPSTREAM_TIMEOUT, extra_headers={"maas-type": "benefit"})
            result.append(parse_sse(raw.decode("utf-8", "replace")))
        except Exception as exc:  # noqa: BLE001 - re-raised on caller thread
            errors.append(exc)

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(UPSTREAM_TIMEOUT)
    try:
        if worker.is_alive():
            try:
                _http("POST", "/chat/v1/cloudagent/sessions/%s/messages/stop" % sid, cookie, cftk, {}, timeout=20)
            except Exception:  # noqa: BLE001
                pass
            worker.join(15)
            raise BridgeError(504, "upstream model call exceeded %ds" % UPSTREAM_TIMEOUT, etype="timeout")
        if errors:
            raise errors[0]
        if not result:
            raise BridgeError(502, "upstream returned no payload")
        content, usage, reason = result[0]
        if not content:
            raise BridgeError(502, "upstream returned an empty completion", etype="empty_completion")
        return completion_envelope(content, usage, reason)
    finally:
        try:
            _http("DELETE", "/chat/v1/cloudagent/sessions/%s" % sid, cookie, cftk, timeout=20, agent_type="CodeBase")
        except Exception as exc:  # noqa: BLE001
            log("warn: failed to delete session %s: %s" % (sid, exc))


def completion_envelope(content, usage, reason):
    cache = usage.get("cache") or {}
    prompt_tokens = int(usage.get("input") or 0) + int(cache.get("read") or 0) + int(cache.get("write") or 0)
    completion_tokens = int(usage.get("output") or 0) + int(usage.get("reasoning") or 0)
    total = int(usage.get("total") or 0) or (prompt_tokens + completion_tokens)
    return {
        "content": content,
        "finish_reason": "length" if reason == "length" else "stop",
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total or (prompt_tokens + completion_tokens),
            "prompt_tokens_details": {"cached_tokens": int(cache.get("read") or 0)},
        },
    }


def chat_completion(body):
    if int(body.get("n") or 1) != 1:
        raise BridgeError(400, "n>1 is not supported by this bridge")
    if body.get("tools"):
        log("warn: client sent %d tool declaration(s); served text-only" % len(body["tools"]))
    mid = (body.get("model") or "").strip()
    if mid.startswith("codearts/"):
        mid = mid[len("codearts/"):]
    if not mid:
        raise BridgeError(400, "missing 'model'")
    catalog = gateway_models()
    if mid not in catalog:
        raise BridgeError(400, "unknown model %r for provider codearts" % mid)
    header, transcript = build_prompt(body.get("messages"))
    if not _slots.acquire(timeout=UPSTREAM_TIMEOUT):
        raise BridgeError(503, "bridge saturated (%d in flight)" % MAX_INFLIGHT, etype="overloaded")
    try:
        return mid, complete(mid, header, transcript)
    finally:
        _slots.release()


def envelope(mid, completion, stream, include_usage):
    cid = "chatcmpl-%s" % "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(24))
    created = int(time.time())
    base = {"id": cid, "created": created, "model": mid}
    if not stream:
        return dict(
            base,
            object="chat.completion",
            choices=[
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": completion["content"]},
                    "finish_reason": completion["finish_reason"],
                }
            ],
            usage=completion["usage"],
        )
    chunks = [
        dict(base, object="chat.completion.chunk", choices=[{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]),
        dict(base, object="chat.completion.chunk", choices=[{"index": 0, "delta": {"content": completion["content"]}, "finish_reason": None}]),
        dict(base, object="chat.completion.chunk", choices=[{"index": 0, "delta": {}, "finish_reason": completion["finish_reason"]}]),
    ]
    if include_usage:
        chunks.append(dict(base, object="chat.completion.chunk", choices=[], usage=completion["usage"]))
    return chunks


# ---------------------------------------------------------------- http server


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "codearts-bridge/1.0"

    def log_message(self, fmt, *args):
        log("%s %s" % (self.address_string(), fmt % args))

    def _send_json(self, status, obj):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_error_json(self, err):
        self._send_json(err.status, {"error": {"message": err.message, "type": err.etype, "code": err.status}})

    def _authorized(self):
        if not API_KEY:
            return True
        return self.headers.get("Authorization", "") == "Bearer %s" % API_KEY

    def do_GET(self):
        try:
            if self.path in ("/health", "/healthz"):
                cookie, cftk = get_credentials()
                models = gateway_models(force="refresh=1" in self.path)
                ready = "ok"
                benefit = {}
                try:
                    benefit = benefit_balance(cookie, cftk)
                except Exception as exc:  # noqa: BLE001
                    ready = "degraded: %s" % exc
                with _claim_lock:
                    claim_state = {"ok": _claim["ok"], "day": _claim["day"], "at": int(_claim["at"] or 0)}
                return self._send_json(200, {
                    "status": ready,
                    "upstream": UPSTREAM_BASE,
                    "cdp": CDP_URL,
                    "models": sorted(models),
                    "model_count": len(models),
                    "benefit": benefit,
                    "claim": claim_state,
                })
            if self.path.startswith("/benefit"):
                ok, info = claim_benefit(force="force=1" in self.path)
                return self._send_json(200, {"claimed": ok, "benefit": info})
            if self.path.startswith("/v1/models"):
                models = gateway_models(force="refresh=1" in self.path)
                data = [
                    {
                        "id": mid,
                        "object": "model",
                        "created": 0,
                        "owned_by": "huawei-codearts-free",
                        "context_window": meta.get("context_window"),
                        "max_tokens": meta.get("max_tokens"),
                    }
                    for mid, meta in sorted(models.items(), key=lambda kv: kv[1].get("sort", 99))
                ]
                return self._send_json(200, {"object": "list", "data": data})
            if self.path == "/":
                return self._send_json(200, {"service": "codearts_bridge", "upstream": UPSTREAM_BASE, "cdp": CDP_URL})
            return self._send_json(404, {"error": {"message": "not found", "type": "not_found"}})
        except BridgeError as err:
            return self._send_error_json(err)
        except Exception as exc:  # noqa: BLE001
            log("unhandled GET error: %r" % (exc,))
            return self._send_json(500, {"error": {"message": repr(exc), "type": "internal"}})

    def do_POST(self):
        if not self._authorized():
            return self._send_json(401, {"error": {"message": "invalid api key", "type": "auth"}})
        if self.path != "/v1/chat/completions":
            return self._send_json(404, {"error": {"message": "not found", "type": "not_found"}})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception as exc:  # noqa: BLE001
            return self._send_json(400, {"error": {"message": "invalid JSON body: %s" % exc, "type": "invalid_request"}})
        try:
            mid, completion = chat_completion(body)
        except BridgeError as err:
            log("completion failed: %s" % err.message)
            return self._send_error_json(err)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            log("upstream HTTP %s: %s" % (exc.code, detail))
            return self._send_json(502, {"error": {"message": "upstream HTTP %s: %s" % (exc.code, detail), "type": "upstream_http"}})
        except Exception as exc:  # noqa: BLE001
            log("unhandled completion error: %r" % (exc,))
            return self._send_json(500, {"error": {"message": repr(exc), "type": "internal"}})
        if not body.get("stream"):
            return self._send_json(200, envelope(mid, completion, False, True))
        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        for chunk in envelope(mid, completion, True, include_usage):
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main():
    if "--check" in sys.argv:
        cookie, cftk = get_credentials(force=True)
        models = gateway_models(force=True)
        print(json.dumps({"cftk": cftk, "cookie_chars": len(cookie), "models": sorted(models)}, indent=2))
        return 0
    threading.Thread(target=_cred_refresher, daemon=True).start()
    threading.Thread(target=_claim_refresher, daemon=True).start()
    try:
        get_credentials(force=True)
    except BridgeError as exc:
        log("warn: startup credential fetch failed: %s" % exc)
    try:
        claim_benefit(force=True)
    except Exception as exc:  # noqa: BLE001
        log("warn: startup benefit claim failed: %s" % exc)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: threading.Thread(target=httpd.shutdown, daemon=True).start())
    log("listening on %s:%d -> %s (cdp %s, key %s)" % (HOST, PORT, UPSTREAM_BASE, CDP_URL, "on" if API_KEY else "off"))
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
        log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
