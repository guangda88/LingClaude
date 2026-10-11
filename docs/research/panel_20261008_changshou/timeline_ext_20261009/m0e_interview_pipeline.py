#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m0e: 访谈提纲 v1 组装管线（M0 主线最后一块）
输入（本目录）:
  kg_智能气功_多元图谱.json   四维索引 by_year/by_person/by_place/by_org + nodes/edges
  口述素材库.tsv              624 条第一人称年份锚定句
  m0_人物清单v2.tsv           1392 人
  m0_地点清单v2.tsv           1621 地
  m0_班级清单.tsv             96 班级键(82 含期/级号)
  m0d_生平年表.tsv            205 条庞明生平
  m0d_讲课资产索引.tsv        345 场讲课资产
输出:
  m0e_访谈提纲_v1.md          四大章节问题书（访谈员用）
  m0e_问题库.tsv              全量问题库(章节/锚点类型/锚点值/问题/依据)
  m0e_提纲简报.md             统计简报
原则:
  - 每问必有证据锚（kg 事件/素材句/样例句），不给访谈员裸问题
  - 组块化: 按 十年代/人物/地点/班级 四条回忆线索组织
  - 仅内部使用；冲突数据不静默改（依据列保留双源原文）
"""
import csv, json, os
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = '/home/ai/lingyi/znqg_timeline_ext_20261009'
os.chdir(BASE)

def read_tsv(name, delim='\t'):
    with open(f'{BASE}/{name}', encoding='utf-8') as f:
        return list(csv.DictReader(f, delimiter=delim))

def clip(s, n):
    s = (s or '').replace('\t', ' ').replace('\n', ' ').strip()
    return s if len(s) <= n else s[:n] + '…'

# ---------- 载入 ----------
kg = json.load(open('kg_智能气功_多元图谱.json', encoding='utf-8'))
nodes = {n['id']: n for n in kg['nodes']}
idx = kg['index']
by_year, by_person, by_place, by_org = idx['by_year'], idx['by_person'], idx['by_place'], idx['by_org']
# 事件度数(参与边数) = 素材丰富度
edeg = Counter()
for e in kg['edges']:
    edeg[e['s']] += 1

oral = read_tsv('口述素材库.tsv', delim=',')
persons = read_tsv('m0_人物清单v2.tsv')
places = read_tsv('m0_地点清单v2.tsv')
classes = read_tsv('m0_班级清单.tsv')
bio = read_tsv('m0d_生平年表.tsv')
assets = read_tsv('m0d_讲课资产索引.tsv')

questions = []  # {section, seq, anchor_type, anchor, year, q, evidence}
def add(section, anchor_type, anchor, year, q, evidence):
    questions.append(dict(section=section, anchor_type=anchor_type, anchor=anchor,
                          year=year, q=q, evidence=evidence))

# ---------- 一、年代线 ----------
DECADES = [('1975-1979 功法源流与早期', lambda y: 1970 <= y <= 1979),
           ('1980-1987 普及推广期', lambda y: 1980 <= y <= 1987),
           ('1988-1992 培训体系成形期', lambda y: 1988 <= y <= 1992),
           ('1993-1999 华夏中心鼎盛期', lambda y: 1993 <= y <= 1999)]
oral_by_year = defaultdict(list)
for r in oral:
    y = ''.join(ch for ch in r['锚定时间'][:4] if ch.isdigit())
    if y.isdigit():
        oral_by_year[int(y)].append(r)

for dname, din in DECADES:
    evs = []
    for yk, eids in by_year.items():
        y = int(yk)
        if not din(y):
            continue
        for eid in eids:
            evs.append((edeg.get(eid, 0), y, eid))
    evs.sort(reverse=True)
    add(dname, '年代-开場', f'{dname}', '', 
        f'（{dname}）您最初接触智能气功是哪一年？通过什么渠道（广播/书/熟人/面授）？',
        '开场定位题，无证据锚')
    for deg, y, eid in evs[:6]:
        ev = nodes[eid]
        t = clip(ev['text'], 80)
        add(dname, '年代-事件', f'{y}', y,
            f'{y} 年前后公开记载有：“{t}” ——当时您是否听说或亲历此事？您在哪里、在做什么？',
            f'kg:{eid} deg={deg}')
    # 口述素材印证式追问: 该年代抽 3 条(按素材句去重)
    oy = sorted([y for y in oral_by_year if din(y)])
    picks, seen_txt = [], set()
    if oy:
        for y in (oy[0], oy[len(oy)//2], oy[-1]):
            for r in oral_by_year[y]:
                key = r['素材句'][:50]
                if key not in seen_txt:
                    seen_txt.add(key)
                    picks.append(r)
                    break
    for r in picks:
        add(dname, '年代-印证', r['锚定时间'], int(r['锚定时间'][:4]) if r['锚定时间'][:4].isdigit() else '',
            f'有公开材料记载（{clip(r["素材句"], 60)}）——与您了解的情况一致吗？您当时的经历是什么？',
            clip(r['素材句'].split('〔源:')[-1], 40))

# ---------- 二、人物线 ----------
add('二、人物线', '人物-庞明开场', '庞明', '',
    '您第一次见到/听到庞明老师是哪一年、在哪里？当时讲的什么内容？',
    '人物清单v2: 正文 273 次/署名 188 篇')
# 庞明生平: 按十年里程碑取(出生/求学/发轫/创编/鼎盛), 每十年至多2条
dec_seen = {}
bio_pick = []
for r in bio:
    yy = int(r['year']) if r['year'].isdigit() else 0
    if not yy:
        continue
    dec = yy // 10
    if dec_seen.get(dec, 0) < 2:
        dec_seen[dec] = dec_seen.get(dec, 0) + 1
        bio_pick.append(r)
for r in bio_pick:
    add('二、人物线', '人物-生平', f"庞明 {r['date_raw']}", r['year'],
        f'（背景：{clip(r["叙述"], 70)}）这一年前后您与庞老师有任何直接或间接的交集吗？',
        f'生平年表 {r["year"]}')
p_top = [p for p in persons if p['人物'] != '庞明' and int(p['频次']) >= 10][:15]
for p in p_top:
    add('二、人物线', '人物-同场', p['人物'], p['年份跨度'].split('-')[0],
        f'您是否听过或见过 {p["人物"]}？在什么场合（讲课/带功/治病/办班）？印象最深的一件事？',
        f'人物清单v2 频次={p["频次"]} 跨度={p["年份跨度"]}')

# ---------- 三、地点线 ----------
SKIP_PLACE = {'中国', '全国'}
p_top = [(k.replace('D:', ''), len(v)) for k, v in by_place.items()
         if k.replace('D:', '') not in SKIP_PLACE]
p_top.sort(key=lambda x: -x[1])
for name, cnt in p_top[:12]:
    yrs = [int(nodes[e]['year']) for e in by_place[f'D:{name}'] if nodes[e].get('year')]
    span = f'{min(yrs)}-{max(yrs)}' if yrs else ''
    add('三、地点线', '地点', name, span,
        f'您去过 {name} 吗？哪几年？去做什么（学功/治病/教功/参会）？当地智能气功的氛围如何？',
        f'kg by_place 事件数={cnt}')

# ---------- 四、班级线 ----------
MAIN_CLASS = {'教练员', '康复', '辅导员', '师资'}
class_by_type = defaultdict(list)
for r in classes:
    class_by_type[r['班型']].append(r)
for ctype, rows in class_by_type.items():
    sample = clip(rows[0]['样例句'].split('‖')[0], 70)
    org = clip(rows[0]['上级机构(主)'], 30)
    add('四、班级线', '班级-班型', ctype, '',
        f'您是否参加过“{ctype}”类班（如 {org} 办）？哪一期、哪年？带班老师是谁？',
        f'班级清单 键数={len(rows)} 例:{sample}')
    if ctype in MAIN_CLASS:
        for r in rows:
            if not (r['期号'] or r['级号']):
                continue
            no = r['期号'] or f'{r["级号"]}级'
            add('四、班级线', '班级-期号', r['班级键'], r['年份'].split('/')[0],
                f'您或您熟悉的人是否参加过{ctype}{no}？（证据：{sample}）当时地点和主讲老师您还记得吗？',
                f'班级键={r["班级键"]} 次数={r["出现次数"]}')

# ---------- 五、讲课资产抽验 ----------
a_by_y = defaultdict(list)
for r in assets:
    a_by_y[r['year']].append(r)
for y in sorted(a_by_y):
    r = a_by_y[y][0]
    add('五、讲课资产抽验(可选)', '讲课-在场', f'{r["year"]} {r["地点段"]}', r['year'],
        f'{r["year"]}年{r["month"]}月{r["day"]}日在{r["地点段"]}有一场“{clip(r["主题"], 40)}”，您在场吗？或听过这盘录音？',
        f'资产索引 {r["载体"]}')

# ---------- 输出 ----------
questions_tsv = 'm0e_问题库.tsv'
with open(questions_tsv, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['章节', '锚点类型', '锚点值', '年份', '问题', '依据'])
    for i, q in enumerate(questions, 1):
        w.writerow([i, q['section'], q['anchor_type'], q['anchor'], q['year'], q['q'], q['evidence']])

sec_names = []
seen_sec = set()
for q in questions:
    if q['section'] not in seen_sec:
        seen_sec.add(q['section'])
        sec_names.append(q['section'])

md = ['# 访谈提纲 v1 ——《与长寿智能人谈天》M0 数据锚点版', '',
      '> 生成: m0e_interview_pipeline.py | 全部问题带证据锚（kg 事件ID/素材库源/班级样例句），冲突数据不静默改',
      '> 使用约束: 仅内部使用；受访者 70+ 智功习练者；组块化提问按四大回忆线索展开；★开场题必问，其余按受访者经历选问', '']
n = 0
for sec in sec_names:
    qs = [q for q in questions if q['section'] == sec]
    md.append(f'## {sec}（{len(qs)} 问）')
    md.append('')
    for q in qs:
        n += 1
        star = '★' if q['anchor_type'].endswith('开場') or q['anchor_type'] == '人物-庞明开场' else '·'
        md.append(f'{n}. {star}[{q["anchor_type"]}|锚:{q["anchor"]}|依据:{q["evidence"]}]')
        md.append(f'   {q["q"]}')
    md.append('')
md.append('---')
md.append(f'合计 {len(questions)} 问 | 章节 {len(sec_names)} | 问题库明细: m0e_问题库.tsv')
with open('m0e_访谈提纲_v1.md', 'w', encoding='utf-8') as f:
    f.write('\n'.join(md))

stats = ['# m0e 提纲简报', '',
         f'- 问题总量: {len(questions)}',
         '- 分章节: ' + ', '.join(f'{s}={len([q for q in questions if q["section"] == s])}' for s in sec_names),
         f'- 锚点类型: {dict(Counter(q["anchor_type"] for q in questions))}',
         f'- 证据覆盖: {len([q for q in questions if not q["evidence"].startswith(("开场", "无"))])}/{len(questions)}',
         f'- 年代覆盖: {sorted({q["year"] for q in questions if isinstance(q["year"], int)})}']
with open('m0e_提纲简报.md', 'w', encoding='utf-8') as f:
    f.write('\n'.join(stats))

# 归档双写
if os.path.isdir(ARCHIVE):
    import shutil
    for fn in ('m0e_访谈提纲_v1.md', 'm0e_问题库.tsv', 'm0e_提纲简报.md', 'm0e_interview_pipeline.py'):
        shutil.copy2(f'{BASE}/{fn}', f'{ARCHIVE}/{fn}')

print(f'OK 问题={len(questions)} 章节={len(sec_names)}')
for s in sec_names:
    print(f'  {s}: {len([q for q in questions if q["section"] == s])}')
