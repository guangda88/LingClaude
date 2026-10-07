#!/usr/bin/env python3
# 用已收到的 code 抢救换 token：v1 oidc 端点（Basic auth）优先，多端点兜底
import json, ssl, time, base64, urllib.request, urllib.error

CRE = json.load(open('/home/ai/lingclaude/tmp/feishu_app.json'))
APP_ID, SECRET = CRE['app_id'], CRE['app_secret']
CODE = 'cKEry2L1BBda4DFf8d71yBf3Kww6G15x'
REDIRECT = 'http://localhost:18765/callback'
W = '/tmp/feishu_doc_work'
LOG = open(W + '/token_rescue.log', 'w', buffering=1)
def P(*a): LOG.write(' '.join(str(x) for x in a) + '\n')
CTX = ssl._create_unverified_context()

def post(url, payload, headers):
    rq = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method='POST')
    try:
        resp = urllib.request.urlopen(rq, timeout=30, context=CTX)
        return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

BASIC = 'Basic ' + base64.b64encode(f'{APP_ID}:{SECRET}'.encode()).decode()
tries = [
    ('v1_oidc_basic', 'https://open.feishu.cn/open-apis/authen/v1/oidc/access_token',
     {'grant_type': 'authorization_code', 'code': CODE},
     {'Content-Type': 'application/json', 'Authorization': BASIC}),
    ('v1_oidc_json', 'https://open.feishu.cn/open-apis/authen/v1/oidc/access_token',
     {'grant_type': 'authorization_code', 'code': CODE, 'client_id': APP_ID, 'client_secret': SECRET},
     {'Content-Type': 'application/json'}),
    ('v2_oauth_json', 'https://open.feishu.cn/open-apis/authen/v2/oauth/token',
     {'grant_type': 'authorization_code', 'client_id': APP_ID, 'client_secret': SECRET,
      'code': CODE, 'redirect_uri': REDIRECT},
     {'Content-Type': 'application/json'}),
]
ok = None
for name, url, payload, hdrs in tries:
    st, body = post(url, payload, hdrs)
    P(name, 'HTTP', st, body[:260])
    try:
        j = json.loads(body)
    except Exception:
        continue
    if (j.get('code') == 0) or (j.get('access_token') and not j.get('code')):
        ok = j
        P('WINNER =', name)
        break

if not ok:
    P('ALL_FAILED'); LOG.close(); print('RESCUE_FAILED'); raise SystemExit(1)

d = ok.get('data') or ok
UT = d.get('access_token')
json.dump({'user_access_token': UT, 'refresh_token': d.get('refresh_token'),
           'expires': time.time() + int(d.get('expires_in', 7200))},
          open('/home/ai/lingclaude/tmp/feishu_user_token.json', 'w'))
P('USER TOKEN SAVED, expires_in =', d.get('expires_in'))
LOG.close(); print('RESCUE_OK')
