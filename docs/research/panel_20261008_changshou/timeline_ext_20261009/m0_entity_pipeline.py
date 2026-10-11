#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# M0: 从 m1 带日期句子库采集 人物+地点 实体，与时间轴 v4 按年份映射
import csv, re
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path('/home/ai/lingclaude/docs/research/panel_20261008_changshou/timeline_ext_20261009')
SENT = BASE / 'm1_日期句子全量.tsv'
V4 = BASE / '时间轴（大事记）v4.csv'
HOT = BASE / '智能气功专业热词2000个.txt'

import jieba
import jieba.posseg as pseg
try:
    jieba.setLogLevel(60)
except Exception:
    pass

YEAR_RE = re.compile(r'(19[5-9]\d|20[01]\d)')
PROVINCES = ['黑龙江', '内蒙古', '宁夏', '新疆', '西藏', '上海', '北京', '天津', '重庆',
             '河北', '山西', '辽宁', '吉林', '江苏', '浙江', '安徽', '福建', '江西',
             '山东', '河南', '湖北', '湖南', '广东', '海南', '四川', '贵州', '云南',
             '陕西', '甘肃', '青海', '台湾', '广西', '中国']
P_BAD = set('有的 希望 所以 但是 这个 那个 我们 你们 他们 自己 大家 时候 可以 应该 就是 '
            '上面 下面 前面 后面 一些 每次 一样 已经 不是 还有 然后 于是 因此 因为 如果 '
            '虽然 此外 另外 例如 其他 其它 当前 今后 后来 开始 结束 地方 情况 问题 方法 '
            '理论 功理 功能 智能 气功 混元 意念 两个 三种 其中 只有 必须 以及 同志 '
            '学员 老师 学生 先生 大夫 医生 病人 人们 人家 本人 原来 突然 忽然 '
            '练功 康复 师生 探邃 学习 研究 工作 生活 交流 讨论 培训 办班 治疗 养生 '
            '健身 治病 身心 历史 过程 情况 方式 体系 层次 形式 内容 含义 特点 '
            '条件 效果 作用 发展 提高 建设 成立 组织 科学 文化 教育 教学 训练 '
            '群众 领导 干部 专家 学者 记者 作者 读者 子宫 高潮 病例 典型 '
            '西医 保健 荣获 老同志 玉米 明星 大力支持 广大群众 老太太 '
            '大小便 麻木 丰年 经人介绍 老前辈 同行们 各界 全体 许多 许多'.split())
PLACE_BAD = set('对照区 发气区 实验区 气功区 全区 该区 本区 场 全 个 在 华夏 普及 全国 '
                '国内 各地 本地 当地 这里 那里 哪里 大小 几个 一个 两个 各省 各市 各县 '
                '培训中心 活动中心 老干部 研究所 疗养院 委员会 招待所 学院 大学 中学 东西'.split())
PLACE_SUF = ('省', '市', '县', '区', '镇', '乡', '村', '屯', '寨', '岛', '山', '河', '江',
             '湖', '海', '路', '街', '巷', '城', '州', '沟', '湾', '岭')
SEED_PERSONS = ['庞明', '邱玉才', '吕炳奎', '毛泽东', '江泽民']
PAT_PLACE = re.compile(u'([\u4e00-\u9fa5]{2,4}(?:省|市|县|镇|乡))')

hot = set()
for ln in HOT.read_text(encoding='utf-8').splitlines():
    w = ln.strip()
    if w:
        hot.add(w)

# ---- 载入 v4 ----
v4_events = defaultdict(list)
with open(V4, encoding='utf-8', newline='') as f:
    for row in csv.reader(f):
        if not row or len(row) < 2:
            continue
        t = row[0].strip()
        if not t or '时间' in t[:6]:
            continue
        m = YEAR_RE.search(t)
        if m:
            v4_events[int(m.group(1))].append(row[1].strip())

# ---- 采集 ----
persons = {}   # key -> dict(display, freq, files, years, chan)
places = {}    # key -> dict(display_best, display_cnt, parent, freq, files, years, chan)


def bump_p(key, disp, fname, year, chan):
    d = persons.setdefault(key, {'display': disp, 'freq': 0, 'files': set(),
                                 'years': Counter(), 'chan': set()})
    d['freq'] += 1
    d['files'].add(fname)
    if year:
        d['years'][year] += 1
    d['chan'].add(chan)


def bump_l(key, disp, parent, fname, year, chan):
    d = places.setdefault(key, {'cnt': Counter(), 'parent': parent, 'freq': 0,
                                'files': set(), 'years': Counter(), 'chan': set()})
    d['freq'] += 1
    d['cnt'][disp] += 1
    d['files'].add(fname)
    if year:
        d['years'][year] += 1
    d['chan'].add(chan)


norm_cache = {}


def norm_place(tok):
    if tok in norm_cache:
        return norm_cache[tok]
    parent, core = '', tok
    for p in PROVINCES:
        if tok.startswith(p) and len(tok) - len(p) >= 2:
            parent, core = p, tok[len(p):]
            break
    gkey = core
    for s in ('市', '县', '区', '镇', '乡'):
        if gkey.endswith(s) and len(gkey) > 2:
            gkey = gkey[:-1]
    norm_cache[tok] = (gkey, core, parent)
    return norm_cache[tok]


# ---- Pass 1: 分词缓存 + pat/ns 预扫，收集全局地名集 ----
sents = []
GLOBAL_PLACES = set()
for ln in SENT.read_text(encoding='utf-8').splitlines():
    parts = ln.split('\t')
    if len(parts) < 3:
        continue
    fname = parts[0]
    year_m = YEAR_RE.search(parts[1])
    year = int(year_m.group(1)) if year_m else None
    sent = '\t'.join(parts[2:])
    toks = [(t.strip(), p) for t, p in pseg.cut(sent) if len(t.strip()) >= 2]
    sents.append((fname, year, sent, toks))
    for mtok in PAT_PLACE.findall(sent):
        gkey, _, _ = norm_place(mtok)
        if gkey not in PLACE_BAD:
            GLOBAL_PLACES.add(gkey)
    for tok, pos in toks:
        if pos == 'ns' and tok not in PLACE_BAD and tok not in hot:
            gkey, _, _ = norm_place(tok)
            if gkey not in PLACE_BAD and len(gkey) >= 2:
                GLOBAL_PLACES.add(gkey)

# ---- Pass 2: 正式采集；nr 通道遇全局地名改道入地点表 ----
n_sent = 0
rerouted = 0
for fname, year, sent, toks in sents:
    n_sent += 1
    place_hits = set()
    for mtok in PAT_PLACE.findall(sent):
        gkey, core, parent = norm_place(mtok)
        if gkey not in PLACE_BAD:
            bump_l(gkey, core, parent, fname, year, 'pat')
            place_hits.add(gkey)

    for tok, pos in toks:
        if pos == 'nr' and tok not in P_BAD and tok not in hot:
            gkey, _, _ = norm_place(tok)
            if gkey in GLOBAL_PLACES or gkey in place_hits:
                bump_l(gkey, tok, '', fname, year, 'nr→地名改道')
                rerouted += 1
            else:
                bump_p(tok, tok, fname, year, 'nr')
        elif pos == 'ns' and tok not in PLACE_BAD and tok not in hot:
            gkey, core, parent = norm_place(tok)
            if gkey not in PLACE_BAD and len(gkey) >= 2:
                bump_l(gkey, core, parent, fname, year, 'ns')

    for seed in SEED_PERSONS:
        if seed in sent:
            bump_p(seed, seed, fname, year, 'seed')


def years_str(yc):
    ys = sorted(yc)
    return '%d-%d' % (ys[0], ys[-1]) if ys else '', ','.join(str(y) for y in ys)


# ---- 输出 1/2: 清单 ----
P_OUT = BASE / 'm0_人物清单.tsv'
L_OUT = BASE / 'm0_地点清单.tsv'
pm = [(k, v) for k, v in persons.items() if v['freq'] >= 2]
pm.sort(key=lambda kv: -kv[1]['freq'])
lm = [(k, v) for k, v in places.items() if v['freq'] >= 2]
lm.sort(key=lambda kv: -kv[1]['freq'])

with open(P_OUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['人物', '频次', '文件数', '年份跨度', '出现年份', '通道'])
    for k, v in pm:
        span, ys = years_str(v['years'])
        w.writerow([k, v['freq'], len(v['files']), span, ys, '+'.join(sorted(v['chan']))])

with open(L_OUT, 'w', encoding='utf-8', newline='') as f:
    w = csv.writer(f, delimiter='\t')
    w.writerow(['地点(归并)', '显示形', '父级', '频次', '文件数', '年份跨度', '出现年份', '通道'])
    for k, v in lm:
        span, ys = years_str(v['years'])
        w.writerow([k, v['cnt'].most_common(1)[0][0], v['parent'], v['freq'],
                    len(v['files']), span, ys, '+'.join(sorted(v['chan']))])

# ---- 输出 3/4: 实体-时间轴映射 ----
def write_map(path, entities, is_place):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f, delimiter='\t')
        head = ['地点', '年份', '当年频次', 'v4当年条数', 'v4当年样例'] if is_place \
            else ['人物', '年份', '当年频次', 'v4当年条数', 'v4当年样例']
        w.writerow(head)
        nrow = 0
        for k, v in entities:
            disp = v['cnt'].most_common(1)[0][0] if is_place else v['display']
            for y in sorted(v['years']):
                evs = v4_events.get(y, [])
                if not evs:
                    continue
                sample = '；'.join(e[:50] for e in evs[:2])
                w.writerow([disp, y, v['years'][y], len(evs), sample])
                nrow += 1
        return nrow


MAP_P = BASE / 'm0_人物-时间轴映射.tsv'
MAP_L = BASE / 'm0_地点-时间轴映射.tsv'
np1 = write_map(MAP_P, pm, False)
np2 = write_map(MAP_L, lm, True)

print('sentences=%d  persons=%d  places=%d  mapP=%d  mapL=%d' %
      (n_sent, len(pm), len(lm), np1, np2))
print('TOP10 persons:', ' | '.join('%s:%d' % (k, v['freq']) for k, v in pm[:10]))
print('TOP10 places :', ' | '.join('%s:%d' % (k, v['freq']) for k, v in lm[:10]))
