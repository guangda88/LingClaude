#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""m1 B级571条复审管线: 拒/分型/去重/升格 → 时间轴v5
输入: m1_B级待审.tsv(571) + 时间轴（大事记）v4.csv(1432)
输出: 时间轴（大事记）v5.csv (v4全量+升格新事件〔源:复审升格〕)
     m1_B级复审决策.tsv (571行全决策留痕) + m1_B级回忆体候选.tsv + m1_B级人工区.tsv + m1_B级复审报告.md
规则序: R1无日期拒 → R2过短拒 → R3参考文献拒 → R4回忆体分桶(不混表) → R5v4去重 → R6机构/事件动词升格 → R7留人工区
"""
import csv, re, os
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

# ---------- 载入 ----------
b_rows = list(csv.DictReader(open("m1_B级待审.tsv", encoding="utf-8"), delimiter="\t"))
assert len(b_rows) == 571, f"B级数变: {len(b_rows)}"
v4 = []
with open("时间轴（大事记）v4.csv", encoding="utf-8") as f:
    for row in csv.reader(f):
        if row and row[0].strip():
            v4.append((row[0].strip(), row[1].strip() if len(row) > 1 else ""))
print(f"B级: {len(b_rows)} | v4: {len(v4)}")

def norm(s):
    return re.sub(r'''[\s，。；：、！？（）()〔〕\[\]“”"'‘’·—\-～~]''', "", s)

def core(s, date_raw):
    """去日期头部, 取叙述主体(归一化后)"""
    d = norm(date_raw or "")
    ns = norm(s)
    if d and ns.startswith(d):
        return ns[len(d):]
    return ns

v4_cores = {core(t, d): (d, t) for d, t in v4 if core(t, d)}

REF_PAT = re.compile(r"出版社|年版|第一版|第二版|第\s*\d+\s*页|\[?\d+\]\s*《|著\s*《|译\s*《|□\s*$|ISBN")
CITE_PAT = re.compile(r"摘自|选自|引自|《人民日报》|政府工作报告|远景目标纲要")
MEMOIR_PAT = re.compile(r"我们(?!的)|咱们|我本人|我自己|亲历|笔者|(?<!其)[我他她你](?!们)")
ORG_PAT = re.compile(r"培训班|学习班|进修|康复班|研究会|协会|培训中心|辅导站|总站|杂志社|出版社|学院|大学")
EVENT_PAT = re.compile(r"举办|召开|成立|创编|命名|推广|出版|创刊|停刊|发表|开设|开办|组建|印发|试行|恢复|通过|决定|正式|讲学|讲课|报告")

def ymd(r):
    y, m, d = r["年"].strip(), r["月"].strip(), r["日"].strip()
    if not y:
        return None
    return y + "年" + (m + "月" if m else "") + (d + "日" if d else "")

# ---------- 决策 ----------
decisions, promote, memoir_bucket = [], [], []
reasons = Counter()
for i, r in enumerate(b_rows):
    s = r["句子"].strip()
    date = ymd(r)
    dec = {"idx": i + 1, "年": r["年"], "月": r["月"], "日": r["日"],
           "来源": r["来源文件"], "句子": s, "决策": "", "理由": "", "去向": ""}
    if not date:
        dec.update(决策="拒", 理由="无日期", 去向="reject"); reasons["无日期拒"] += 1
    elif len(norm(s)) < 12:
        dec.update(决策="拒", 理由="叙述过短(<12字)", 去向="reject"); reasons["过短拒"] += 1
    elif REF_PAT.search(s):
        dec.update(决策="拒", 理由="参考文献/书目条目", 去向="reject"); reasons["参考文献拒"] += 1
    elif MEMOIR_PAT.search(s):
        memoir_bucket.append(dec)
        dec.update(决策="分型", 理由="第一人称回忆体(与大事记体不混表)", 去向="memoir"); reasons["回忆体分桶"] += 1
    else:
        # R5a 引文摘录(优先判定, 避免去重键含引文正文)
        if CITE_PAT.search(s):
            dec.update(决策="拒", 理由="引文摘录(非本编事件)", 去向="reject"); reasons["引文摘录拒"] += 1
        else:
            c = core(s, date)
            hit = None
            if c:
                for vc, (vd, vt) in v4_cores.items():
                    if c in vc or vc in c:
                        hit = (vc, vd, vt); break
            # R5b v4去重 / R6 升格(机构∧事件动词) / R7 人工区
            if hit:
                dec.update(决策="拒", 理由=f"v4已有同事件({hit[1]})", 去向="dup_v4"); reasons["v4去重拒"] += 1
            elif ORG_PAT.search(s) and EVENT_PAT.search(s):
                promote.append(dec)
                dec.update(决策="升", 理由="机构∧事件动词, v4无同事件", 去向="v5"); reasons["升格v5"] += 1
            else:
                dec.update(决策="留", 理由="无法自动定性, 留人工区", 去向="manual"); reasons["留人工区"] += 1
    decisions.append(dec)

# ---------- v5 落盘 (升格事件按时间插入, 尾标可回退) ----------
def datekey(date):
    # search 式松解析: "1975年春"/"1999年1月19日～21日" 等松散日期也能定位年代
    m = re.search(r"(\d{4})年(?:(\d{1,2})月)?(?:(\d{1,2})日)?", date or "")
    return (int(m.group(1)) if m else 9999,
            int(m.group(2)) if m and m.group(2) else 0,
            int(m.group(3)) if m and m.group(3) else 0)

new_events = []
for dec in promote:
    date = ymd(dec)
    text = dec["句子"]
    if not text.startswith(date):
        text = date + "，" + text
    new_events.append((datekey(date), date, text + "〔源:复审升格·%s〕" % dec["来源"].split("/")[0]))
# 升格内部去重: 与先前升格句共享≥12字连续片段者视为同事件转述
def lcs_len(a, b):
    best = 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            best = max(best, k)
    return best

kept, dropped = [], 0
for dec in promote:
    s_norm = norm(dec["句子"])
    if any(lcs_len(s_norm, norm(k["句子"])) >= 12 for k in kept):
        dec.update(决策="拒", 理由="升格内部同事件转述", 去向="dup_internal"); dropped += 1
    else:
        kept.append(dec)
promote = kept
reasons["升格内部去重拒"] = dropped

new_events = []
for dec in promote:
    date = ymd(dec)
    text = dec["句子"]
    if not text.startswith(date):
        text = date + "，" + text
    new_events.append((datekey(date), date, text + "〔源:复审升格·%s〕" % dec["来源"].split("/")[0]))
v5 = [(datekey(d), d, t) for d, t in v4] + new_events
v5.sort(key=lambda x: x[0])
with open("时间轴（大事记）v5.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    for _, d, t in v5:
        w.writerow([d, t])

# ---------- 决策留痕 + 分桶 ----------
with open("m1_B级复审决策.tsv", "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["idx", "年", "月", "日", "来源", "句子", "决策", "理由", "去向"], delimiter="\t")
    w.writeheader()
    for dec in decisions:
        w.writerow(dec)
with open("m1_B级回忆体候选.tsv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f, delimiter="\t")
    w.writerow(["年", "月", "日", "来源", "句子"])
    for dec in memoir_bucket:
        w.writerow([dec["年"], dec["月"], dec["日"], dec["来源"], dec["句子"]])
with open("m1_B级人工区.tsv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f, delimiter="\t")
    w.writerow(["年", "月", "日", "来源", "句子"])
    for dec in decisions:
        if dec["去向"] == "manual":
            w.writerow([dec["年"], dec["月"], dec["日"], dec["来源"], dec["句子"]])

# ---------- 报告 ----------
lines = ["# m1 B级571条复审报告", "日期: 2026-10-09 | 管线: m1_b_review_pipeline.py（全可复跑）", "",
         "## 决策分布", ""]
for k, n in reasons.most_common():
    lines.append(f"- {k}: {n}")
lines += ["", f"## 升格并入 v5: {len(promote)} 条",
          f"- 时间轴（大事记）v4: {len(v4)} → v5: {len(v5)} (+{len(promote)})",
          "- 升格条款尾标〔源:复审升格·通道〕可与 v4 原生条目区分, 可一键回退", "",
          f"## 回忆体分桶: {len(memoir_bucket)} 条 → m1_B级回忆体候选.tsv（口述素材线, 不混大事记表）", "",
          f"## 留人工区: {reasons['留人工区']} 条 → m1_B级人工区.tsv", "", "## 升格样例"]
for d in promote[:6]:
    lines.append(f"- [{ymd(d)}] {d['句子'][:56]}")
open("m1_B级复审报告.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")

print("决策分布:", dict(reasons))
print(f"v5: {len(v4)} → {len(v5)} 升格{len(promote)} | 回忆体{len(memoir_bucket)} | 人工区{reasons['留人工区']}")
print("落盘: v5.csv / 复审决策.tsv / 回忆体候选.tsv / 人工区.tsv / 复审报告.md")
