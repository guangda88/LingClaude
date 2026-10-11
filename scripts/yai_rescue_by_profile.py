#!/usr/bin/env python3
"""v3: HTTP-refresh-first（tokens.json 的 rt）+ CDP 兜底；flock 防并发；原子写 tokens.json
   由 yai-rescue.timer (systemd --user) 每 2 小时触发（:23）"""
import json, os, sys, time, base64, urllib.request, urllib.error, fcntl

TOKF = '/tmp/yai_tokens.json'
LOCK = '/tmp/yai_rescue.lock'
PORT = 9256

def log(msg):
    print(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}", flush=True)

def exp_of(t):
    try:
        p = t.split('.')[1]; p += '=' * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get('exp', 0)
    except Exception:
        return 0

def http_refresh(rt):
    """返回 (body, set_cookies)；轮换的新 rt 只从 Set-Cookie 下发，body 里没有"""
    req = urllib.request.Request('https://ai.yitang.top/api/auth/refresh', method='POST', data=b'{}')
    req.add_header('Cookie', f'refresh_token={rt}')
    req.add_header('Content-Type', 'application/json')
    req.add_header('User-Agent', 'Mozilla/5.0')
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()), (r.headers.get_all('Set-Cookie') or [])

def rt_from_setcookie(sc_list):
    import re as _re
    for c in sc_list or []:
        m = _re.match(r'refresh_token=([^;]+)', c)
        if m and m.group(1):
            return m.group(1)
    return None

def try_write_tokens(toks, newjwt=None, newrt=None, newcsrf=None):
    cur = json.load(open(TOKF))
    changed = False
    if newjwt and exp_of(newjwt) > exp_of(cur.get('YAI_JWT', '')):
        cur['YAI_JWT'] = newjwt; changed = True
        log(f"✅ 新 JWT exp={time.strftime('%m-%d %H:%M', time.localtime(exp_of(newjwt)))}")
    if newrt and newrt != cur.get('YAI_REFRESH_TOKEN'):
        cur['YAI_REFRESH_TOKEN'] = newrt; changed = True
        log('✅ refresh_token 已更新（Set-Cookie 轮换收割）')
    if newcsrf and newcsrf != cur.get('YAI_CSRF_TOKEN'):
        cur['YAI_CSRF_TOKEN'] = newcsrf; changed = True
        log('✅ csrf_token 已更新')
    if changed:
        tmp = TOKF + '.tmp'
        json.dump(cur, open(tmp, 'w')); os.replace(tmp, TOKF)
        log('💾 tokens.json 原子更新完成')
        _sync_ling_keys(cur)
    else:
        log('响应未带来更新（不比现役新）')
    return 0

def _sync_ling_keys(toks):
    """同步 proxy3 实际消费的 ~/.ling_keys.env（yai_adapter 401 自愈读这里）"""
    import re as _re
    env_path = os.path.expanduser('~/.ling_keys.env')
    if not os.path.exists(env_path):
        log('ling_keys.env 不存在，跳过同步'); return
    repl = {k: toks.get(k, '') for k in ('YAI_JWT', 'YAI_REFRESH_TOKEN', 'YAI_CSRF_TOKEN') if toks.get(k)}
    lines = open(env_path).read().splitlines(keepends=True)
    out, hit = [], set()
    for ln in lines:
        m = _re.match(r'((?:export\s+)?)(YAI_JWT|YAI_REFRESH_TOKEN|YAI_CSRF_TOKEN)=(.*)', ln)
        if m and m.group(2) in repl:
            out.append(f'{m.group(1)}{m.group(2)}={repl[m.group(2)]}\n')
            hit.add(m.group(2)); continue
        out.append(ln)
    for k, v in repl.items():
        if k not in hit:
            out.append(f'{k}={v}\n')
    tmp = env_path + '.tmp'
    open(tmp, 'w').write(''.join(out)); os.replace(tmp, env_path)
    log(f'💾 ling_keys.env 已同步: {sorted(hit)}')

def cdp_fallback():
    """v2 的 CDP 抓取逻辑：拉起 headless Chrome 抓 cookie 罐，尝试 refresh"""
    import subprocess
    try:
        PROC = subprocess.Popen(
            ['google-chrome', f'--remote-debugging-port={PORT}', '--remote-allow-origins=*',
             '--no-first-run', '--user-data-dir=/tmp/gc_A', '--headless=new', '--disable-gpu',
             '--no-sandbox', '--disable-dev-shm-usage', '--ozone-platform=headless', 'about:blank'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log(f'CDP 启动失败: {e}'); return 1
    try:
        ws_url = None
        for _ in range(20):
            time.sleep(1)
            try:
                v = json.loads(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/version', timeout=2).read())
                ws_url = v['webSocketDebuggerUrl']; break
            except Exception:
                continue
        if not ws_url:
            log('CDP 端口未就绪'); return 1
        from websocket import create_connection

        def cdp_call(ws, id_, method, params=None, deadline_s=30):
            ws.send(json.dumps({'id': id_, 'method': method, 'params': params or {}}))
            dl = time.time() + deadline_s
            while time.time() < dl:
                try:
                    msg = json.loads(ws.recv())
                except Exception:
                    time.sleep(0.5); continue
                if msg.get('id') == id_:
                    return msg
            return None

        result = None
        try:
            ws = create_connection(ws_url, timeout=30)
            r = cdp_call(ws, 1, 'Storage.getCookies'); ws.close()
            if r and 'result' in r:
                result = r['result']; log('路径1 browser/Storage.getCookies 成功')
        except Exception as e:
            log(f'路径1 异常: {e}')
        if not result:
            try:
                targets = json.loads(urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/list', timeout=3).read())
                page = next((t for t in targets if t['type'] == 'page'), None)
                if page:
                    ws = create_connection(page['webSocketDebuggerUrl'], timeout=30)
                    r = cdp_call(ws, 2, 'Network.getCookies', {'urls': ['https://ai.yitang.top']}); ws.close()
                    if r and 'result' in r:
                        result = {'cookies': r['result'].get('cookies', [])}; log('路径2 page/Network.getCookies 成功')
            except Exception as e:
                log(f'路径2 异常: {e}')
        if not result:
            log('两条 WS 路径全灭'); return 1
        data = {}
        for c in [c for c in result['cookies'] if 'yitang' in c.get('domain', '')]:
            if c.get('value'):
                data[f"{c['domain']}|{c['name']}"] = c['value']
        json.dump(data, open('/tmp/yai_decrypted.json', 'w'))
        jar_jwt = next((v for k, v in data.items() if 'access' in k and v.startswith('eyJ')), None)
        jar_csrf = next((v for k, v in data.items() if 'csrf' in k), None)
        if jar_jwt:
            open('/tmp/rescued_jwt.txt', 'w').write(jar_jwt)
            log(f"🎯 罐内 JWT ({len(jar_jwt)}B, exp={time.strftime('%m-%d %H:%M', time.localtime(exp_of(jar_jwt)))}) 存 rescued_jwt.txt")
            try_write_tokens(json.load(open(TOKF)), jar_jwt, None, jar_csrf)
        rt2 = next((v for k, v in data.items() if 'refresh' in k), None)
        if rt2:
            try:
                body, sc = http_refresh(rt2)
                d = body.get('data') or {}
                log(f'罐内 rt refresh 成功, code={body.get("code")}')
                newrt2 = d.get('refresh_token') or d.get('refreshToken') or rt_from_setcookie(sc)
                csrf2 = next((v for k, v in data.items() if 'csrf' in k), None)
                return try_write_tokens(json.load(open(TOKF)), d.get('token'), newrt2, csrf2)
            except urllib.error.HTTPError as e:
                log(f'罐内 rt refresh HTTP {e.code}（可能已轮换烧毁）'); return 1
            except Exception as e:
                log(f'罐内 rt refresh 异常: {e}'); return 1
        log('罐内无 refresh_token'); return 1
    finally:
        try:
            PROC.terminate(); PROC.wait(timeout=5)
        except Exception:
            try: PROC.kill()
            except Exception: pass
        log('Chrome 已清理')

def main():
    fd = open(LOCK, 'w')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log('已有实例在跑，退出'); return 0
    if not os.path.exists(TOKF):
        log('tokens.json 不存在'); return cdp_fallback()
    toks = json.load(open(TOKF))
    cur_exp = exp_of(toks.get('YAI_JWT', ''))
    log(f"现役 JWT exp={time.strftime('%m-%d %H:%M', time.localtime(cur_exp)) if cur_exp else '?'}")
    rt = toks.get('YAI_REFRESH_TOKEN', '')
    if not rt:
        log('tokens.json 无 refresh_token'); return cdp_fallback()
    try:
        body, sc = http_refresh(rt)
    except urllib.error.HTTPError as e:
        log(f'HTTP refresh {e.code}（rt 可能已被烧毁）→ CDP 兜底'); return cdp_fallback()
    except Exception as e:
        log(f'HTTP refresh 异常 {type(e).__name__}: {e} → CDP 兜底'); return cdp_fallback()
    d = body.get('data') or {}
    log(f'HTTP refresh 成功, code={body.get("code")}')
    newrt = d.get('refresh_token') or d.get('refreshToken') or rt_from_setcookie(sc)
    return try_write_tokens(toks, d.get('token'), newrt)

if __name__ == '__main__':
    sys.exit(main())
