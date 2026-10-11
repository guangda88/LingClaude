#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kg v3: 生平年表(庞明个人线) + 著作60种 挂图
输入: kg_智能气功_多元图谱.json (v2, 4512节点/1625边)
     m0d_生平年表.tsv (year,date_raw,叙述 209条)
     m0d_著作年表.tsv (9列, 含6处未引号多行字段→确定性接行修复)
产出: kg_智能气功_多元图谱.json 升级为 v3 (bio事件B%04d + 著作W%03d + 四笔名节点)
     kg.graphml 同步 | kg_v3_并入报告.md
原则: 冲突不静默改; 生平=个人经历事件, 与大事记体(v4)分型不混表(ntype=event + src=bio)
"""
import csv, json, re, os
from collections import defaultdict, Counter

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

# ---------- 0. 载入 v2 图 ----------
with open("kg_智能气功_多元图谱.json", encoding="utf-8") as f:
    kg = json.load(f)
nodes = {n["id"]: n for n in kg["nodes"]}
edges = kg["edges"]
idx = kg["index"]
base_nodes, base_edges = len(nodes), len(edges)
assert base_nodes == 4512 and base_edges == 1625, f"基线不符: {base_nodes}/{base_edges}"

# ---------- 1. 著作表解析(含裸换行修复) ----------
def load_works():
    raw = list(csv.reader(open("m0d_著作年表.tsv", encoding="utf-8"), delimiter="\t"))
    rows, buf = [], None
    for r in raw[1:]:
        if r and re.fullmatch(r"\d+", (r[0] or "").strip()):
            if buf: rows.append(buf)
            buf = r
        elif buf is not None:
            # 接行: 续行首字段拼入上一记录末字段(还原被裸换行截断的多行字段)
            buf[-1] = buf[-1] + "\n" + r[0]
            buf.extend(r[1:])
        # 忽略表头/空行
    if buf: rows.append(buf)
    bad = [r for r in rows if len(r) != 9]
    assert not bad, f"修复后仍有异常行: {len(bad)}"
    return rows

def year_of(s):
    m = re.search(r"(19|20)\d{2}", s or "")
    return int(m.group()) if m else None

works = []
for r in load_works():
    no, title, pen, pub, isbn, ptime, chars, pages, note = [x.strip() for x in r]
    works.append({"no": int(no), "title": title, "pen": pen,
                  "pubs": [p.strip() for p in re.split(r"[\n;；]", pub) if p.strip() and p != "——"],
                  "isbn": isbn, "year": year_of(ptime), "ptime": ptime,
                  "chars_k": chars, "pages": pages, "note": note})
print(f"著作: {len(works)} 种, 年份解析 {sum(1 for w in works if w['year'])}, 跨度 "
      f"{min(w['year'] for w in works if w['year'])}-{max(w['year'] for w in works if w['year'])}")

# ---------- 2. 著作节点 + 四笔名 ----------
PENS = ["贺明", "甄隐", "尹真", "庞鹤鸣"]
pen_works = Counter()
for w in works:
    for p in PENS:
        if p in w["pen"]:
            pen_works[p] += 1
for p, cnt in pen_works.items():
    nid = "P:" + p
    if nid not in nodes:
        nodes[nid] = {"id": nid, "ntype": "person", "freq": cnt, "pen_name": True}
        edges.append({"s": nid, "t": "P:庞明", "rel": "pen_name_of", "m": p, "ev": "著作年表署名"})
    else:
        nodes[nid]["pen_name"] = True

work_ids = []
for w in works:
    wid = "W%03d" % w["no"]
    work_ids.append(wid)
    nodes[wid] = {"id": wid, "ntype": "work", "year": w["year"], "title": w["title"],
                  "pen": w["pen"], "pub": "；".join(w["pubs"]), "isbn": w["isbn"],
                  "chars_k": w["chars_k"], "pages": w["pages"], "note": w["note"]}
    edges.append({"s": wid, "t": "P:庞明", "rel": "authored_by", "m": "庞明", "ev": f"署名{w['pen']}"})
    for p in PENS:
        if p in w["pen"] and "P:" + p in nodes:
            edges.append({"s": wid, "t": "P:" + p, "rel": "authored_by", "m": p, "ev": "署名"})

# 著作×出版单位 挂图 v3 最终规则: 出版方字符串为权威, 词典名⊆出版方 才挂
# 教训链: 双向包含×图节点名=689假边 → 收紧仍616(粘连"正名") → 反转方向根治
# 词典本身也含粘连键, 故: (a)BAD_FRAG动词前缀剔除 (b)仅 词典名⊆pub (c)目标节点须在图
BAD_FRAG = re.compile(r'^(作为|仅|入|在|到|去|从|由|了|把|被|将|又|至|向|回|抵|于)')
org_canon = set()
with open("m0_机构设施清单.tsv", encoding="utf-8") as f:
    rd = csv.reader(f, delimiter="\t")
    next(rd)
    for r in rd:
        if r and r[0].strip() and not BAD_FRAG.match(r[0].strip()):
            org_canon.add(r[0].strip())
graph_o = {n for n in nodes if n.startswith("O:")}
pub_hit = 0
for w in works:
    wid = "W%03d" % w["no"]
    for pub in w["pubs"]:
        best = None
        for name in org_canon:
            if len(name) >= 5 and name in pub and ("O:" + name) in graph_o:
                if best is None or len(name) > len(best):
                    best = name
        if best:
            edges.append({"s": wid, "t": "O:" + best, "rel": "published_by", "m": best, "ev": pub})
            pub_hit += 1

# ---------- 3. 生平事件挂图 ----------
bio_ids = []
bio_entity_edges = 0
# 候选实体: 图内既有干净节点(P freq≥2 / O / D), 最长优先, 位置贪心防重叠
cand = []
for nid, nd in nodes.items():
    if nid.startswith("P:") and nd.get("freq", 0) >= 2:
        cand.append((nid, len(nid) - 2))
    elif nid.startswith("O:"):
        cand.append((nid, len(nid) - 2))
    elif nid.startswith("D:"):
        cand.append((nid, len(nid) - 2))
cand.sort(key=lambda x: -x[1])
with open("m0d_生平年表.tsv", encoding="utf-8") as f:
    rd = csv.DictReader(f, delimiter="\t")
    bio_rows = [r for r in rd if r.get("叙述")]
print(f"生平事件: {len(bio_rows)} 条")
for i, r in enumerate(bio_rows):
    bid = "B%04d" % i
    bio_ids.append(bid)
    y = int(r["year"]) if str(r["year"]).isdigit() else None
    text = r["叙述"]
    nodes[bid] = {"id": bid, "ntype": "event", "src": "bio", "date": r["date_raw"],
                  "year": y, "month": 0, "day": 0, "precision": "year", "text": text}
    edges.append({"s": bid, "t": "P:庞明", "rel": "involves_person", "m": "庞明", "ev": "生平年表"})
    # 词典匹配(最长优先, 贪心防重叠: 已占用片段才跳过)
    taken = []
    for nid, L in cand:
        name = nid[2:]
        p = text.find(name)
        if p < 0:
            continue
        if any(not (p + L <= a or p >= b) for a, b in taken):
            continue
        rel = "involves_person" if nid.startswith("P:") else \
              ("involves_org" if nid.startswith("O:") else "at_place")
        edges.append({"s": bid, "t": nid, "rel": rel, "m": name, "ev": "生平匹配"})
        bio_entity_edges += 1
        taken.append((p, p + L))

# ---------- 4. 索引重建 ----------
by_year, by_person, by_place, by_org = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
by_work = defaultdict(list)
for e in edges:
    s, t, rel = e["s"], e["t"], e["rel"]
    if rel == "involves_person": by_person[t].append(s)
    elif rel == "at_place": by_place[t].append(s)
    elif rel == "involves_org": by_org[t].append(s)
    elif rel == "authored_by": by_work[t].append(s)
    # year 索引: 事件/著作按年份
for nid in list(by_year):
    pass
year_index = defaultdict(list)
for nid, nd in nodes.items():
    if nd.get("ntype") in ("event", "work") and nd.get("year"):
        year_index[str(nd["year"])].append(nid)
for k in year_index: year_index[k] = sorted(set(year_index[k]))

# ---------- 5. timeline 重排 (事件+著作按 年/月/日) ----------
def sortkey(nid):
    nd = nodes[nid]
    return (nd.get("year") or 9999, nd.get("month") or 0, nd.get("day") or 0,
            0 if nd["ntype"] == "event" else 1, nid)
tl = [nid for nid, nd in nodes.items()
      if nd.get("ntype") in ("event", "work") and not nid.startswith("B9")]
kg["timeline"] = sorted(tl, key=sortkey)

# ---------- 6. 元数据 & 落盘 ----------
new_edges = len(edges) - base_edges
etypes = Counter(e["rel"] for e in edges)
kg["meta"] = {"name": "智能气功多元知识图谱", "built": "2026-10-09", "version": "v3",
              "nodes": len(nodes), "edges": len(edges),
              "v3_add": {"bio_events": len(bio_ids), "works": len(work_ids),
                         "pen_name_nodes": len(pen_works), "new_edges": new_edges,
                         "bio_entity_edges": bio_entity_edges, "pub_edges": pub_hit},
              "link_rate": kg["meta"].get("link_rate", "")}
kg["nodes"] = list(nodes.values())
kg["edges"] = edges
kg["index"] = {"by_year": dict(year_index), "by_person": dict(by_person),
               "by_place": dict(by_place), "by_org": dict(by_org), "by_work": dict(by_work)}
with open("kg_智能气功_多元图谱.json", "w", encoding="utf-8") as f:
    json.dump(kg, f, ensure_ascii=False, separators=(",", ":"))

# GraphML (属性全字符串化)
import networkx as nx
G = nx.DiGraph()
for nid, nd in nodes.items():
    G.add_node(nid, **{k: str(v) for k, v in nd.items() if k != "id"})
for e in edges:
    G.add_edge(e["s"], e["t"], rel=e["rel"], m=e.get("m", ""), ev=e.get("ev", ""))
nx.write_graphml(G, "kg.graphml")

print(f"\nv3 节点: {base_nodes} → {len(nodes)} (+{len(nodes)-base_nodes})")
print(f"v3 边:   {base_edges} → {len(edges)} (+{new_edges})")
print("边型分布:", dict(etypes.most_common(12)))
print(f"四笔名挂图: {dict(pen_works)} (贺明/甄隐/尹真新建节点, 均 pen_name_of→P:庞明)")
comp = nx.number_connected_components(G.to_undirected())
print(f"连通分量: {comp}")
print("JSON+GraphML 落盘完成")
