#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多元知识图谱构建：时间×地点×人物×事件 星型枢纽模型
节点: event(1429) / person(1392) / place(1621) / org(1026)
边:   event-person(参与者) / event-place(发生地) / event-org(涉及机构) / place-parent / org-alias
链接: 词典最长匹配(实体表复用, 位置贪心防重叠), 热词表互斥过滤
产出: kg_智能气功_多元图谱.json(H5消费) + kg.graphml(可视化)
"""
import csv, json, re, os
from collections import defaultdict, Counter

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

# ---------- 1. 载入 v4 时间轴 (事件节点) ----------
events = []
with open("时间轴（大事记）v4.csv", encoding="utf-8") as f:
    for i, row in enumerate(csv.reader(f)):
        if not row or not row[0].strip():
            continue
        date_raw = row[0].strip()
        text = row[1].strip() if len(row) > 1 else ""
        if not text:
            continue
        # 日期解析: 三级粒度
        m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日", date_raw)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            precision = "day"
        else:
            m = re.match(r"^(\d{4})年(\d{1,2})月", date_raw)
            if m:
                y, mo, d = int(m.group(1)), int(m.group(2)), None
                precision = "month"
            else:
                m = re.match(r"^(\d{4})", date_raw)
                if m:
                    y, mo, d = int(m.group(1)), None, None
                    precision = "year"
                else:
                    y, mo, d = None, None, None
                    precision = "other"
        events.append({"idx": len(events), "date": date_raw, "y": y, "m": mo,
                       "d": d, "precision": precision, "text": text})

# ---------- 2. 载入实体表 ----------
def read_tsv(fn):
    with open(fn, encoding="utf-8") as f:
        rd = csv.reader(f, delimiter="\t")
        header = next(rd)
        return header, [r for r in rd if r and any(c.strip() for c in r)]

ph, prows = read_tsv("m0_人物清单v2.tsv")      # 人物/频次/文件数/年份跨度/出现年份/通道
dh, drows = read_tsv("m0_地点清单v2.tsv")      # 地点(归并)/显示形/父级/频次/文件数/年份跨度/出现年份/通道
oh, orows = read_tsv("m0_机构设施清单.tsv")    # 机构/别名/类型/句频/年份/例句
hh, hrows = read_tsv("智能气功专业热词2000个.txt".replace(".txt", "") + ".txt") if False else ("", [])

# 热词互斥表 (功法/术语不是人名地名)
hot = set()
try:
    with open("智能气功专业热词2000个.txt", encoding="utf-8") as f:
        for ln in f:
            w = ln.strip().split("\t")[0].strip()
            if len(w) >= 2:
                hot.add(w)
except FileNotFoundError:
    try:
        with open("热词2000个.txt", encoding="utf-8") as f:
            for ln in f:
                w = ln.strip().split("\t")[0].strip()
                if len(w) >= 2:
                    hot.add(w)
    except Exception:
        pass

persons = {}
for r in prows:
    if len(r) >= 6:
        persons[r[0].strip()] = {"freq": int(r[1]) if r[1].isdigit() else 0,
                                 "years": r[4].strip(), "channel": r[5].strip()}
places = {}
for r in drows:
    if len(r) >= 7:
        places[r[0].strip()] = {"display": r[1].strip(), "parent": r[2].strip(),
                                "freq": int(r[3]) if r[3].isdigit() else 0, "years": r[6].strip()}
orgs = {}
for r in orows:
    if len(r) >= 5:
        aliases = [a.strip() for a in r[1].split(";") if a.strip()] if r[1].strip() else []
        orgs[r[0].strip()] = {"type": r[2].strip(), "freq": r[3].strip(),
                              "aliases": aliases, "years": r[4].strip()}

# ---------- 2.5 链接层质量闸 ----------
# PLACE_STOP: m0 NS通道误标/粘连键在词典层剔除 (动词/序数/泛指/介词粘连/日期粘连)
PLACE_STOP = set("""参加 举行 召开 召开了 举办 举办首届 首届 第二届 第三届 第四届 第五届 第六届 第七届 第八届 第九届 第十届
在 全 个 各 该 本 每 有关 全国 全省 全市 全县 全区 中国共产党 中央 国家 社会 历史 发展 建设 工作 学习 生活
东西 南北 上下 左右 大会 会议 讲座 学习班 训练班 培训班 招生 报名 入学 毕业 开学 开班 结业 表演 比赛 竞赛
对照区 发气区 大会堂 会场 课堂 课堂里 现场基地 在石家庄 到秦皇岛 至秦皇岛 在秦皇岛 了石家庄 由石家庄 从石家庄
研究会在 委员会在 受到 建立了 根据 回国 等省 等地 自治 壮族自治 社会主义 建国 创业 中考 贫困 城市 山区 庆典
华北 东北 香港回归 在全 智班 天班 鹤翔 景德 林厚 是由徐州 开了全省 召开的全省
智能气功 智能功 蒙目 个县 各县 每年 明年 去年 当年 同年 次年 这年 该年 头年 多年 五年 三年 两年 四年 一年 八年 七年 六年 九年 十年""".split())
def place_key_ok(k):
    if k in PLACE_STOP:
        return False
    if k.endswith("在"):  # 研究会在/委员会在
        return False
    if k and (k[0] in "年月日0123456789" or k.startswith(
            ("在", "到", "从", "由", "了", "至", "开", "各", "每", "该", "本", "等", "向", "往", "自", "当", "据", "随", "乘", "抵", "赴", "经", "过", "沿", "临"))):
        return False
    return True
# 地名归并: 短名规范形 (河北省->河北)
PLACE_CANON = {"河北省": "河北", "北京市": "北京", "天津市": "天津", "上海市": "上海",
               "唐山市": "唐山", "秦皇岛市": "秦皇岛", "石家庄市": "石家庄", "丰润县": "丰润",
               "山东省": "山东", "辽宁省": "辽宁", "山西省": "山西", "四川省": "四川",
               "贵州省": "贵州", "黑龙江省": "黑龙江", "台湾省": "台湾", "内蒙古": "内蒙古"}
# PERSON_STOP: nr误标的功法/时间/动词/形容词术语
PERSON_STOP = set("""子时功 活子时 抱球 站桩 开合 拉气 贯气 排气 收气 发气 组场 场性 信息物
诊断治疗 智能气功 群众性 普及性 科学性 实践性 完整性 系统性 练子 文明 迎国庆 冬令营 光荣 容纳 顾问
庆祝大会 祝贺 蓝图 迎春 春光明媚 应邀出席 应邀参加 班招生 花生米 简支梁 宝贵 边远 温泉""".split())
# ORG 污染正名特征: 含口语介宾/指人前缀的簇键不建链接条目 (链接功能由真名键承担)
def org_is_polluted(name):
    return (name.startswith(("在", "到", "去", "从", "回", "进")) or "老师" in name
            or "同志" in name or name.endswith(("上", "里", "中", "时")))
orgs = {k: v for k, v in orgs.items() if not org_is_polluted(k)}

# ---------- 3. 词典构建 (最长匹配, 长词优先) ----------
lexicon = []  # (term, kind, canon)
persons = {k: v for k, v in persons.items() if k not in PERSON_STOP}
places = {k: v for k, v in places.items() if place_key_ok(k)}
for name in persons:
    lexicon.append((name, "person", name))
for name in places:
    lexicon.append((name, "place", PLACE_CANON.get(name, name)))
for name, meta in orgs.items():
    lexicon.append((name, "org", name))
    for al in meta["aliases"]:
        if al and al != name:
            lexicon.append((al, "org", name))
# 按长度降序: 位置相同时最长胜出
lexicon.sort(key=lambda x: -len(x[0]))
LEX_RE = [(t, k, c, re.compile(re.escape(t))) for t, k, c in lexicon]

def link_entities(text):
    """位置贪心最长匹配: 收集全部候选, 按 start 排序同位取最长, 贪心选不重叠"""
    cands = []
    for term, kind, canon, rx in LEX_RE:
        for m in rx.finditer(text):
            cands.append((m.start(), m.end(), term, kind, canon))
    cands.sort(key=lambda x: (x[0], -x[1]))
    chosen, last_end = [], -1
    for s, e, term, kind, canon in cands:
        if s >= last_end:
            # 热词互斥: 功法术语不做人名地名链接 (机构名不受限)
            if kind in ("person", "place") and term in hot:
                continue
            chosen.append((s, e, term, kind, canon))
            last_end = e
    return chosen

# ---------- 4. 建图 ----------
import networkx as nx
G = nx.MultiGraph()

for ev in events:
    eid = "E%04d" % ev["idx"]
    G.add_node(eid, ntype="event", date=ev["date"], year=ev["y"] or 0,
               month=ev["m"] or 0, day=ev["d"] or 0, precision=ev["precision"], text=ev["text"])
for name, meta in persons.items():
    G.add_node("P:" + name, ntype="person", freq=meta["freq"], years=meta["years"], channel=meta["channel"])
for name, meta in places.items():
    G.add_node("D:" + name, ntype="place", display=meta["display"], freq=meta["freq"], years=meta["years"])
    if meta["parent"] and meta["parent"] in places:
        G.add_edge("D:" + name, "D:" + meta["parent"], rel="located_in")
for name, meta in orgs.items():
    G.add_node("O:" + name, ntype="org", org_type=meta["type"], freq=meta["freq"], years=meta["years"])
    for al in meta["aliases"]:
        if al and al != name:
            G.add_node("O:" + al, ntype="org", org_type="alias", freq=0, years="", alias_of=name)
            G.add_edge("O:" + name, "O:" + al, rel="alias_of")

link_stats = {"events_with_entity": 0, "ep": 0, "ed": 0, "eo": 0}
for ev in events:
    eid = "E%04d" % ev["idx"]
    seen = set()
    for s, e2, term, kind, canon in link_entities(ev["text"]):
        key = (kind, canon)
        if key in seen:
            continue
        seen.add(key)
        if kind == "person":
            G.add_edge(eid, "P:" + canon, rel="involves_person", matched=term)
            link_stats["ep"] += 1
        elif kind == "place":
            G.add_edge(eid, "D:" + canon, rel="at_place", matched=term)
            link_stats["ed"] += 1
        else:
            G.add_edge(eid, "O:" + canon, rel="involves_org", matched=term)
            link_stats["eo"] += 1
    if seen:
        link_stats["events_with_entity"] += 1

# ---------- 4.5 机构驻地边 (数据驱动: 机构×地点事件共现) ----------
# 语料常以机构名代地点("康复中心举行了..."), 驻地边打通 人物-机构-地点 跨型导航
org_place = defaultdict(Counter)
NON_GEO = {"中国", "全国", "华北", "东北", "各地", "各省"}
for ev in events:
    eid = "E%04d" % ev["idx"]
    o_l = {v for u, v, d in G.edges(eid, data=True) if d.get("rel") == "involves_org"}
    d_l = {v for u, v, d in G.edges(eid, data=True) if d.get("rel") == "at_place"
           and G.nodes[v].get("display") not in NON_GEO}
    for o in o_l:
        for pl in d_l:
            org_place[o][pl] += 1
org_hq_added = 0
for o, counter in org_place.items():
    if not counter:
        continue
    pl, cnt = counter.most_common(1)[0]
    if cnt >= 3:
        G.add_edge(o, pl, rel="located_in", evidence=cnt)
        org_hq_added += 1

# ---------- 4.6 班级层 (m0c: 康复班XX期/师资班XX级 - 口述访谈最细锚点) ----------
# 班级证据来自m1句库而非v4事件集，故班级不与事件连边; 直连机构(affiliated_to)/地点(held_at)/人物(involves_person)
import csv as _csv
class_rows = []
with open("m0_班级清单.tsv", encoding="utf-8") as f:
    rd = _csv.reader(f, delimiter="\t"); next(rd)
    class_rows = [r for r in rd if len(r) >= 10]
def _freq_of(n):
    f = G.nodes[n].get("freq", 0)
    try: return int(f)
    except (TypeError, ValueError): return 0
_O_NAMES = {n[2:] for n in G.nodes if n.startswith("O:") and _freq_of(n) >= 2 and not G.nodes[n].get("alias_of")}
_D_NAMES = {n[2:] for n in G.nodes if n.startswith("D:")}
_P_NAMES = {n[2:] for n in G.nodes if n.startswith("P:") and _freq_of(n) >= 3}
def _match_nodes(raw, node_names):
    """证据字符串→图中已有干净节点: 精确命中或包含式(长名含短正名), 匹配不上即弃(防污染)"""
    BAD_FRAG = re.compile(r'^(作为|仅|入|个|在|到|去|从|由|了|把|被|将|又)')
    CLASS_P_BAD = {'班学功','孙女','秀英','段时间','小学生','大儿子','小儿子','老师','同学'}  # 亲属称谓/时间词/泛称的nr误标
    out = set()
    for nm in [x.strip() for x in raw.split("|") if x.strip()]:
        if nm in CLASS_P_BAD:  # 班级层人物证据的称谓/泛称误标
            continue
        if BAD_FRAG.match(nm):  # 动词/介词/副词开头的机构键必为粘连污染
            clean = re.sub(r'^(作为|仅|入)', '', nm)
            if clean in node_names:
                out.add(clean)
            continue
        if nm in node_names:
            out.add(nm); continue
        cands = [n for n in node_names if len(n) >= 4 and n in nm]
        if cands: out.add(max(cands, key=len))
    return out
class_edges = 0
for r in class_rows:
    key, ctype, qi, lvl, cnt, years, org_main, orgs_raw, places_raw, persons_raw, sample = r[:11]
    cid = "C:" + key
    G.add_node(cid, ntype="class", cls_type=ctype, qi=qi or "", lvl=lvl or "",
               freq=int(cnt) if cnt.isdigit() else 0, years=years, org=org_main, sample=sample)
    for o in _match_nodes(orgs_raw, _O_NAMES):
        G.add_edge(cid, "O:" + o, rel="affiliated_to"); class_edges += 1
    for p_ in _match_nodes(places_raw, _D_NAMES):
        G.add_edge(cid, "D:" + p_, rel="held_at"); class_edges += 1
    for p in _match_nodes(persons_raw, _P_NAMES):
        G.add_edge(cid, "P:" + p, rel="involves_person"); class_edges += 1

# ---------- 5. 统计 ----------
ntypes = defaultdict(int)
for n, d in G.nodes(data=True):
    ntypes[d.get("ntype", "?")] += 1
etypes = defaultdict(int)
for u, v, d in G.edges(data=True):
    etypes[d.get("rel", "?")] += 1
deg = sorted(G.degree, key=lambda x: -x[1])
top_hub_events = [(n, v, G.nodes[n].get("date", ""), G.nodes[n].get("text", "")[:40])
                  for n, v in deg if G.nodes[n].get("ntype") == "event"][:8]
top_entities = [(G.nodes[n].get("ntype", "?"), n, v) for n, v in deg if G.nodes[n].get("ntype") != "event"][:15]
comp = nx.number_connected_components(G)

print("节点: %d  %s" % (G.number_of_nodes(), dict(ntypes)))
print("边: %d  %s" % (G.number_of_edges(), dict(etypes)))
print("事件实体链接率: %d/%d (%.0f%%)" % (link_stats["events_with_entity"], len(events),
      100.0 * link_stats["events_with_entity"] / max(len(events), 1)))
print("连通分量: %d" % comp)
print("TOP枢纽事件(度):")
for n, v, dt, tx in top_hub_events:
    print("  %s 度%d %s %s" % (n, v, dt, tx))
print("TOP实体(度):")
for t, n, v in top_entities:
    print("  [%s] %s 度%d" % (t, n, v))

# ---------- 6. 产出 JSON (H5消费: 节点+边+四维索引) ----------
adj_p, adj_d, adj_o = defaultdict(list), defaultdict(list), defaultdict(list)
year_index = defaultdict(list)
for u, v, d in G.edges(data=True):
    if d.get("rel") == "involves_person":
        adj_p[v].append(u)
    elif d.get("rel") == "at_place":
        adj_d[v].append(u)
    elif d.get("rel") == "involves_org":
        adj_o[v].append(u)
for ev in events:
    if ev["y"] is not None:
        year_index[str(ev["y"])].append("E%04d" % ev["idx"])

kg = {
    "meta": {"name": "智能气功多元知识图谱", "built": "2026-10-09",
             "nodes": G.number_of_nodes(), "edges": G.number_of_edges(),
             "link_rate": "%.1f%%" % (100.0 * link_stats["events_with_entity"] / max(len(events), 1))},
    "nodes": [],
    "edges": [{"s": u, "t": v, "rel": d.get("rel", ""), "m": d.get("matched", ""),
               "ev": d.get("evidence", "")}
              for u, v, d in G.edges(data=True)],
    "index": {"by_year": dict(year_index), "by_person": dict(adj_p),
              "by_place": dict(adj_d), "by_org": dict(adj_o)},
    "timeline": ["E%04d" % ev["idx"] for ev in sorted(events, key=lambda x: (x["y"] or 9999, x["m"] or 0, x["d"] or 0))],
}
for n, d in G.nodes(data=True):
    kg["nodes"].append(dict(id=n, **d))
with open("kg_智能气功_多元图谱.json", "w", encoding="utf-8") as f:
    json.dump(kg, f, ensure_ascii=False, separators=(",", ":"))
nx.write_graphml(G, "kg.graphml")
print("JSON+GraphML 落盘完成")
