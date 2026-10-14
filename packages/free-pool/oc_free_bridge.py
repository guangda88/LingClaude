#!/usr/bin/env python3
"""OpenAI-compatible bridge in front of `opencode serve`.

OpenCode Zen's free tier rejects any request that does not come from a real
OpenCode client, so proxy3 cannot talk to https://opencode.ai/zen/v1 directly.
`opencode serve` passes the gate; this bridge translates the OpenAI Chat
Completions contract onto serve's session/message API so free-pool routes can
use the Zen free models.

Endpoints (bound to loopback only):
  GET  /health              -> bridge + upstream health
  GET  /v1/models           -> free Zen models discovered from upstream config
  POST /v1/chat/completions -> OpenAI chat completions (stream and non-stream)

Upstream contract notes (verified against opencode 1.18.29):
  * POST /session                                   -> {"id": "ses_..."}
  * POST /session/{id}/message                      -> {info:{tokens,error}, parts:[...]}
  * DELETE /session/{id}                            -> true
  * GET  /config/providers                          -> per-model cost, used to find free models
  * Passing a "tools" map in the message body makes the free tier return 403,
    so tools are never sent; the serving agent denies them via config instead.
"""

import json
import os
import random
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVE_URL = os.environ.get("OC_SERVE_URL", "http://127.0.0.1:4099").rstrip("/")
HOST = os.environ.get("OC_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("OC_BRIDGE_PORT", "4100"))
API_KEY = os.environ.get("OC_BRIDGE_KEY", "").strip()
AGENT = os.environ.get("OC_BRIDGE_AGENT", "").strip()
UPSTREAM_TIMEOUT = float(os.environ.get("OC_BRIDGE_TIMEOUT", "180"))
MAX_INFLIGHT = int(os.environ.get("OC_BRIDGE_MAX_INFLIGHT", "4"))
MODEL_CACHE_TTL = float(os.environ.get("OC_BRIDGE_MODEL_TTL", "60"))
MODEL_ID = os.environ.get("OC_BRIDGE_MODEL_ID", "big-pickle")

BRIDGE_RULES = (
    "You are serving as a plain text completion API for another program. "
    "You have no tools: never try to call one, never describe one, never claim you ran one. "
    "The conversation below is provided as plain text history. "
    "Answer only the final 'User:' turn and treat everything before it as context. "
    "Reply with the answer text only: no preamble, no restating the question, no tool calls."
)

_model_lock = threading.Lock()
_model_cache = {"at": 0.0, "free": {}, "all": set()}
_slots = threading.Semaphore(MAX_INFLIGHT)


def log(msg):
    sys.stderr.write("[oc-bridge %s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stderr.flush()


class BridgeError(Exception):
    def __init__(self, status, message, etype="bridge_error"):
        super().__init__(message)
        self.status = status
        self.message = message
        self.etype = etype


def upstream(path, payload=None, method=None, timeout=UPSTREAM_TIMEOUT):
    url = SERVE_URL + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        raise BridgeError(502, "upstream %s %s -> HTTP %s %s" % (method or "POST", path, exc.code, body))
    except Exception as exc:  # noqa: BLE001 - surfaced to the caller verbatim
        raise BridgeError(502, "upstream %s %s failed: %s: %s" % (method or "POST", path, type(exc).__name__, exc))


def model_catalog(force=False):
    """Free Zen models = opencode provider models whose cost is entirely zero."""
    now = time.time()
    with _model_lock:
        if not force and now - _model_cache["at"] < MODEL_CACHE_TTL and _model_cache["all"]:
            return _model_cache["free"], _model_cache["all"]
    data = upstream("/config/providers", timeout=30)
    providers = data.get("providers")
    if providers is None:
        providers = data if isinstance(data, list) else []
    free, allids = {}, set()
    for prov in providers:
        if prov.get("id") != "opencode":
            continue
        for mid, meta in (prov.get("models") or {}).items():
            allids.add(mid)
            cost = meta.get("cost") or {}
            if not cost:
                continue
            if float(cost.get("input", 1)) == 0 and float(cost.get("output", 1)) == 0:
                free[mid] = meta
    with _model_lock:
        _model_cache.update({"at": now, "free": free, "all": allids})
    return free, allids


def normalize_model(requested):
    mid = (requested or "").strip()
    if mid.startswith("opencode/"):
        mid = mid[len("opencode/") :]
    if not mid:
        raise BridgeError(400, "missing 'model'")
    _, allids = model_catalog()
    if allids and mid not in allids:
        raise BridgeError(400, "unknown model %r for provider opencode" % mid)
    return mid


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
    """OpenAI messages -> (system, transcript). Roles are flattened to text."""
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
    system = "\n\n".join(systems + [BRIDGE_RULES])
    return system, "\n\n".join(lines)


def complete(mid, system, transcript):
    """One request = one throwaway session, deleted afterwards.

    The call runs on a worker thread so a model that stops to wait for a tool
    approval (opencode asks the TUI, which does not exist in headless serve mode)
    cannot wedge the bridge: on timeout the session is aborted and the caller
    gets a 504 instead of a hung socket.
    """
    session = upstream("/session", {"title": "bridge-%d" % int(time.time() * 1000)})
    sid = session.get("id")
    if not sid:
        raise BridgeError(502, "upstream session creation returned no id")
    payload = {
        "model": {"providerID": "opencode", "modelID": mid},
        "system": system,
        "parts": [{"type": "text", "text": transcript}],
    }
    if AGENT:
        # Off by default: Zen's free tier answers 403 FreeTierError when the
        # request names an agent that carries an explicit permission ruleset.
        payload["agent"] = AGENT
    result, errors = [], []

    def call():
        try:
            result.append(upstream("/session/%s/message" % sid, payload, timeout=UPSTREAM_TIMEOUT + 30))
        except Exception as exc:  # noqa: BLE001 - re-raised on the caller's thread
            errors.append(exc)

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(UPSTREAM_TIMEOUT)
    try:
        if worker.is_alive():
            try:
                upstream("/session/%s/abort" % sid, {}, method="POST", timeout=20)
                worker.join(15)
            except BridgeError:
                pass
            log("warn: aborted session %s after %ds (model likely blocked on a tool prompt)" % (sid, UPSTREAM_TIMEOUT))
            raise BridgeError(504, "upstream model call exceeded %ds and was aborted" % UPSTREAM_TIMEOUT, etype="timeout")
        if errors:
            raise errors[0]
        if not result:
            raise BridgeError(502, "upstream returned no payload")
        return parse_completion(result[0])
    finally:
        try:
            upstream("/session/%s" % sid, method="DELETE", timeout=30)
        except BridgeError:
            log("warn: failed to delete session %s" % sid)


def parse_completion(result):
    info = result.get("info") or {}
    err = info.get("error")
    if err:
        data = err.get("data") or {}
        raise BridgeError(
            502,
            "upstream model error: %s" % (data.get("message") or err.get("name") or err),
            etype="upstream_model_error",
        )
    text, tool_parts, reason = [], 0, "stop"
    for part in result.get("parts") or []:
        ptype = part.get("type")
        if ptype == "text" and isinstance(part.get("text"), str):
            text.append(part["text"])
        elif ptype == "tool":
            tool_parts += 1
        elif ptype == "step-finish":
            reason = part.get("reason") or reason
    if tool_parts:
        log("warn: model emitted %d tool part(s); tools are disabled, text only" % tool_parts)
    content = "".join(text).strip()
    if not content:
        raise BridgeError(502, "upstream returned an empty completion", etype="empty_completion")
    tokens = info.get("tokens") or {}
    cache = tokens.get("cache") or {}
    prompt_tokens = int(tokens.get("input") or 0) + int(cache.get("read") or 0) + int(cache.get("write") or 0)
    completion_tokens = int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0)
    return {
        "content": content,
        "finish_reason": "length" if reason == "length" else "stop",
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": int(cache.get("read") or 0)},
        },
    }


def chat_completion(body):
    if int(body.get("n") or 1) != 1:
        raise BridgeError(400, "n>1 is not supported by this bridge")
    if body.get("tools"):
        log("warn: client sent %d tool declaration(s); served text-only" % len(body["tools"]))
    mid = normalize_model(body.get("model"))
    system, transcript = build_prompt(body.get("messages"))
    acquired = _slots.acquire(timeout=UPSTREAM_TIMEOUT)
    if not acquired:
        raise BridgeError(503, "bridge saturated (%d in flight)" % MAX_INFLIGHT, etype="overloaded")
    try:
        return mid, complete(mid, system, transcript)
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
        dict(
            base,
            object="chat.completion.chunk",
            choices=[{"index": 0, "delta": {"content": completion["content"]}, "finish_reason": None}],
        ),
        dict(
            base,
            object="chat.completion.chunk",
            choices=[{"index": 0, "delta": {}, "finish_reason": completion["finish_reason"]}],
        ),
    ]
    if include_usage:
        chunks.append(dict(base, object="chat.completion.chunk", choices=[], usage=completion["usage"]))
    return chunks


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oc-free-bridge/1.0"

    def log_message(self, fmt, *args):  # quieter default logging
        log("%s %s" % (self.address_string(), fmt % args))

    def _send_json(self, status, obj):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_error_json(self, err):
        self._send_json(
            err.status,
            {"error": {"message": err.message, "type": err.etype, "code": err.status}},
        )

    def _authorized(self):
        if not API_KEY:
            return True
        got = self.headers.get("Authorization", "")
        return got == "Bearer %s" % API_KEY

    def do_GET(self):
        try:
            if self.path in ("/health", "/healthz"):
                health = upstream("/global/health", timeout=10)
                free, allids = model_catalog()
                return self._send_json(
                    200,
                    {
                        "status": "ok" if health.get("healthy") else "degraded",
                        "serve": SERVE_URL,
                        "serve_version": health.get("version"),
                        "agent_override": AGENT or None,
                        "free_models": sorted(free),
                        "free_model_count": len(free),
                        "opencode_model_count": len(allids),
                    },
                )
            if self.path == "/v1/models":
                free, _ = model_catalog(force="refresh=1" in self.path)
                data = [
                    {
                        "id": mid,
                        "object": "model",
                        "created": 0,
                        "owned_by": "opencode-zen-free",
                        "context_window": (meta.get("limit") or {}).get("context"),
                    }
                    for mid, meta in sorted(free.items())
                ]
                return self._send_json(200, {"object": "list", "data": data})
            if self.path == "/":
                return self._send_json(200, {"service": "oc_free_bridge", "agent_override": AGENT or None, "serve": SERVE_URL})
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
        health = upstream("/global/health", timeout=10)
        free, _ = model_catalog(force=True)
        print(json.dumps({"serve": health, "free_models": sorted(free)}, indent=2))
        return 0
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        # shutdown() blocks until serve_forever() returns, so it must not run on
        # the thread that is inside serve_forever(): that deadlocks the process.
        signal.signal(sig, lambda *_: threading.Thread(target=httpd.shutdown, daemon=True).start())
    log("listening on %s:%d -> serve %s (agent=%s, key=%s)" % (HOST, PORT, SERVE_URL, AGENT, "on" if API_KEY else "off"))
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
        log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
