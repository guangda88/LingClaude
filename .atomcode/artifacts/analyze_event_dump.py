#!/usr/bin/env python3
"""解析 opencode db 备份转储: event 表按类型/会话聚合, 定位增长放大源"""
import sys, re
from collections import Counter

TYPE_CNT = Counter(); TYPE_BYTES = Counter(); SAMPLES = {}
SESS_CNT = Counter(); SESS_BYTES = Counter()
BY_DATE = Counter()  # 从 event id hex 时间戳还原日期分布

pat = re.compile(r"^INSERT INTO event VALUES\('([^']+)',"      # id
                 r"'([^']+)',"                                  # aggregate_id (session)
                 r"(\d+),"                                      # seq
                 r"'([^']+)',"                                  # type
                 r"(.*)\);$")                                   # data (贪婪, 含转义'' )

for line in sys.stdin:
    m = pat.match(line.rstrip('\n'))
    if not m:
        continue
    eid, agg, seq, etype, data = m.groups()
    n = len(data)
    TYPE_CNT[etype] += 1
    TYPE_BYTES[etype] += n
    SESS_CNT[agg] += 1
    SESS_BYTES[agg] += n
    if etype not in SAMPLES and n > 300:
        SAMPLES[etype] = data[:200]
    mm = re.match(r"evt_([0-9a-f]+)", eid)
    if mm:
        try:
            t = int(mm.group(1)[:11], 16)  # hex ms 时间戳
            if 1.5e12 < t < 1.9e12:
                import datetime
                d = datetime.datetime.fromtimestamp(t/1000).strftime('%Y-%m-%d')
                BY_DATE[d] += n
        except Exception:
            pass

print('== 按事件类型: count / 总MB / 平均字节 ==')
for t, c in TYPE_CNT.most_common(20):
    print(f"{t:42s} n={c:6d} {TYPE_BYTES[t]/1e6:9.1f}MB avg={TYPE_BYTES[t]//c:7d}B")
    if t in SAMPLES:
        print('    sample:', SAMPLES[t][:180])
print()
print('== 会话 Top8 (事件数/字节) ==')
for s, c in SESS_CNT.most_common(8):
    print(f"{s} n={c:6d} {SESS_BYTES[s]/1e6:8.1f}MB")
print()
print('== 按日期的字节分布 (hex时间戳还原) ==')
for d in sorted(BY_DATE):
    print(f"{d}  {BY_DATE[d]/1e6:9.1f}MB")
