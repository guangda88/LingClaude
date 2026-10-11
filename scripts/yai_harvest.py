#!/usr/bin/env python3
"""新族验证+收割: me探活 → rt刷新 → 抓 Set-Cookie 新rt → 原子更新 ling_keys.env + tokens.json"""
import json, re, os, time, base64, urllib.request, urllib.error

FAM = json.load(open('/tmp/yai_new_family.json'))
OUT = open('/tmp/yai_harvest.log', 'w', buffering=1)

def exp_of(t):
    try:
        p = t.split('.')[1]; p += '=' * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get('exp', 0)
    except Exception:
        return 0

def req(method, path, jwt=None, cookie=None):
    r = urllib.request.Request('https://ai.yitang.top' + path, method=method, data=b'{}' if method == 'POST' else None)
    if jwt:
        r.add_header('Authorization', f'Bearer {jwt}')
    if cookie:
        r.add_header('Cookie', cookie)
    r.add_header('Content-Type', 'application/json')
    r.add_header('User-Agent', 'Mozilla/5.0')
    try:
        with urllib.request.urlopen(r, timeout=12) as resp:
            return resp.status, resp.read()[:300], resp.headers.get_all('Set-Cookie') or []
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300], e.headers.get_all('Set-Cookie') or []

# 1. 探活
at = FAM['access_token']
st, body, _ = req('GET', '/api/auth/me', jwt=at)
print(f'[1] me(access) -> {st} {body[:120]}', file=OUT)

# 2. rt 刷新
rt = FAM['refresh_token']
st2, body2, sc = req('POST', '/api/auth/refresh', cookie=f'refresh_token={rt}')
print(f'[2] refresh(rt) -> {st2} {body2[:150]}', file=OUT)
for c in sc:
    print(f'    SET-COOKIE: {c[:200]}', file=OUT)
if st2 != 200:
    print('RESULT: refresh 失败，新族 rt 也无法使用', file=OUT)
    raise SystemExit(1)

# 3. 提取新 access + 从 Set-Cookie 抓新 rt
try:
    d = json.loads(body2).get('data') or {}
except Exception:
    d = {}
new_at = d.get('token') or ''
new_rt = None
for c in sc:
    m = re.match(r'refresh_token=([^;]+)', c)
    if m:
        new_rt = m.group(1)
print(f'[3] new_access len={len(new_at)} exp={time.strftime("%m-%d %H:%M", time.localtime(exp_of(new_at))) if new_at else "?"}', file=OUT)
print(f'[3] new_rt from Set-Cookie: {"捕捉到 " + str(len(new_rt)) + "B" if new_rt else "❌ 未下发(需人工介入)"}', file=OUT)

final_at = new_at if (new_at and exp_of(new_at) > exp_of(at)) else at
final_rt = new_rt or rt

# 4. 原子更新 ~/.ling_keys.env（只动 YAI_ 三行）
env_path = os.path.expanduser('~/.ling_keys.env')
lines = open(env_path).read().splitlines(keepends=True)
repl = {'YAI_JWT': final_at, 'YAI_REFRESH_TOKEN': final_rt,
        'YAI_CSRF_TOKEN': FAM.get('csrf_token', '')}
newlines, hit = [], set()
for ln in lines:
    m = re.match(r'((?:export\s+)?)(YAI_JWT|YAI_REFRESH_TOKEN|YAI_CSRF_TOKEN)=(.*)', ln)
    if m:
        k = m.group(2)
        if k in repl and repl[k]:
            newlines.append(f'{m.group(1)}{k}={repl[k]}\n')
            hit.add(k)
            continue
    newlines.append(ln)
for k, v in repl.items():
    if k not in hit and v:
        newlines.append(f'{k}={v}\n')
tmp = env_path + '.tmp'
open(tmp, 'w').write(''.join(newlines))
os.replace(tmp, env_path)
print(f'[4] ling_keys.env 已原子更新: 更新了 {sorted(hit)}', file=OUT)

# 5. 同步 tokens.json
tk = json.load(open('/tmp/yai_tokens.json'))
tk['YAI_JWT'] = final_at
tk['YAI_REFRESH_TOKEN'] = final_rt
if FAM.get('csrf_token'):
    tk['YAI_CSRF_TOKEN'] = FAM['csrf_token']
t2 = '/tmp/yai_tokens.json.tmp'
json.dump(tk, open(t2, 'w')); os.replace(t2, '/tmp/yai_tokens.json')
print('[5] tokens.json 已同步', file=OUT)
json.dump({'access_token': final_at, 'refresh_token': final_rt, 'csrf_token': FAM.get('csrf_token', '')},
          open('/tmp/yai_new_family.json', 'w'))
print('RESULT: 🎉 复活完成', file=OUT)
