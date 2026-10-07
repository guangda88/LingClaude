# -*- coding: utf-8 -*-
"""OAuth 监听 v4: code -> v2 token(扁平解析) -> 落盘 -> 自动发射 img_final."""
import json, time, socket, subprocess, urllib.request

APP = json.load(open('/home/ai/lingclaude/tmp/feishu_app.json'))
W = '/tmp/feishu_doc_work'
LOG = open(W + '/oauth_v4.log', 'a', buffering=1)

def P(*a):
    LOG.write(time.strftime('%H:%M:%S ') + ' '.join(str(x) for x in a) + '\n')

CID, CSEC = APP['app_id'], APP['app_secret']
deadline = time.time() + 1800
srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(('0.0.0.0', 18765))
srv.listen(5)
P('listening (30min window)')

while time.time() < deadline:
    srv.settimeout(max(1, deadline - time.time()))
    try:
        c, _ = srv.accept()
    except socket.timeout:
        break
    data = b''
    c.settimeout(5)
    try:
        while b'\r\n\r\n' not in data and len(data) < 65536:
            b2 = c.recv(4096)
            if not b2: break
            data += b2
    except Exception:
        pass
    line = data.split(b'\r\n')[0].decode('utf-8', 'ignore')
    P('req:', line[:90])
    if '/callback' not in line or 'code=' not in line:
        c.sendall(b'HTTP/1.1 404 Not Found\r\nContent-Length: 2\r\n\r\n40')
        c.close(); continue
    code = line.split('code=')[1].split('&')[0].split(' ')[0]
    P('got code len=%d' % len(code))
    body = json.dumps({'grant_type': 'authorization_code', 'code': code,
                       'redirect_uri': 'http://localhost:18765/callback'}).encode()
    basic = __import__('base64').b64encode(('%s:%s' % (CID, CSEC)).encode()).decode()
    tok = None
    for attempt in range(1, 7):
        try:
            rq = urllib.request.Request('https://open.feishu.cn/open-apis/authen/v2/oauth/token',
                data=body, headers={'Content-Type': 'application/json',
                'Authorization': 'Basic ' + basic}, method='POST')
            resp_txt = urllib.request.urlopen(rq, timeout=30).read().decode()
            P('attempt%d RAW=%s' % (attempt, resp_txt[:300]))
            open(W + '/token_raw_response.json', 'w').write(resp_txt)
            r = json.loads(resp_txt)
            if r.get('code') == 0 or r.get('access_token'):
                tok = r; P('attempt%d SUCCESS' % attempt); break
        except urllib.error.HTTPError as e:
            try: eb = e.read().decode()[:200]
            except Exception: eb = str(e)[:200]
            P('attempt%d HTTP%s %s' % (attempt, e.code, eb))
        except Exception as e:
            P('attempt%d EXC %s' % (attempt, str(e)[:120]))
        time.sleep(1)
    if tok:
        json.dump(tok, open('/home/ai/lingclaude/tmp/feishu_user_token.json', 'w'), ensure_ascii=False, indent=1)
        exp = time.time() + int(tok.get('expires_in', 7200))
        P('token saved, expires_in=%s' % tok.get('expires_in'))
        c.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n'
                  b'<h2>\xe2\x9c\x85 \xe6\x8e\x88\xe6\x9d\x83\xe6\x88\x90\xe5\x8a\x9f\xef\xbc\x81\xe7\x81\xb5\xe5\x85\x8b\xe5\xb7\xb2\xe6\x8b\xbf\xe5\x88\xb0\xe4\xbb\xa4\xe7\x89\x8c\xef\xbc\x8c\xe6\xad\xa3\xe5\x9c\xa8\xe8\x87\xaa\xe5\x8a\xa8\xe6\x8f\x92\xe5\x9b\xbe\xef\xbc\x8c\xe5\x8f\xaf\xe5\x85\xb3\xe9\x97\xad\xe6\xad\xa4\xe9\xa1\xb5\xe3\x80\x82</h2>')
        time.sleep(1)
        subprocess.Popen(['python3', W + '/img_final.py'],
                         stdout=open(W + '/img_final_stdout.log', 'w'),
                         stderr=subprocess.STDOUT)
        P('img_final launched')
    else:
        c.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n'
                  b'<h2>\xe2\x9d\x8c token exchange failed, see log</h2>')
    c.close()
srv.close()
P('listener exit')
