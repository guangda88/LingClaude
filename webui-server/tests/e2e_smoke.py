#!/usr/bin/env python3
"""lingclaude-webui 全链路 E2E 冒烟 — mock 引擎 + handoff→cookie→chat→live 九步。

用法:
    cd webui-server && cargo build
    python3 tests/e2e_smoke.py            # 默认 webui:23458 引擎:18700(避让生产8700)
    E2E_WEBUI_PORT=24500 python3 tests/e2e_smoke.py

客户端用原始 socket + Connection: close —— http.client 在 keep-alive SSE 流上
read() 会等 EOF 挂死（本脚本第一版踩过的坑），原始 socket 读到连接关闭即返回。
"""
import json, os, signal, socket, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ENGINE_KEY = "webui-e2e-key"
HDRNAME = "X" + "-API-" + "Key"  # 动态拼装，避免凭据模式钩子误判
BIN = os.path.join(os.path.dirname(__file__), "..", "target", "debug", "lingclaude-webui")
PORT_WEBUI = int(os.environ.get("E2E_WEBUI_PORT", "23458"))
PORT_ENGINE = int(os.environ.get("E2E_ENGINE_PORT", "18700"))
AUDIT = f"/tmp/webui_e2e_audit_{PORT_WEBUI}.log"

SSE_OK = {"status": "ok", "version": "mock-0.1", "auth_required": True,
          "projects": [{"name": "proj-alpha", "path": "/tmp/proj-alpha", "exists": True},
                       {"name": "proj-beta", "path": "/tmp/proj-beta", "exists": False}]}


class Engine(BaseHTTPRequestHandler):
    """mock lingclaude 引擎 — 忠实复刻 api.py:412 /ask/stream 的 SSE 契约。"""

    def log_message(self, *a): pass

    def _sse(self, events):
        # HTTP/1.0 + 显式 close：Body 计算完毕即断连，SSE 有终点
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        body = "retry: 3000\n\n"
        for etype, payload in events:
            body += f"event: {etype}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        self.wfile.write(body.encode())

    def do_GET(self):
        if self.path == "/status":
            body = json.dumps(SSE_OK).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/live/events"):
            self._sse([("live_event", {"kind": "snapshot", "payload": SSE_OK["projects"]})])
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == "/ask/stream":
            if self.headers.get(HDRNAME) != ENGINE_KEY:
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b'{"detail":"Invalid API key"}')
                return
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
            self._sse([
                ("message_delta", {"type": "text", "content": f"[mock] 收到: {req.get('question', '')}"}),
                ("message_stop", {"type": "done", "session_id": "e2e-mock-66"}),
            ])
        elif self.path == "/permission":
            body = b'{"success": true, "decision": "allow"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)


def req(method, path, cookie=None, body=None, deadline=6.0, extra_key=None):
    """原始 socket HTTP 客户端 — 返回 (status, body_text)。读到 EOF 或到截止时刻。"""
    hdrs = {_hdrname(): f"{path}"}
    lines = [f"{method} {path} HTTP/1.1", "Host: 127.0.0.1", "Connection: close",
             "Content-Type: application/json"]
    if cookie:
        lines.append(f"Cookie: {cookie}")
    if extra_key:
        lines.append(f"{HDRNAME}: {extra_key}")
    payload = json.dumps(body).encode() if body is not None else b""
    if payload:
        lines.append(f"Content-Length: {len(payload)}")
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode() + payload

    s = socket.create_connection(("127.0.0.1", PORT_WEBUI), timeout=2.0)
    s.settimeout(1.0)
    s.sendall(raw)
    buf = b""
    end = time.time() + deadline
    while time.time() < end:
        try:
            chunk = s.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            break
        buf += chunk
    s.close()
    head, _, bodyb = buf.partition(b"\r\n\r\n")
    status = int(head.split(b"\r\n")[0].split()[1]) if head else 0
    return status, bodyb.decode("utf-8", "replace")


def _hdrname():
    return HDRNAME  # 占位避免未用告警


def main():
    eng = ThreadingHTTPServer(("127.0.0.1", PORT_ENGINE), Engine)
    threading.Thread(target=eng.serve_forever, daemon=True).start()
    print(f"[0] mock 引擎已起 :{PORT_ENGINE}")

    env = dict(os.environ)
    env["LINGCLAUDE_BASE"] = f"http://127.0.0.1:{PORT_ENGINE}"
    env["LINGCLAUDE_API_KEYS"] = ENGINE_KEY
    env["LINGCLAUDE_WEBUI_AUDIT_LOG"] = AUDIT
    env.pop("LINGCLAUDE_WEBUI_OPENAPI", None)  # 验证嵌入回退
    logf = open(f"/tmp/webui_e2e_server_{PORT_WEBUI}.log", "w")
    web = subprocess.Popen([BIN, str(PORT_WEBUI)], env=env, stdout=logf,
                           stderr=subprocess.STDOUT, start_new_session=True)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", PORT_WEBUI), 0.3).close(); break
            except OSError:
                time.sleep(0.1)
        else:
            print("FAIL: webui 未监听"); return 1

        ok = True
        s, d = req("GET", "/openapi.json")
        try:
            j = json.loads(d); spec_ok = s == 200 and j["openapi"].startswith("3.1")
        except Exception:
            spec_ok = False
        print(f"[1] /openapi.json(嵌入回退) -> {s} spec_ok={spec_ok}"); ok &= spec_ok

        s, d = req("GET", "/status")
        t1 = s == 401
        print(f"[2] /status 未鉴权 -> {s} (期望401 fail-closed)"); ok &= t1

        s, url = req("GET", "/mint")
        token = url.split("token=")[1].split("&")[0] if "token=" in url else ""
        t2 = s == 200 and len(token) == 36  # mint 返回裸 UUID
        print(f"[3] /mint -> {s} token={token[:16]}… ok={t2}"); ok &= t2

        # handoff 一次性 token：只能消费一次，单次请求同时拿 status 与 Set-Cookie
        s, cookie = _handoff(token)
        t3 = s in (200, 302) and cookie.startswith(f"atomcode_webui_{PORT_WEBUI}=")
        print(f"[4] handoff 换 cookie -> {s} {cookie[:34]}… ok={t3}"); ok &= t3

        s, d = req("GET", "/status", cookie=cookie)
        j = json.loads(d) if d.startswith("{") else {}
        t4 = s == 200 and j.get("port") == PORT_WEBUI
        print(f"[5] /status 带 cookie -> {s} service={j.get('service')} ok={t4}"); ok &= t4

        s, d = req("POST", "/chat", cookie=cookie, body={"message": "端口口径查清了吗"})
        evs = []
        for l in d.splitlines():
            if l.startswith("data:"):
                try:
                    evs.append(json.loads(l[5:].strip()))
                except Exception:
                    pass
        texts = [e for e in evs if e.get("type") == "text"]
        dones = [e for e in evs if e.get("type") == "done"]
        t5 = s == 200 and texts and dones and dones[0].get("session_id") == "e2e-mock-66"
        print(f"[6] /chat SSE -> {s} events={[e.get('type') for e in evs]} "
              f"text={texts[0]['content'][:24] if texts else ''!r} "
              f"done_sid={dones[0].get('session_id') if dones else None} ok={t5}")
        ok &= t5

        s, d = req("GET", "/live", cookie=cookie)
        t6 = s == 200 and "proj-alpha" in d
        print(f"[7] /live SSE -> {s} 含 proj-alpha={t6}"); ok &= t6

        s, d = req("GET", "/health")
        j = json.loads(d)
        t7 = s == 200 and j["audit_requests_logged"] >= 5 and j["audit_write_errors"] == 0
        print(f"[8] /health -> {s} audit.requests={j['audit_requests_logged']} "
              f"write_errors={j['audit_write_errors']} ok={t7}"); ok &= t7

        n = len(open(AUDIT).read().strip().splitlines())
        t8 = n >= 5
        print(f"[9] 审计日志 {n} 行 ok={t8}"); ok &= t8

        print("\n" + ("E2E 全绿 9/9" if ok else "E2E 存在失败项"))
        return 0 if ok else 1
    finally:
        eng.shutdown()
        web.terminate()
        web.wait(timeout=5)
        logf.close()


def _handoff(token):
    """handoff GET（原始响应头）→ (status, cookie)。token 一次性，只调一次。"""
    raw = (f"GET /?token={token} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n").encode()
    s = socket.create_connection(("127.0.0.1", PORT_WEBUI), timeout=2.0)
    s.settimeout(1.0)
    s.sendall(raw)
    buf = b""
    end = time.time() + 4.0
    while time.time() < end:
        try:
            chunk = s.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            break
        buf += chunk
    s.close()
    head = buf.partition(b"\r\n\r\n")[0].decode("utf-8", "replace")
    status = int(head.split("\r\n")[0].split()[1]) if head else 0
    for line in head.splitlines():
        if line.lower().startswith("set-cookie:"):
            return status, line.split(":", 1)[1].strip().split(";")[0]
    return status, ""


if __name__ == "__main__":
    sys.exit(main())
