#!/usr/bin/env python3
"""v5: TTL 探测 + 委派权威收割链（替代 v3 的自带收割）
   背景（2026-10-23 复盘）：rt 自 v3 时代起不再持久化——真实轮换发生在 9228 页面内，
   每次成功刷新旧票即烧，罐外快照（tokens.json/env）永远落后一轮，自带收割必然 401。
   权威链 = cron :17 的 S2 快速路径（refresh_yai_jwt.sh，页上下文刷新，Set-Cookie 回罐自持）。
   v5 职责：JWT 充足 → no-op exit 0（不再每 2h 假失败刷脏 failed 列表）；
            TTL 不足（cron 链失能）→ 委派 S2 兜底收割。flock 防并发保留。
   由 yai-rescue.timer (systemd --user) 每 2 小时触发（:23）"""
import json, os, sys, time, base64, fcntl, subprocess

TOKF = '/tmp/yai_tokens.json'
LOCK = '/tmp/yai_rescue.lock'
S2 = '/home/ai/scripts/refresh_yai_jwt.sh'   # 权威收割（9228 快速路径 → 9222 重型 自动降级）
MIN_TTL_H = 1.0                              # 剩余 <1h 才触发兜底

def log(msg):
    print(f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}", flush=True)

def exp_of(t):
    try:
        p = t.split('.')[1]; p += '=' * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get('exp', 0)
    except Exception:
        return 0

def main():
    lock_fd = open(LOCK, 'w')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log('上一轮仍在跑，跳过')
        return 0

    jwt = ''
    try:
        jwt = json.load(open(TOKF)).get('YAI_JWT', '')
    except Exception as e:
        log(f'tokens.json 不可读: {e}')

    exp = exp_of(jwt)
    ttl_h = (exp - time.time()) / 3600 if exp else -1.0
    if ttl_h >= MIN_TTL_H:
        log(f'现役 JWT 剩 {ttl_h:.1f}h（≥{MIN_TTL_H}h）→ no-op（权威链 :17 值守正常）')
        return 0

    log(f'现役 JWT 剩 {ttl_h:.1f}h < {MIN_TTL_H}h（权威链疑似失能）→ 委派 S2 兜底收割')
    r = subprocess.run(['bash', S2], capture_output=True, text=True, timeout=240)
    tail = '\n'.join((r.stdout or '').splitlines()[-6:])
    log(f'S2 exit={r.returncode}\n{tail}')
    return r.returncode

if __name__ == '__main__':
    sys.exit(main())
