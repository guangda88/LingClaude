# -*- coding: utf-8 -*-
"""M0c 班级管道：康复班XX期/教练员班XX期/师资班XX级 → 时间/地点/人物/上级机构 四向链接"""
import re, csv, json, collections

BASE = 'docs/research/panel_20261008_changshou/timeline_ext_20261009/'

CN_NUM = {'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10,'首':1,'零':0}
def cn2int(s):
    s = s.strip()
    if s.isdigit(): return int(s)
    if not s: return None
    if s == '十': return 10
    m = re.match(r'^([一二三四五六七八九])?十([一二三四五六七八九])?$', s)
    if m:
        a = CN_NUM[m.group(1)] if m.group(1) else 1
        b = CN_NUM[m.group(2)] if m.group(2) else 0
        return a*10 + b
    if s in CN_NUM: return CN_NUM[s]
    try: return int(s)
    except: return None

# 班型白名单（侦察 TOP + 智能功体系已知班型）
CLASS_TYPES = ['康复','教练员','辅导员','师资','专家教授','超常智能','暑期师生','函授',
               '刊授','骨干','儿童','提高','形神庄','捧气贯顶','三心并','直腿坐',
               '学员','教师','职工','干部','强化','研修','疗养','进修','师资函授','学习','培训']
_TYPE_ALT = '(?:' + '|'.join(CLASS_TYPES) + ')'
# 期号在班名前（主形态，59处）："第22期教练员班"/"二十九期专家教授学习班"/"第80期形神庄辅导员班"
PRE_QI_RE = re.compile(r'(?P<q>第?\s*[0-9一二三四五六七八九十百首]{1,5}\s*期)\s*(?:[\u4e00-\u9fa5]{0,3}?(?:法|功))?[\u4e00-\u9fa5]{0,4}?(?P<t>' + _TYPE_ALT + r')班')
# 班名后置期号（19处）："师资班1997年招收第六期学员"
POST_QI_RE = re.compile(r'(?P<t>' + _TYPE_ALT + r')班(?P<qi>\s*第?\s*[0-9一二三四五六七八九十百首]{1,5}\s*期)?')
QI_RE = re.compile(r'第?\s*([0-9一二三四五六七八九十百首]{1,5})\s*期')
LVL_RE = re.compile(r'(?:19|20)?(\d{2})\s*[年级]')
# 杂志期号噪声：详见本刊/《》XXXX年第N期第N页
JOURNAL_NOISE = re.compile(r'(详见本刊|本刊\s*[0-9一二三四五六七八九十]{0,3}\s*\d{4}\s*年|《[^》]{1,20}》\s*\d{4}\s*年)')
# 班名前的机构/地名/动词粘连剪枝
STRIP_PREFIX = re.compile(r'^.*?(?:培训中心|康复中心|进修学院|秦皇岛|石家庄|丰润|华夏|智能气功|气功)(?=[\u4e00-\u9fa5]*班$)')

def norm_type(t):
    t = STRIP_PREFIX.sub('', t) if len(t) > 6 else t
    if '智能气功' in t: t = t.replace('智能气功', '')
    if t.startswith('气功'): t = t[2:]
    return t

def extract_class(sentence):
    """返回 (班型, 期号或级号, 级标记) 列表。级号形态："师资95级"（学历教育按级招生）"""
    out = []
    if '班' not in sentence: return out
    if JOURNAL_NOISE.search(sentence) and '办班' not in sentence: return out
    for m in PRE_QI_RE.finditer(sentence):
        qm = QI_RE.search(m.group('q')); t = norm_type(m.group('t'))
        if t: out.append((t, cn2int(qm.group(1)) if qm else None, None))
    for m in POST_QI_RE.finditer(sentence):
        if any(m.start('t') < e and m.end('t') > st for st, e in [(mm.start(), mm.end()) for mm in PRE_QI_RE.finditer(sentence)]): continue
        t = norm_type(m.group('t'))
        if not t: continue
        qi = cn2int(QI_RE.search(m.group('qi')).group(1)) if m.group('qi') else None
        lvl = None
        pre = sentence[max(0, m.start()-8):m.start()]
        lm = re.search(r'(19|20)?(\d{2})\s*级\s*$', pre)
        if lm: lvl = lm.group(2)
        if qi is None and lvl is None:
            after = sentence[m.end():m.end()+16]
            qm = QI_RE.search(after)
            if qm: qi = cn2int(qm.group(1))
        out.append((t, qi, lvl))
    return out

# ---- 机构/地点/人物词典（复用 m0 表）----
def load_dict(path, col=0, freq_col=False):
    d = set()
    with open(path, encoding='utf-8') as f:
        rd = csv.reader(f, delimiter='\t')
        next(rd, None)
        for r in rd:
            if len(r) <= col or not r[col]: continue
            if freq_col:
                # 要求实证频次≥3（作者/正文双源或高频），杀 freq-1 误标尾
                freq = 0
                for cell in r[1:]:
                    if cell.isdigit(): freq = int(cell); break
                if freq < 3: continue
            d.add(r[col])
    return {k for k in d if len(k) >= 2}

ORGS = load_dict(BASE + 'm0_机构设施清单.tsv') | {'培训中心','康复中心','进修学院','智能气功培训中心','华夏智能气功培训中心'}
PLACES = load_dict(BASE + 'm0_地点清单v2.tsv')
PERSONS = load_dict(BASE + 'm0_人物清单v2.tsv', freq_col=True)
# 泛称班（学习/培训/函授/刊授/进修）：仅当带期号/级号才是实体，否则是活动叙述
GENERIC_TYPES = {'学习','培训','函授','刊授','进修'}
PERSON_BAD = {'子时功','中医','西医','明星','玉米','西医治疗','高达','蓝图','班超','查体','玉霞',
              '五元庄','形神庄','捧气贯顶','三心并','直腿坐','隆重召开','班招生','班自','广泛传播',
              '摘要','通知书','千里','智慧','候冲','小白','博士生','左耳','何老','刘老师'}
PERSONS = {p for p in PERSONS if p not in PERSON_BAD and not re.search(r'(治疗|研究|班$|老师$|老$|之[一二三四五]|自$|书$|者$)', p)}
PLACE_BAD = {'在石家庄','智班','受到','建立了','参加','首届','对照区','发气区','普及','华夏'}
PLACES = {p for p in PLACES if p not in PLACE_BAD and len(p) >= 2}
ORG_BAD = {'庞老师在培训中心','在培训中心','日入康复中心','日来到康复中心','个培训中心'}
# 机构键含动词/介词字（到在又更将把被向从于去）必为粘连污染键（真实机构名不含这些字）
ORGS = {o for o in ORGS if o not in ORG_BAD and not re.search(r'[到在又更将把被向从于去]', o)}
def longest_match(text, vocab, used):
    found = []
    for w in sorted(vocab, key=len, reverse=True):
        s = 0
        while True:
            i = text.find(w, s)
            if i < 0: break
            if not any(i < e and i+len(w) > st for st, e in used):
                found.append(w); used.append((i, i+len(w)))
            s = i + 1
    return found

# 默认主办机构（句中无机构时）
DEFAULT_ORG = {'康复':'华夏智能气功康复中心','疗养':'华夏智能气功康复中心','教练员':'华夏智能气功培训中心',
               '辅导员':'华夏智能气功培训中心','师资':'华夏智能气功培训中心','函授':'华夏智能气功培训中心',
               '刊授':'华夏智能气功培训中心','超常智能':'华夏智能气功培训中心','专家教授':'华夏智能气功培训中心'}

rows = [l.rstrip('\n').split('\t') for l in open(BASE + 'm1_日期句子全量.tsv', encoding='utf-8') if l.strip()]
print('句库:', len(rows))
classes = collections.defaultdict(lambda: {'cnt':0,'years':set(),'date_samples':set(),'orgs':set(),
                                           'places':set(),'persons':set(),'sents':[],'files':set()})
no_hit = 0
for r in rows:
    fp = r[0] if r else ''
    ds = r[1] if len(r) > 1 else ''
    sent = r[2] if len(r) > 2 else ''
    found = extract_class(sent)
    if not found:
        no_hit += '班' in sent and ('期' in sent or '级' in sent)
        continue
    used = []
    orgs = longest_match(sent, ORGS, used)
    places = longest_match(sent, PLACES, used)
    persons = longest_match(sent, PERSONS, used)
    ym = re.search(r'(19|20)\d{2}', ds or sent)
    yr = ym.group(0) if ym else None
    for t, qi, lvl in found:
        if t in GENERIC_TYPES and qi is None and lvl is None: continue  # 泛称班无期/级不收
        key = t + ('第%d期' % qi if qi else (lvl + '级' if lvl else '（期号未详）'))
        c = classes[key]
        c['cnt'] += 1
        c['type'] = t
        if qi: c['qi'] = qi
        if lvl: c['lvl'] = lvl
        if yr: c['years'].add(yr)
        if ds: c['date_samples'].add(ds[:24])
        if orgs: c['orgs'] |= set(orgs[:3])
        if places: c['places'] |= set(places[:3])
        if persons: c['persons'] |= set(persons[:3])
        if len(c['sents']) < 3: c['sents'].append(sent[:80])
        c['files'].add(fp.split('/')[-1][:30])

print('识别班级键:', len(classes), '（含班+期/级但未识别句:', no_hit, '）')
# ---- 输出清单 ----
def main_org(c):
    if c['orgs']: return sorted(c['orgs'], key=lambda o: -len(o))[0]
    return DEFAULT_ORG.get(c.get('type',''), '')
out = []
for k, c in classes.items():
    out.append([k, c.get('type',''), c.get('qi',''), c.get('lvl',''), c['cnt'],
                '/'.join(sorted(c['years'])), main_org(c),
                '|'.join(sorted(c['orgs'])[:3]), '|'.join(sorted(c['places'])[:3]),
                '|'.join(sorted(c['persons'])[:3]), ' ‖ '.join(c['sents'])])
out.sort(key=lambda x: (-x[4], x[1], x[0]))
with open(BASE + 'm0_班级清单.tsv', 'w', encoding='utf-8') as f:
    f.write('班级键\t班型\t期号\t级号\t出现次数\t年份\t上级机构(主)\t上级机构(句中)\t地点\t人物\t样例句\n')
    for r in out: f.write('\t'.join(str(x) for x in r) + '\n')
print('班级清单:', len(out), '条')
# ---- 节奏统计（班型×期号→年份区间）----
rhythm = collections.defaultdict(list)
for k, c in classes.items():
    if c.get('qi') and c['years']:
        for y in c['years']: rhythm[c['type']].append((c['qi'], int(y)))
print('\n班型节奏（期号×年份，推算办班密度）:')
for t, lst in sorted(rhythm.items(), key=lambda x: -len(x[1])):
    if len(lst) >= 2:
        qs = [q for q, y in lst]; ys = [y for q, y in lst]
        print(f'  {t}班: 期号{min(qs)}-{max(qs)} 年份{min(ys)}-{max(ys)} 证据{len(lst)}处')
top = out[:15]
print('\nTOP15 班级:')
for r in top: print('  %s [%s] x%s %s年 org=%s P=%s' % (r[0], r[1], r[4], r[5], r[6], r[9]))
