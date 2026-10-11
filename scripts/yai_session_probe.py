#!/usr/bin/env python3
"""用活 JWT 探测 yitang 可换取新 refresh_token 的接口；记录状态码与 Set-Cookie"""
import json, urllib.request, urllib.error, time

JWT = json.load(open('/tmp/yai_tokens.json'))['YAI_JWT']
OUT = open('/tmp/yai_probe_session.log', 'w', buffering=1)
BASE = 'https://ai.yitang.top'

def probe(method, path, extra_hdr=None, cookie=None, body=None, tag=''):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header('Authorization', f'Bearer {JWT}')
    req.add_header('User-Agent', 'Mozilla/5.0')
    if extra_hdr:
        for k, v in extra_hdr.items():
            req.add_header(k, v)
    if cookie:
        req.add_header('Cookie', cookie)
    data = body.encode() if isinstance(body, str) else body
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            sc = r.headers.get_all('Set-Cookie') or []
            print(f"[{tag}] {method} {path} -> {r.status} set_cookie={len(sc)} body={r.read()[:100]}", file=OUT)
            for c in sc:
                print(f"    SET-COOKIE: {c[:160]}", file=OUT)
            return
    except urllib.error.HTTPError as e:
        sc = e.headers.get_all('Set-Cookie') or []
        print(f"[{tag}] {method} {path} -> HTTP {e.code} set_cookie={len(sc)} body={e.read()[:100]}", file=OUT)
        for c in sc:
            print(f"    SET-COOKIE: {c[:160]}", file=OUT)
    except Exception as e:
        print(f"[{tag}] {method} {path} -> {type(e).__name__}: {e}", file=OUT)

probe('GET', '/api/user/info', tag='alive-check')
probe('POST', '/api/auth/refresh', cookie=f'access_token={JWT}', tag='acc-as-rt')
probe('POST', '/api/auth/refresh', extra_hdr={'Authorization': f'Bearer {JWT}'}, body='{}', tag='bearer-rt')
for p in ('/api/auth/session', '/api/auth/token', '/api/auth/token/refresh',
          '/api/auth/renew', '/api/user/session', '/api/auth/me'):
    probe('GET', f'/api/user/info'.replace('/api/user/info', p), tag='session-get')
    probe('POST', p, body='{}', tag='session-post')
probe('GET', '/', tag='root-page')
print('PROBE DONE', file=OUT)
