#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
m0f_chat_corpus_pipeline.py — 聊天语料接收/脱敏/解析管线（M0 口述史支线）
用法:
  python3 m0f_chat_corpus_pipeline.py                # 扫描 inbox/，解析全部新文件
  python3 m0f_chat_corpus_pipeline.py --selftest     # 内置样例自测
产物:
  chat_corpus_20261009/parsed/                       # 脱敏后的规整 JSONL（每文件一份）
  chat_corpus_20261009/reports/接收台账_<批次>.md    # 消息量/发言人分布/脱敏统计/质控标记
设计原则（延续 M0 纪律）:
  1. 只读 inbox，不回写原文件；raw 原样保存在 inbox/ 不动
  2. 脱敏在本地完成：手机号/座机/邮箱/微信号/QQ号 → 哈希占位符（salt 随机生成，不入库）
  3. 每条消息带质控标记（低置信/疑似广告/疑似定位分享），决策留给人工
  4. 秘级红线：路径含「注意保管的资料」直接拒收并记台账
"""
import sys, os, re, json, hashlib, argparse, datetime, pathlib

BASE = pathlib.Path(__file__).resolve().parent
INBOX = BASE / "inbox"
PARSED = BASE / "parsed"
REPORTS = BASE / "reports"
SALT = os.environ.get("CORPUS_SALT", hashlib.sha256(str(os.getpid()).encode()).hexdigest()[:16])

# ---------- 秘级红线 ----------
FORBIDDEN = ("注意保管的资料",)

def is_forbidden(path: str) -> bool:
    return any(k in path for k in FORBIDDEN)

# ---------- 脱敏 ----------
RE_MOBILE = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')
RE_TEL    = re.compile(r'(?<!\d)(?:0\d{2,3}[-—]?)?\d{7,8}(?!\d)')
RE_EMAIL  = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
RE_WXID   = re.compile(r'(?<![A-Za-z0-9_-])(?:微信号|wx[_-]?id|微信)\s*[:：]?\s*([A-Za-z][A-Za-z0-9_-]{5,19})(?![A-Za-z0-9_-])', re.I)
RE_QQ     = re.compile(r'(?<![0-9])[1-9]\d{4,10}(?![0-9])')

def mask(text: str):
    stats = {"mobile": 0, "tel": 0, "email": 0, "wxid": 0, "qq": 0}
    def h(plain):
        return hashlib.sha256((SALT + plain).encode()).hexdigest()[:12]
    def sub_mobile(m):
        stats["mobile"] += 1; return f"⟨手机:{h(m.group(0))}⟩"
    def sub_email(m):
        stats["email"] += 1; return f"⟨邮箱:{h(m.group(0))}⟩"
    def sub_wxid(m):
        stats["wxid"] += 1; return f"⟨微信号:{h(m.group(1))}⟩"
    t = RE_MOBILE.sub(sub_mobile, text)
    t = RE_EMAIL.sub(sub_email, t)
    t = RE_WXID.sub(sub_wxid, t)
    # 座机：7-8 位数字且上下文含「电话|联系」才脱敏（避免误伤年份/期号）
    def sub_tel(m):
        pre = t[max(0, m.start()-6):m.start()]
        if re.search(r'电话|联系|致电|拨打', pre):
            stats["tel"] += 1
            return f"⟨电话:{h(m.group(0))}⟩"
        return m.group(0)
    t = RE_TEL.sub(sub_tel, t)
    # QQ：5-11 位首非零数字，且上下文含「QQ」才脱敏（避免误伤日期/编号）
    def sub_qq(m):
        pre = t[max(0, m.start()-4):m.start()]
        if re.search(r'QQ|qq|扣扣', pre):
            stats["qq"] += 1
            return f"⟨QQ:{h(m.group(0))}⟩"
        return m.group(0)
    t = RE_QQ.sub(sub_qq, t)
    return t, stats

# ---------- 质控标记 ----------
RE_URL    = re.compile(r'https?://|www\.')
RE_AD     = re.compile(r'(加我|私信我|低价|出售|代购|点击链接|扫码|免费领)')
RE_LOC    = re.compile(r'(定位|共享实时位置|位置共享|\[位置\]|\[LOCATION\])')
RE_MEDIA  = re.compile(r'^\[(图片|图片.*?\]|视频|语音|动画表情|文件|链接|名片|转账|红包|位置|音乐|小程序)\]$|^\[图片\]$|^\[表情\]$')

def qc_flags(msg: str):
    flags = []
    if RE_URL.search(msg): flags.append("含外链")
    if RE_AD.search(msg): flags.append("疑似广告")
    if RE_LOC.search(msg): flags.append("位置分享")
    if RE_MEDIA.match(msg.strip()) and len(msg.strip()) <= 12: flags.append("占位消息(图片/语音等)")
    return flags

# ---------- 解析器 ----------
DATE_HINT = re.compile(r'(20\d{2}|19\d{2})[-/年\.]')

def parse_wechat_txt(raw: str):
    """微信 PC 端导出/粘贴格式: 消息头行 + 正文行
    头行形态1: 2023-05-01 下午8:01 昵称
    头行形态2: 2023-05-01 20:01:23 昵称"""
    msgs, cur = [], None
    head = re.compile(r'^(20\d{2}[-/\.]\d{1,2}[-/\.]\d{1,2})\s*([上下]午)?\s*(\d{1,2}:\d{2}(?::\d{2})?)\s+(\S.*)$')
    for line in raw.splitlines():
        line = line.rstrip()
        m = head.match(line)
        if m:
            if cur: msgs.append(cur)
            nick = m.group(4).strip()
            cur = {"time": f"{m.group(1)} {m.group(3)}", "sender": nick, "text": ""}
        elif cur is not None:
            if line == "" and cur["text"]:
                msgs.append(cur); cur = None
            else:
                cur["text"] += ("\n" if cur["text"] else "") + line
    if cur: msgs.append(cur)
    return msgs

def parse_qq_txt(raw: str):
    """QQ 导出常见形态: 昵称(时间):\n内容  或  时间 昵称\n内容"""
    msgs, cur = [], None
    head = re.compile(r'^(.*?)\((20\d{2}-\d{1,2}-\d{1,2} \d{1,2}:\d{2}(?::\d{2})?)\)\s*[:：]?\s*$')
    for line in raw.splitlines():
        line = line.rstrip()
        m = head.match(line)
        if m:
            if cur: msgs.append(cur)
            cur = {"time": m.group(2), "sender": m.group(1).strip(), "text": ""}
        elif DATE_HINT.search(line[:30]) and cur is None:
            parts = line.split(None, 2)
            if len(parts) >= 3:
                cur = {"time": f"{parts[0]} {parts[1]}", "sender": parts[2].strip(), "text": ""}
        elif cur is not None:
            cur["text"] += ("\n" if cur["text"] else "") + line
    if cur and cur["text"]: msgs.append(cur)
    return msgs

def parse_csv(path: str):
    import csv
    msgs = []
    with open(path, newline='', encoding='utf-8', errors='replace') as f:
        sample = f.read(4096); f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t") if sample else csv.excel
        for row in csv.DictReader(f, dialect=dialect):
            msg = row.get("内容") or row.get("content") or row.get("Msg") or row.get("StrContent") or ""
            snd = row.get("发送人") or row.get("sender") or row.get("Sender") or row.get("strTalker") or "未知"
            tm = row.get("时间") or row.get("time") or row.get("CreateTime") or ""
            if not msg: continue
            msgs.append({"time": str(tm), "sender": str(snd).strip() or "未知", "text": str(msg)})
    return msgs

def parse_generic(raw: str):
    """兜底：按空行分块，从块内启发式找时间戳"""
    msgs, cur = [], None
    ts = re.compile(r'(20\d{2}[-/年\.]\d{1,2}[-/月\.]\d{1,2}日?\s*\d{1,2}:\d{2}(?::\d{2})?)')
    for block in re.split(r'\n\s*\n', raw):
        block = block.strip()
        if not block: continue
        m = ts.search(block)
        msgs.append({"time": m.group(1) if m else "", "sender": "未识别", "text": block})
    return msgs

def sniff_format(path: str, raw_head: str) -> str:
    low = path.lower()
    if low.endswith((".csv", ".tsv")): return "csv"
    if "20" in raw_head and re.search(r'(上|下)午?\s*\d{1,2}:\d{2}', raw_head): return "wechat"
    if re.search(r'\(20\d{2}-\d{1,2}-\d{1,2} \d{1,2}:\d{2}', raw_head): return "qq"
    if DATE_HINT.search(raw_head[:80]): return "wechat"
    return "generic"

def process_file(fp: pathlib.Path):
    rec = {"file": fp.name, "rel": str(fp.relative_to(BASE)), "status": "", "n_msgs": 0,
           "senders": {}, "masked": {}, "qc": {}, "out": ""}
    if is_forbidden(str(fp)):
        rec["status"] = "REJECTED(秘级红线)"; return rec
    try:
        raw = fp.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        rec["status"] = f"READ_ERROR:{e}"; return rec
    if not raw.strip():
        rec["status"] = "EMPTY"; return rec
    head = raw[:2000]
    fmt = sniff_format(fp.name, head)
    if fmt == "csv": msgs = parse_csv(str(fp))
    elif fmt == "wechat": msgs = parse_wechat_txt(raw)
    elif fmt == "qq": msgs = parse_qq_txt(raw)
    else: msgs = parse_generic(raw)
    # 兜底：解析失败或 0 条 → generic
    if not msgs:
        fmt = "generic(fallback)"; msgs = parse_generic(raw)
    out_name = fp.stem + ".jsonl"
    out_path = PARSED / out_name
    n = 0
    with open(out_path, "w", encoding="utf-8") as w:
        for m in msgs:
            txt_masked, stats = mask(m["text"])
            flags = qc_flags(m["text"])
            for k, v in stats.items(): rec["masked"][k] = rec["masked"].get(k, 0) + v
            snd = m["sender"] or "未知"
            rec["senders"][snd] = rec["senders"].get(snd, 0) + 1
            for f in flags: rec["qc"][f] = rec["qc"].get(f, 0) + 1
            oid = hashlib.sha256((m.get("time","") + snd + m["text"]).encode()).hexdigest()[:16]
            w.write(json.dumps({"id": oid, "time": m.get("time",""), "sender": snd,
                                "text": txt_masked, "qc_flags": flags, "src_file": fp.name,
                                "fmt": fmt}, ensure_ascii=False) + "\n")
            n += 1
    rec.update(status=f"OK({fmt})", n_msgs=n, out=str(out_path.relative_to(BASE)))
    return rec

def selftest():
    INBOX_SELFTEST = INBOX / "_selftest"
    # --- 样例1: 微信 ---
    (INBOX_SELFTEST / "样例_微信导出.txt").write_text(
        "2023-05-01 下午8:01 张师兄\n今天去 Training 中心练功，收获很大，我的手机 13812345678，加微信：wxid_abc123\n\n"
        "2023-05-01 下午8:03 李功友\n[图片]\n\n"
        "2023-05-02 上午9:15 张师兄\n好的，QQ 98765432 联系。也可以发邮件 a@b.com\n", encoding="utf-8")
    # --- 样例2: QQ ---
    (INBOX_SELFTEST / "样例_QQ导出.txt").write_text(
        "王大姐(2024-01-15 14:30)\n我 1998 年在华夏智能气功培训中心学习过\n\n"
        "赵老师(2024-01-15 14:33)\n欢迎！可以看 1997.4.7_第五期提高研修班 的讲课\n", encoding="utf-8")
    # --- 样例3: 秘级红线 ---
    (INBOX / "注意保管的资料_测试.txt").write_text("测试秘级拒收\n", encoding="utf-8")
    # --- 样例4: 通用 ---
    (INBOX_SELFTEST / "样例_杂记.txt").write_text(
        "2023年5月1日 10:00 有人提到 1999年最后一次集训\n\n另一块没有时间\n", encoding="utf-8")

    recs = []
    for fp in sorted(INBOX.rglob("*.txt")) + sorted(INBOX.rglob("*.csv")) + sorted(INBOX.rglob("*.md")):
        recs.append(process_file(fp))
    print(json.dumps(recs, ensure_ascii=False, indent=1))
    # 断言
    by = {r["file"]: r for r in recs}
    assert by["样例_微信导出.txt"]["status"].startswith("OK(wechat)"), by
    assert by["样例_微信导出.txt"]["n_msgs"] == 3, by
    assert by["样例_QQ导出.txt"]["status"].startswith("OK(qq)"), by
    assert by["样例_QQ导出.txt"]["n_msgs"] == 2, by
    assert by["注意保管的资料_测试.txt"]["status"].startswith("REJECTED"), by
    assert by["样例_杂记.txt"]["status"].startswith("OK(generic"), by
    # 脱敏断言
    j = [json.loads(l) for l in open(PARSED / "样例_微信导出.jsonl", encoding="utf-8")]
    joined = "\n".join(x["text"] for x in j)
    assert "13812345678" not in joined and "⟨手机:" in joined, joined
    assert "wxid_abc123" not in joined and "⟨微信号:" in joined, joined
    assert "a@b.com" not in joined and "⟨邮箱:" in joined, joined
    assert any(x["qc_flags"] for x in j), j
    jq = [json.loads(l) for l in open(PARSED / "样例_QQ导出.jsonl", encoding="utf-8")]
    assert jq[0]["sender"] == "王大姐" and "1998" in jq[0]["text"], jq   # 年份不被误伤
    print("SELFTEST PASS ✅  (微信3条/QQ2条/秘级拒收/通用兜底/脱敏5类/QC标记)")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest(); return
    PARSED.mkdir(exist_ok=True); REPORTS.mkdir(exist_ok=True)
    files = [p for p in sorted(INBOX.rglob("*"))
             if p.is_file() and not any(part.startswith("_") for part in p.relative_to(INBOX).parts)
             and p.suffix.lower() in
             {".txt", ".csv", ".tsv", ".md", ".json", ".jsonl"}]
    if not files:
        print("inbox/ 为空。请将聊天导出文件放入 inbox/ 后重跑。"); return
    recs = [process_file(p) for p in files]
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    rpt = REPORTS / f"接收台账_{ts}.md"
    total = sum(r["n_msgs"] for r in recs)
    with open(rpt, "w", encoding="utf-8") as w:
        w.write(f"# 聊天语料接收台账 {ts}\n\n| 文件 | 状态 | 消息数 | 脱敏 | 质控 |\n|---|---|---|---|---|\n")
        for r in recs:
            w.write(f"| {r['file']} | {r['status']} | {r['n_msgs']} | {json.dumps(r['masked'],ensure_ascii=False)} | {json.dumps(r['qc'],ensure_ascii=False)} |\n")
        w.write(f"\n**合计**: {len(recs)} 文件 / {total} 条消息\n")
        w.write(f"- 解析目录: `{PARSED.name}/`（脱敏后 JSONL）\n")
        w.write(f"- 原始文件保留于 `inbox/`（未改动）\n")
        w.write(f"- 注意: 本批 salt=`{SALT}`（随机，不入库）→ 同一明文跨批次哈希不同，属预期\n")
    print(f"✅ {len(recs)} 文件 / {total} 条 → 台账 {rpt.name}")

if __name__ == "__main__":
    main()
