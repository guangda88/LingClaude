#!/usr/bin/env python3
"""检验 rescued_jwt.txt（罐内 JWT）在 /api/auth/me 是否仍存活"""
import json, urllib.request, urllib.error, time

jwt = open('/tmp/rescued_jwt.txt').read().strip()
out = open('/tmp/yai_jar_check.log', 'w', buffering=1)
req = urllib.request.Request('https://ai.yitang.top/api/auth/me')
req.add_header('Authorization', f'Bearer {jwt}')
req.add_header('User-Agent', 'Mozilla/5.0')
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print(f'JAR JWT -> HTTP {r.status} body={r.read()[:200]}', file=out)
except urllib.error.HTTPError as e:
    print(f'JAR JWT -> HTTP {e.code} body={e.read()[:200]}', file=out)
except Exception as e:
    print(f'JAR JWT -> {type(e).__name__}: {e}', file=out)
print('DONE', file=out)
