#!/usr/bin/env python3
# rescue_yai_jwt.py — 2026-09-19 救援脚本：绕开屡次无响应的 Runtime.evaluate
#
# 原理：
#   1. 起 headless Chrome(9222, /tmp/chrome_cdp_profile —— 12:34 新登录态所在)
#   2. 走浏览器级 WebSocket 调 CDP「Storage.getCookies」拿全部 cookie（不执行页面 JS）
#   3. 用拿到的 cookie 在浏览器外用纯 urllib 调 https://ai.yitang.top/api/auth/refresh
#      （与页面内 fetch 机制等价：同源 cookie 自动携带；428 换新 csrf 重试一次）
#   4. 成功 → 存 /tmp/yai_tokens.json + /tmp/yai_tokens.env + 注入 proxy3
#
# 用法（用户终端）：python3 /home/ai/lingclaude/rescue_yai_jwt.py
# 全程每步都有硬超时，任何失败 60 秒内给出明确判决。

import json, subprocess, time, urllib.request, websocket, base64, sys, os, re

CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
PROFILE = "/tmp/chrome_cdp_profile"
YAI = "https://ai.yitang.top"


def log(msg):
    print(msg, flush=True)


def fail(msg):
    log(f"❌ {msg}")
    sys.exit(1)


# ---------- 1. 确保 Chrome + CDP 就绪 ----------
def cdp_alive():
    try:
        urllib.request.urlopen(CDP_URL + "/json/version", timeout=2).read()
        return True
    except Exception:
        return False


if not cdp_alive():
    subprocess.run(["fuser", "-k", f"{CDP_PORT}/tcp"], capture_output=True)
    time.sleep(1)
    subprocess.Popen(
        ["google-chrome-stable",
         f"--remote-debugging-port={CDP_PORT}",
         "--remote-allow-origins=*",
         "--no-first-run", f"--user-data-dir={PROFILE}",
         "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", f"{YAI}/chat"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ok = False
    for _ in range(20):  # 最多 20s
        time.sleep(1)
        if cdp_alive():
            ok = True
            break
    if not ok:
        fail("CDP 启动失败（20s 内端口未就绪）")
log("✅ CDP 就绪")

# ---------- 2. 浏览器级 WS → Storage.getCookies ----------
wsurl = None
try:
    v = json.loads(urllib.request.urlopen(CDP_URL + "/json/version", timeout=5).read())
    wsurl = v["webSocketDebuggerUrl"]
except Exception as e:
    fail(f"拿 /json/version 失败: {e}")


def rpc(ws, payload, deadline_s=15):
    """发送并等待同 id 响应，带硬超时"""
    mid = payload["id"]
    ws.send(json.dumps(payload))
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            m = json.loads(ws.recv())
        except Exception:
            break
        if m.get("id") == mid:
            return m
    return None


try:
    ws = websocket.create_connection(wsurl, timeout=10)
except Exception as e:
    fail(f"浏览器 WS 连接失败: {e}")

m = rpc(ws, {"id": 1, "method": "Storage.getCookies", "params": {}})
if not m or "result" not in m:
    fail(f"Storage.getCookies 无响应/失败: {str(m)[:120]}")
cookies = m["result"].get("cookies", [])
ws.close()

yai_cookies = [c for c in cookies if "yitang" in c.get("domain", "")]
log(f"✅ 拿到 {len(yai_cookies)} 个 yitang cookie: "
    f"{[c['name'] for c in yai_cookies] or '空'}")
if not yai_cookies:
    fail("profile 里没有 yitang cookie —— 登录态没落盘？")

# ---------- 3. 浏览器外纯 HTTP 刷新 ----------
cookie_hdr = "; ".join(f"{c['name']}={c['value']}" for c in yai_cookies)
csrf = next((c["value"] for c in yai_cookies if c["name"] == "csrf_token"), "")


def do_refresh(csrf_val):
    req = urllib.request.Request(
        f"{YAI}/api/auth/refresh", data=b"{}",
        headers={"Content-Type": "application/json",
                 "Cookie": cookie_hdr,
                 "x-csrf-token": csrf_val,
                 "Origin": YAI, "Referer": f"{YAI}/chat",
                 "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) rescue/1.0"},
        method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status, json.loads(resp.read().decode() or "{}"), resp.headers
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        try:
            body_json = json.loads(body)
        except Exception:
            body_json = {}
        return e.code, body_json, e.headers
    except Exception as e:
        return 0, {"code": f"FETCH_FAIL:{str(e)[:60]}"}, None


status, data, hdrs = do_refresh(csrf)
if status == 428:  # CSRF 轮换协议：用 Set-Cookie 里的新 csrf 重试一次
    new_csrf = ""
    sc = (hdrs or {}).get("Set-Cookie", "") if hdrs else ""
    m2 = re.search(r"csrf_token=([^;]+)", sc)
    if m2:
        new_csrf = m2.group(1)
    log(f"⚠️ 428 CSRF 轮换，用新 csrf 重试…")
    status, data, hdrs = do_refresh(new_csrf)
    csrf = new_csrf or csrf

d = data.get("data") or {}
jwt = d.get("access_token") or d.get("token") or ""
log(f"refresh HTTP {status} code={data.get('code')} JWT={len(jwt)}字")

# ---------- 4. 校验 + 落盘 + 注入 ----------
if not (len(jwt) > 50 and jwt.count(".") == 2):
    fail(f"未拿到有效 JWT（HTTP {status}）。若 401，请在 VNC 可见浏览器重新登录一次再跑本脚本")

try:
    pad = "=" * (-len(jwt.split(".")[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(jwt.split(".")[1] + pad))
    exp = payload.get("exp", 0)
    remain_h = (exp - time.time()) / 3600
    log(f"JWT 过期: {time.strftime('%m-%d %H:%M', time.gmtime(exp))} (剩 {remain_h:.1f}h)")
    if remain_h <= 0:
        fail("拿到的 JWT 已过期")
except Exception as e:
    fail(f"JWT 解析失败: {e}")

with open("/tmp/yai_tokens.json", "w") as f:
    json.dump({"YAI_JWT": jwt, "YAI_CSRF_TOKEN": csrf}, f)
with open("/tmp/yai_tokens.env", "w") as f:
    f.write(f"YAI_JWT={jwt}\nYAI_CSRF_TOKEN={csrf}\n")
log("✅ token 已保存 (/tmp/yai_tokens.json + .env)")

admin_key = ""
try:
    with open(os.path.expanduser("~/.ling_keys.env")) as f:
        for line in f:
            if line.startswith("export PROXY3_ADMIN_KEY="):
                admin_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
except Exception:
    pass
if not admin_key:
    fail("PROXY3_ADMIN_KEY 读取失败，token 已存盘，可手工注入")

req = urllib.request.Request(
    "http://127.0.0.1:8765/admin/env",
    data=json.dumps({"YAI_JWT": jwt, "YAI_CSRF_TOKEN": csrf}).encode(),
    headers={"Content-Type": "application/json", "X-Admin-Key": admin_key},
    method="POST")
try:
    r = json.loads(urllib.request.urlopen(req, timeout=5).read())
    log(f"✅ proxy3 注入: {r}")
except Exception as e:
    fail(f"proxy3 注入失败: {e}（token 已存盘）")

# ---------- 5. 清理 Chrome ----------
subprocess.run(["pkill", "-f", f"chrome.*remote-debugging-port={CDP_PORT}"],
               capture_output=True)
log("🎉 救援完成")
