#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m0b v3: 作者抽取(总目录)+机构设施通道(m1句库,迭代剪枝+连词分割+净名归并) -> 清单v2合并"""
import re, csv
from collections import defaultdict

BASE = 'docs/research/panel_20261008_changshou/timeline_ext_20261009/'
raw = open('data/spill/spill_web_fetch_4n8sxqg8.txt', encoding='utf-8').read()
lines = raw.replace('\\r', '').split('\\n')

ORG_SIGN = {'本刊编辑部','编辑部','培训部','函授部','记者部','本刊记者','记者','编者','编者按','佚名',
            '超常智能班','教练组','教研组','教务处','办公室','营销部','咨询部','无名'}
NAME_RE = re.compile(r'^[\u4e00-\u9fa5·]{2,4}$')
AUTHOR_BLOCK = re.compile(r'(诊疗所|研究所|研究院|辅导站|学院|大学|专科学校|疗养院|招待所|杂志社|培训中心|康复中心|学校|礼堂|大会堂|体育馆|体育场|宾馆|饭店|大厦|基地|练功场|总站|分部|公司|医院|诊所|中心|编辑部|记者|函授|培训部|康复部|班|组|会|社|馆|所|院|部|站|室|处|局|科|团|队|厂|店)')

# ---- 1) 作者抽取 ----
year = None
authors = {}
cur_arts = []
def flush(y):
    if not y: return
    for a in cur_arts:
        d = authors.setdefault(a, {'n':0,'years':set()})
        d['n'] += 1; d['years'].add(y)
for l in lines:
    l = l.strip()
    m = re.match(r'^##\s*(\d{4})年?', l)
    if m: flush(year); cur_arts = []; year = m.group(1); continue
    m = re.match(r'^\d{2,3}\s+(.+)$', l)
    if not m: continue
    am = re.search(r'（([^（）]{1,14})）\s*$', m.group(1).strip())
    if not am: continue
    for p in [x.strip(' 、/,') for x in re.split(r'[、\s/]+', am.group(1).strip()) if x.strip(' 、/,')]:
        if p in ORG_SIGN or not NAME_RE.match(p) or AUTHOR_BLOCK.search(p): continue
        cur_arts.append(p)
flush(year)
print(f'[作者] 人名署名 {len(authors)} / 篇次 {sum(v["n"] for v in authors.values())}')

# ---- 2) 机构设施通道 ----
LEAD = re.compile(r'^(集体参加了?|一起参加了?|参加了?|明确了?|处就是这|就是这|就是|曾是|曾在|就诊于|就诊|达[到]?|来到?|来到达|来|到了?|去了?|回到?|进了?|入了?|经|由|从|赴|抵|与|和|及|同|的是|这是|这|在|于)')
VERB2 = {'建立','成立','设立','创办','举办','召开','举行','位于','坐落','设在','建于','前往','抵达','回到','迁至','落户','并入','评为','获评','出席','参加','来到','迁到','并入'}
STOP1 = set('年月日号了是我他她它你其此这那有个每诸各逢值当将把被使让给与和同跟对向及并与的到在')

def prune(name):
    changed = True
    while changed and name:
        changed = False
        m = LEAD.match(name)
        if m and len(name) - len(m.group(0)) >= 4:
            name = name[len(m.group(0)):]; changed = True; continue
        hit = False
        for v in VERB2:
            if name.startswith(v) and len(name) - 2 >= 4:
                name = name[2:]; changed = True; hit = True; break
        if hit: continue
        while name and name[0] in STOP1 and len(name) > 4:
            name = name[1:]; changed = True
    return name

CONNECT = re.compile(r'[和与及同、]')
SUFFIX = ('培训中心','康复中心','研究中心','辅导总站','辅导站','进修学院','研修学院','专科学校','疗养院','疗养所','研究所','研究院',
          '礼堂','大会堂','体育馆','体育场','宾馆','饭店','招待所','大厦','基地','练功场','学院','大学','中专','学校','总站','分部','公司','医院')
GENERIC = {'培训中心','康复中心','辅导站','辅导总站','总站','分部','康复部','培训部','疗养院','招待所','练功场','学校','医院','学院','大学','宾馆','研究中心','研究所','公司','附属医院','肿瘤医院'}
SUFPAT = re.compile(r'([\u4e00-\u9fa5]{0,12}(?:' + '|'.join(SUFFIX) + r'))')
orgs = defaultdict(lambda: {'n':0,'years':set(),'ex':''})
n = 0
with open(BASE+'m1_日期句子全量.tsv', encoding='utf-8') as f:
    for row in csv.reader(f, delimiter='\t'):
        if len(row) < 3: continue
        n += 1
        ym = re.search(r'(19|20)\d{2}', row[1])
        y = ym.group(0) if ym else ''
        seen = set()
        for mm in SUFPAT.finditer(row[2]):
            for piece in CONNECT.split(mm.group(1)):
                name = prune(piece)
                mb = re.search(r'(?:了|给|是|得知|总结|考入|引进|陪同|率领|带领)([一-龥]{4,})$', name)
                if mb: name = mb.group(1)
                if len(name) < 4 or name in seen or name in GENERIC: continue
                if re.match(r'^\d', name) or not re.search(r'(中心|学院|大学|学校|站|部|所|院|馆|堂|场|司|校|部)', name[-3:]): continue
                seen.add(name)
                d = orgs[name]; d['n'] += 1
                if y: d['years'].add(y)
                if not d['ex']: d['ex'] = row[2][:60]
print(f'[机构] 句库 {n} 句 -> 剪枝后 {len(orgs)} 个')

# 归并: 后缀聚类, 正名=簇内个人频次最高者(平手取短), 泛称不吸附他人
GENERIC = {'培训中心','康复中心','辅导站','辅导总站','总站','分部','康复部','培训部','疗养院','招待所','练功场','学校','医院','学院','大学','宾馆','研究中心','研究所','公司','附属医院','肿瘤医院'}
clusters = defaultdict(list)
for a in orgs:
    if a in GENERIC: clusters[a].append(a); continue
    host = next((b for b in sorted(orgs, key=len) if b != a and b.endswith(a) and len(b)-len(a) >= 2 and b not in GENERIC), a)
    clusters[host].append(a)
merged = {}
for host, members in clusters.items():
    canon = max(members, key=lambda m: (orgs[m]['n'], -len(m)))
    d = {'n':0,'years':set(),'ex':'','alias':set()}
    for m in members:
        d['n'] += orgs[m]['n']; d['years'] |= orgs[m]['years']; d['alias'].add(m)
        if len(orgs[m]['ex']) > len(d['ex']): d['ex'] = orgs[m]['ex']
    d['alias'].discard(canon)
    merged[canon] = d
print(f'[归并] {len(orgs)} -> {len(merged)} 键')

# ---- 3) v1 合并 ----
def load(fn):
    with open(BASE+fn, encoding='utf-8') as f: return list(csv.reader(f, delimiter='\t'))
ppl = load('m0_人物清单.tsv'); loc = load('m0_地点清单.tsv')
ph, phead = ppl[0], ppl[1:]; lh, lhead = loc[0], loc[1:]
pname = {r[0] for r in phead}; lname = {r[0] for r in lhead}
newp = [[nm, str(d['n']), '杂志署名', ';'.join(sorted(d['years'])), '', ''] for nm, d in authors.items() if nm not in pname]
newl = [[nm, '|'.join(sorted(d['alias'])[:4]), '机构设施', str(d['n']), '', d['ex'][:40]] for nm, d in sorted(merged.items(), key=lambda x: -x[1]['n']) if nm not in lname]
print(f'[合并] 人物 +{len(newp)} / 地点 +{len(newl)}')

with open(BASE+'m0_人物清单v2.tsv','w',encoding='utf-8',newline='') as f:
    w = csv.writer(f, delimiter='\t'); w.writerow(ph); w.writerows(phead); w.writerows(newp)
with open(BASE+'m0_地点清单v2.tsv','w',encoding='utf-8',newline='') as f:
    w = csv.writer(f, delimiter='\t'); w.writerow(lh); w.writerows(lhead); w.writerows(newl)
with open(BASE+'m0_作者清单.tsv','w',encoding='utf-8',newline='') as f:
    w = csv.writer(f, delimiter='\t'); w.writerow(['作者','署名篇次','出现年份','首年','末年'])
    for nm, d in sorted(authors.items(), key=lambda x: -x[1]['n']):
        yrs = sorted(d['years']); w.writerow([nm, d['n'], ';'.join(yrs), yrs[0], yrs[-1]])
with open(BASE+'m0_机构设施清单.tsv','w',encoding='utf-8',newline='') as f:
    w = csv.writer(f, delimiter='\t'); w.writerow(['机构','别名','类型','句频','年份','例句'])
    for nm, d in sorted(merged.items(), key=lambda x: -x[1]['n']):
        w.writerow([nm, '|'.join(sorted(d['alias'])[:4]), '机构设施', d['n'], ';'.join(sorted(d['years'])[:10]), d['ex'][:50]])
print('[OK] 4 文件落盘')
