#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m0d: 宿主新增三份时间轴材料并入管线
输入: /home/ai/lingyi/znqg-timeline/时间轴（大事记）-{讲课时间,师资班讲课,著作年表}.csv (UTF-8 BOM)
输出(工作区 timeline_ext_20261009/):
  m0d_讲课资产索引.tsv  683资产行 -> 去重会话(wav/docx/mp3同场归并)
  m0d_生平年表.tsv      204叙述行
  m0d_著作年表.tsv      60种著作(含笔名/出版社/ISBN)
  m0d_师资班课程表.tsv  5课程块x日期段 + 讲次分段
  时间轴（大事记）v5.csv v4+出版事件(事件级去重,双源保留)
  m0d_并入报告.md       冲突与统计
"""
import re, csv, json, hashlib, collections, os

SRC = '/home/ai/lingyi/znqg-timeline'
OUT = 'docs/research/panel_20261008_changshou/timeline_ext_20261009'
os.makedirs(OUT, exist_ok=True)

def read_bom(name):
    with open(os.path.join(SRC, name), encoding='utf-8-sig') as f:
        return [r for r in csv.reader(f)]

def wtxt(name, text):
    p = os.path.join(OUT, name)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(text)
    return p

report = []

# ============ 1. 讲课时间 ============
rows = [r[1].strip() for r in read_bom('时间轴（大事记）-讲课时间.csv') if len(r) > 1 and r[1].strip()]
EXT = re.compile(r'\.(wav|mp3|wma|docx|doc|txt|pdf|xls)\s*$', re.I)
assets_raw = [t for t in rows if EXT.search(t)]
narr = [t for t in rows if not EXT.search(t)]

DATE_RE = re.compile(r'^(\d{4})(?:[.\-年](\d{1,2}))?(?:[.\-月](\d{1,2}))?')
def parse_date(t):
    m = DATE_RE.match(t)
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2), m.group(3)
    return (int(y), int(mo) if mo else None, int(d) if d else None)

def split_asset(t):
    """时间_地点_主题.ext  (时间与地点段以 _ 分隔, 主题段可含_)"""
    m = EXT.search(t)
    ext = m.group(1).lower()
    body = t[:m.start()]
    parts = [p.strip() for p in body.split('_')]
    date = None
    # 第0段应为日期(可能带尾随空格/年月日)
    if parts and DATE_RE.match(parts[0]):
        date = parse_date(parts[0])
        parts = parts[1:]
    loc = parts[0] if parts else ''
    topic = '_'.join(parts[1:]) if len(parts) > 1 else (parts[0] if parts else '')
    return date, loc, topic, ext

def norm_key(loc, topic):
    """同场讲课归并键: 剥14位时间戳后缀/空白/全半角"""
    def n(s):
        s = re.sub(r'20\d{12}', '', s)
        return re.sub(r'[\s_:：·・]+', '', s)
    return n(loc) + '|' + n(topic)

sessions = {}
for t in assets_raw:
    date, loc, topic, ext = split_asset(t)
    k = norm_key(loc, topic)
    if k not in sessions:
        sessions[k] = {'date': date, 'loc': loc, 'topic': topic, 'carriers': [],
                       'raws': []}
    if ext not in sessions[k]['carriers']:
        sessions[k]['carriers'].append(ext)
    sessions[k]['raws'].append(t)

n_media = sum(1 for s in sessions.values() if any(c in ('wav', 'mp3', 'wma') for c in s['carriers']))
n_docx = sum(1 for s in sessions.values() if any(c in ('docx', 'doc', 'txt', 'pdf') for c in s['carriers']))
pair = sum(1 for s in sessions.values() if len(s['carriers']) >= 2)
report.append(f'讲课时间: 资产行 {len(assets_raw)} -> 归并会话 {len(sessions)} (含音频 {n_media} / 含文稿 {n_docx} / 双载体同场 {pair})')

# 资产索引 TSV
lines = ['date\tyear\tmonth\tday\t地点段\t主题\t载体\t原始行数\t样例原始行']
for s in sessions.values():
    d = s['date'] or (None, None, None)
    lines.append(f"{d[0] or ''}-{d[1] or ''}-{d[2] or ''}\t{d[0] or ''}\t{d[1] or ''}\t{d[2] or ''}\t{s['loc']}\t{s['topic']}\t{'+'.join(s['carriers'])}\t{len(s['raws'])}\t{s['raws'][0][:100]}")
wtxt('m0d_讲课资产索引.tsv', '\n'.join(lines) + '\n')

# 生平年表 TSV (叙述行)
lines = ['year\tdate_raw\t叙述']
noyear = 0
for t in narr:
    m = DATE_RE.match(t)
    y = m.group(1) if m else ''
    if not y:
        noyear += 1
    dm = re.match(r'^(\d{4}(?:[.\-年]\d{1,2})?(?:[.\-月]\d{1,2})?)[年\s]*', t)
    lines.append(f"{y}\t{dm.group(1) if dm else ''}\t{t}")
wtxt('m0d_生平年表.tsv', '\n'.join(lines) + '\n')
report.append(f'生平年表(叙述行): {len(narr)} (无年份开头 {noyear} 行保留原文)')

# ============ 2. 师资班讲课 ============
crows = [r[0].strip() for r in read_bom('时间轴（大事记）-师资班讲课.csv') if r and r[0].strip()]
CN = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
      '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15, '十六': 16, '十七': 17, '十八': 18,
      '十九': 19, '二十': 20}
course_re = re.compile(r'^[①②③④⑤]([^\.①②③④⑤\d]*?)[\.]*\s*(?:\d+\s*-\s*\d+\s*讲)?\s*(\d{2})\.(\d{1,2})(?:\.(\d{1,2}))?\s*[-—–~至]\s*(\d{2})[\.:\s](\d{1,2})(?:[\. :](\d{1,2}))?')  # end 容忍 12:14 冒号
course_re2 = re.compile(r'^[①②③④⑤]([^\.①②③④⑤\d]*?)[\.]*\s*(?:\d+\s*-\s*\d+\s*讲)?\s*(\d{2})\.(\d{1,2})(?:\.(\d{1,2}))?\s*[-—–~至]\s*(\d{1,2})[、](\d{1,2})(?![\.\d])')  # 仅顿号省写: 93.3-93.6、18; 前瞻防吞 92.11.30
lec_re = re.compile(r'^第([0-9一二三四五六七八九十]+)讲[:：](.+?)(?:（(\d+[-—–]\d+)）)?\s*$')

def cn2int(s):
    if s.isdigit():
        return int(s)
    if s in CN:
        return CN[s]
    if '十' in s:
        a, _, b = s.partition('十')
        return (CN.get(a, 1) if a else 1) * 10 + (CN.get(b, 0) if b else 0)
    return -1
courses, lectures = [], []
for t in crows:
    m2 = course_re2.match(t)
    if m2:
        g2 = m2.groups()
        courses.append({'name': g2[0], 'start': f"19{g2[1]}-{int(g2[2]):02d}-{int(g2[3] or 1):02d}",
                        'end': f"19{g2[1]}-{int(g2[4]):02d}-{int(g2[5]):02d}", 'raw': t})
        continue
    m = course_re.match(t)
    if m:
        g = m.groups()
        # end 段歧义消解: g[4]=start年或>12 -> 年份形态; 否则(<=12且≠start年)继承start年视为月
        if g[4] == g[1] or int(g[4]) > 12:
            end_s = f"19{g[4]}-{int(g[5]):02d}-{int(g[6] or 28):02d}"
        else:
            end_s = f"19{g[1]}-{int(g[4]):02d}-{int(g[5] or 28):02d}"
        courses.append({'name': g[0], 'start': f"19{g[1]}-{int(g[2]):02d}-{int(g[3] or 1):02d}",
                        'end': end_s, 'raw': t})
        continue
    m = lec_re.match(t)
    if m:
        num = m.group(1)
        n = cn2int(num)
        lectures.append({'n': n, 'title': m.group(2), 'pages': m.group(3) or '', 'raw': t})
# 讲次分段: 序号回落(第1讲/第一讲重新出现)即新课程段
segs, cur = [], []
prev = 0
for L in lectures:
    if L['n'] == 1 and prev != 0:
        segs.append(cur)
        cur = []
    cur.append(L)
    prev = L['n']
if cur:
    segs.append(cur)
# 课程块与段按序配对
lines = ['course\tstart\tend\t讲数\t首讲\t末讲\traw']
for i, c in enumerate(courses):
    seg = segs[i] if i < len(segs) else []
    first = seg[0]['title'][:20] if seg else ''
    last = seg[-1]['title'][:20] if seg else ''
    lines.append(f"{c['name']}\t{c['start']}\t{c['end']}\t{len(seg)}\t{first}\t{last}\t{c['raw']}")
wtxt('m0d_师资班课程表.tsv', '\n'.join(lines) + '\n')
report.append(f'师资班讲课: 课程块 {len(courses)} / 讲次 {len(lectures)} / 识别分段 {len(segs)} (段-块按序配对)')

# ============ 3. 著作年表 -> v5 ============
brows = read_bom('时间轴（大事记）-著作年表.csv')
books = []
for r in brows:
    if r and r[0].strip().isdigit():
        books.append({'no': int(r[0]), 'title': (r[1] or '').strip(), 'author': (r[2] or '').strip(),
                      'pub': (r[3] or '').strip().replace('\\n', '/'), 'isbn': (r[4] or '').strip(),
                      'ptime': (r[5] or '').strip(), 'words': (r[6] or '').strip(),
                      'pages': (r[7] or '').strip(), 'note': (r[8] or '').strip() if len(r) > 8 else ''})
lines = ['no\t书名\t署名\t出版单位\tISBN\t出版时间\t字数(千)\t页码\t备注']
for b in books:
    lines.append('\t'.join([str(b['no']), b['title'], b['author'], b['pub'], b['isbn'],
                            b['ptime'], b['words'], b['pages'], b['note']]))
wtxt('m0d_著作年表.tsv', '\n'.join(lines) + '\n')
report.append(f'著作年表: {len(books)} 种 (含1905疑似OCR错字1条, 见报告)')

# v4 读取
with open(os.path.join(OUT, '时间轴（大事记）v4.csv'), encoding='utf-8') as f:
    v4 = list(csv.reader(f))
header = v4[0]
print('v4表头:', header)

def pub_year(pt):
    m = re.search(r'(\d{4})', pt)
    return int(m.group(1)) if m else None

# 与 v4 既有书名去重(事件级: 记事含书名且同年)
book_titles = [b['title'] for b in books if b['title']]
def dup_in_v4(title, y):
    for row in v4[1:]:
        if len(row) > 1 and title and title in row[1]:
            my = re.search(r'(\d{4})', row[0] or '')
            if my and int(my.group(1)) == y:
                return True
    return False

new_events, dup_events = [], []
for b in books:
    y = pub_year(b['ptime'])
    if not y or not b['title']:
        continue
    if y == 1905:
        report.append(f"[冲突/疑OCR] 著作{b['no']}《{b['title']}》出版时间={b['ptime']}, 书号={b['isbn']}(1992年准印)——1905 不并入 v5, 保留标记")
        continue
    if dup_in_v4(b['title'], y):
        dup_events.append(f"{b['title']}({y})")
        continue
    ev = f"{b['ptime']}《{b['title']}》由{b['pub'] or '?'}出版。署名:{b['author'] or '—'}"
    if b['note']:
        ev += f"。备注:{b['note']}"
    new_events.append((b['ptime'], ev, '著作年表(宿主20261009)'))
report.append(f"出版事件并入: 新增 {len(new_events)} / 与v4同年同书名去重 {len(dup_events)} -> {dup_events}")

# 气功探邃 年代冲突检查
tansui = [row for row in v4[1:] if len(row) > 1 and '气功探邃' in row[1]]
report.append(f"[冲突]《气功探邃》v4既有条目年份: {[r[0] for r in tansui]} vs 著作年表1985.6(贺明署名,内部出版)——双源保留")

# v5 组装: 复用 v4 列结构, 追加出版事件行再排序
col_t = 0
ev_rows = []
for (pt, ev, src) in new_events:
    row = [''] * len(header)
    row[col_t] = pt
    row[1] = ev
    for i, h in enumerate(header):
        if '来源' in h:
            row[i] = src
    ev_rows.append(row)
body = v4[1:] + ev_rows
def sort_key(row):
    m = re.match(r'^(\d{4})(?:[.\-年](\d{1,2}))?(?:[.\-月](\d{1,2}))?', (row[0] or '').strip())
    if not m:
        return (9999, 99, 99)
    return (int(m.group(1)), int(m.group(2)) if m.group(2) else 99, int(m.group(3)) if m.group(3) else 99)
body.sort(key=sort_key)
with open(os.path.join(OUT, '时间轴（大事记）v5.csv'), 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(header)
    w.writerows(body)
report.append(f'v5 时间轴: v4 {len(v4)-1} 条 + 出版事件 {len(new_events)} = {len(body)} 条 (按三级粒度重排)')

# ============ 4. 报告 ============
md = '# m0d 三份新材料并入报告 (2026-10-09)\n\n来源: /home/ai/lingyi/znqg-timeline/时间轴（大事记）-{讲课时间,师资班讲课,著作年表}.csv (宿主 08:19-08:20 拷入, UTF-8 BOM)\n\n## 统计\n'
md += '\n'.join(f'- {r}' for r in report) + '\n'
md += '''
## 数据冲突登记(双源保留,不静默改)
1. 《气功探邃》: v4 既有 1981/1983 条目 vs 著作年表 1985年6月(署名贺明,河北省老年人体协/石家庄气功辅导总站,内部出版)——两者可并存(多版次), 待口述访谈侧证
2. 《智能气功功法学》(著作30): 出版时间"1905年6月"疑似OCR错字, 书号=冀新出准印字[1992]第054号 → 大概率1992年6月, **未并入v5**(宁缺毋滥)
## 笔名考证线索
著作年表署名分布: 庞明34/庞鹤鸣9/甄隐2/尹真2/贺明1/庞明编著潘明环整理2/唐德福编1/空3 —— 贺明/甄隐/尹真/庞鹤鸣 为庞明笔名/化名, 图谱层统一挂 P:庞明 并保留原署名属性
## 讲课资产(ASR支线直接可用)
wav 449 + mp3 51 条音频索引(同场多载体已归并), 去重后见 m0d_讲课资产索引.tsv —— M0 语料采集的现成资产清单
'''
wtxt('m0d_并入报告.md', md)
print('\n'.join(report))
print('完成')
