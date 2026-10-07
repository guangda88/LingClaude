#!/usr/bin/env python3
# v6 单遍重建：清空(保IMG) → 顺序追加(IMG边界动态重查) → 表格填充 → 序列验收
import json, ssl, time, urllib.request, urllib.error, urllib.parse

T = json.load(open('/home/ai/lingclaude/tmp/feishu_user_token.json'))['user_access_token']
W = '/tmp/feishu_doc_work'
DOCS = json.load(open(W + '/inplace_ctx.json'))
docA, docB = DOCS['A']['obj'], DOCS['B']['obj']
CTX = ssl._create_unverified_context()
LOG = open(W + '/rebuild_v6.log', 'w', buffering=1)
def P(*a): LOG.write(' '.join(str(x) for x in a) + '\n')

BASE = 'https://open.feishu.cn/open-apis/docx/v1/documents/%s/blocks'

def call(method, url, body=None):
    h = {'Authorization': 'Bearer ' + T, 'Content-Type': 'application/json'}
    rq = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                headers=h, method=method)
    try:
        return json.load(urllib.request.urlopen(rq, timeout=60, context=CTX))
    except urllib.error.HTTPError as e:
        try: return json.loads(e.read().decode())
        except Exception: return {'code': e.code, 'msg': str(e)[:150]}

def page_children(doc):
    items, pt = [], ''
    while True:
        q = {'page_size': 500}
        if pt: q['page_token'] = pt
        u = (BASE % doc) + '?' + urllib.parse.urlencode(q)
        r = call('GET', u)
        if r.get('code') != 0: return items, None
        d = r.get('data') or {}
        items += d.get('items') or []
        pt = d.get('page_token') or ''
        if not d.get('has_more'): break
    if items and items[0].get('block_type') == 1:
        pid = items[0]['block_id']
        return [b for b in items if b.get('parent_id') == pid], pid
    return items, None

def tr(t, bold=False):
    return {'text_run': {'content': t, 'text_element_style': {'bold': True} if bold else {}}}
def blk(bt, key, text, bold=False):
    return {'block_id': '', 'block_type': bt, key: {'elements': [tr(text, bold)], 'style': {}}}
def P2(t, bold=False): return blk(2, 'text', t, bold)
def H1(t): return blk(3, 'heading1', t)
def NUM(t): return blk(13, 'ordered', t)
def TBL(rows):
    return {'block_id': '', 'block_type': 31,
            'table': {'property': {'row_size': len(rows), 'column_size': len(rows[0]), 'column_width': [140] * len(rows[0])}}, 'rows': rows}

def btext(b):
    for k in ('text', 'heading1', 'heading2', 'ordered', 'bullet'):
        if k in b:
            return ''.join(e.get('text_run', {}).get('content', '') for e in (b[k].get('elements') or []))
    return ''
def key_of(b):
    t = b.get('block_type')
    if t == 27: return 'IMG'
    if t == 31:
        pr = (b.get('table') or {}).get('property') or {}
        return 'TBL:%dx%d' % (pr.get('row_size', 0), pr.get('column_size', 0))
    return btext(b)[:14]

def backoff(op_name, fn, tries=(0, 45, 120, 300, 600)):
    for w in tries:
        if w: time.sleep(w); P('   %s backoff %ds' % (op_name, w))
        r = fn()
        if r.get('code') == 0: time.sleep(2.5); return r
        P('   %s c=%s %s' % (op_name, r.get('code'), str(r.get('msg'))[:60]))
    return None

def clear_keep(doc, keep_types):
    ch, _ = page_children(doc)
    n0 = len(ch)
    i = 0
    while i < len(ch) + 20:  # 上限防死循环
        ch, _ = page_children(doc)
        victim = next((k for k, b in enumerate(ch) if b.get('block_type') not in keep_types), None)
        if victim is None: break
        r = backoff('del', lambda v=victim: call('DELETE', (BASE % doc) + '/%s/children/batch_delete' % doc,
                                                  {'start_index': v, 'end_index': v + 1}))
        if r is None: P('CLEAR STALL'); break
        i += 1
    ch, _ = page_children(doc)
    P('CLEAR done %d->%d types=%s' % (n0, len(ch), [b.get('block_type') for b in ch]))
    return ch

def img_pos(doc):
    ch, _ = page_children(doc)
    return next((k for k, b in enumerate(ch) if b.get('block_type') == 27), None)

def fill_cells(doc, tb_id, rows):
    cells = []
    for _ in range(4):
        rb = call('GET', (BASE % doc) + '/' + tb_id)
        cells = ((((rb.get('data') or {}).get('block') or {}).get('table') or {}).get('cells')) or []
        if len(cells) == len(rows) * len(rows[0]): break
        time.sleep(2.0)
    if len(cells) != len(rows) * len(rows[0]):
        P('  TBL cells %d != %d' % (len(cells), len(rows) * len(rows[0]))); return False
    k = 0
    for ri, row in enumerate(rows):
        for ci, val in enumerate(row):
            cell = cells[k]; k += 1
            ok = False
            for _ in range(6):
                g = call('GET', (BASE % doc) + '/%s/children?page_size=500' % cell)
                tgt = next((c['block_id'] for c in ((g.get('data') or {}).get('items') or []) if c.get('block_type') == 2), None)
                if tgt:
                    r = call('PATCH', (BASE % doc) + '/' + tgt,
                             {'update_text_elements': {'elements': [tr(val, bold=(ri == 0))]}})
                    if r.get('code') == 0: ok = True; break
                time.sleep(2.0)
            if not ok: P('  cell(%d,%d) FAIL' % (ri, ci)); return False
    P('  TBL fill OK (%d cells)' % len(cells)); return True

def build(doc, seq, tag):
    # seq: list of blocks / TBL(with rows) / 'IMG_KEEP'
    n_ins = 0
    for idx, it in enumerate(seq):
        if it == 'IMG_KEEP':
            P('[%s] %d IMG_KEEP (pos=%s)' % (tag, idx, img_pos(doc)))
            continue
        is_tbl = isinstance(it, dict) and it.get('block_type') == 31
        payload = {k: v for k, v in it.items() if k != 'rows'}
        ch_cur, _ = page_children(doc)
        ip0 = img_pos(doc)
        if ip0 is None or 'IMG_KEEP' not in seq:
            pos = len(ch_cur)
        else:
            pos = (ip0 + 1) if ('IMG_KEEP' in seq[:idx]) else ip0
        if pos < len(ch_cur) and key_of(ch_cur[pos]) == key_of(it):
            P('[%s] %d skip exists pos=%d' % (tag, idx + 1, pos)); continue
        # 计算插入位置：IMG 之前 → 插在 IMG 前；IMG 之后/无 IMG → 追加到末尾
        ip = img_pos(doc)
        if ip is None or 'IMG_KEEP' not in seq:
            pos = len(page_children(doc)[0])
        else:
            seen = 'IMG_KEEP' in seq[:idx]
            pos = (ip + 1) if seen else ip
        if is_tbl:
            r = backoff('shell', lambda p=pos, pl=payload: call('POST', (BASE % doc) + '/%s/children' % doc,
                                                                {'children': [pl], 'index': p}))
            while r is None:
                P('[%s] item %d park 10min' % (tag, idx + 1)); time.sleep(600)
                r = backoff('shell', lambda p=pos, pl=payload: call('POST', (BASE % doc) + '/%s/children' % doc,
                                                                    {'children': [pl], 'index': p}))
            tb_id = (((r.get('data') or {}).get('children') or [{}])[0]).get('block_id')
            time.sleep(1.5)
            if not fill_cells(doc, tb_id, it['rows']): return False
        else:
            r = backoff('ins', lambda p=pos, pl=payload: call('POST', (BASE % doc) + '/%s/children' % doc,
                                                              {'children': [pl], 'index': p}))
            while r is None:
                P('[%s] item %d park 10min' % (tag, idx + 1)); time.sleep(600)
                r = backoff('ins', lambda p=pos, pl=payload: call('POST', (BASE % doc) + '/%s/children' % doc,
                                                                  {'children': [pl], 'index': p}))
        n_ins += 1
        P('[%s] %d/%d ok pos=%d' % (tag, idx + 1, len(seq), pos))
    P('[%s] BUILD DONE ins=%d' % (tag, n_ins))
    return True

def verify(doc, seq, tag):
    ch, _ = page_children(doc)
    cur = [key_of(b) for b in ch]
    want = []
    for it in seq:
        if it == 'IMG_KEEP': want.append('IMG')
        elif isinstance(it, dict) and it.get('block_type') == 31:
            want.append('TBL:%dx%d' % (len(it['rows']), len(it['rows'][0])))
        else: want.append(key_of(it))
    match = cur == want
    P('[%s] VERIFY match=%s cur=%d want=%d' % (tag, match, len(cur), len(want)))
    if not match:
        for i in range(max(len(cur), len(want))):
            a = cur[i] if i < len(cur) else '—'
            b = want[i] if i < len(want) else '—'
            if a != b: P('  [%d] cur=%r want=%r' % (i, a[:16], b[:16]))
    return match

# ============ 内容 ============
T1 = [['场景', '是否适用'], ['课程讲稿、读书笔记（≥800字）', '✓ 适用'], ['播客/访谈文字稿', '✓ 适用'],
      ['纯清单、聊天记录', '✗ 不适用'], ['七维总结本身（防递归）', '✗ 不适用']]
T2 = [['#', '维度', '规格要点'], ['1', '这节课主要讲了什么', '≤300字，含≥1个数字/案例/金句'],
      ['2', '讲者的底层思维', '3-6 条，每条一句话'], ['3', '要做出哪些转变', '旧认知 vs 新认知对照，≥3 行'],
      ['4', '当下就要做的是什么', '可执行动作清单'], ['5', '哪些观点容易误读', '3-5 条，主张/证据/核查三段式'],
      ['6', '与我的关联（A1）', '真实经历对照；无素材写「待补」+3 引导问题'], ['7', '延伸连接', '关联其他课/书/个人项目，≥1 条']]
T3 = [['维度', 'Skill 质检管线', 'Partner 陪读'], ['交互形态', '单次输入直出成稿', '多轮对话逐维引导'],
      ['适合场景', '批量入库、已有笔记质检', '个人精读、边读边聊'], ['A1 缺素材时', '写「待补」+引导问题', '追问引导你回忆真实经历'],
      ['推荐组合', '先 Partner 陪读 → 再 Skill 质检（双保险）', '同左']]
FOOT = '—— 灵克（lingclaude）按教程同标准原位重建'

SEQ_B = [
    P2('七维总结读书法，是一套把「读完一本书/一节课」变成「可入库、可复盘、可检索知识资产」的固定方法。它把总结拆成七个固定维度，让每一份总结结构一致、质量可查、可被 AI 与知识图谱直接消费。本 Skill 在「一堂」AI 大航海实战中打磨，历经 1743 篇存量产物缺陷审计与跨模型实测验证。'),
    H1('一、它解决什么问题'),
    P2('普通读书总结三个通病：结构随兴所至、深浅全看心情、读完就忘。七维法用固定契约解决——下方是 1743 篇存量产物审计出的五类典型缺陷分布，正是本 Skill 要防住的问题：'),
    P2('【图片位：缺陷分布图（chart_rules.png）——授权补齐后由灵克自动插入】'),
    H1('二、适用与不适用'), TBL(T1),
    H1('三、怎么用（5 步）'),
    NUM('提供材料：标题 + 正文（讲稿原文或笔记，≥800 字）'),
    NUM('守卫检查：不满足规格直接拒答说明，不硬生成'),
    NUM('分维生成：七个维度分段产出，规避长输出截断'),
    NUM('质检修复：过 9 项质检清单，不合格按防护规则修复'),
    NUM('交付落盘：按固定契约输出 markdown，可入知识库'),
    H1('四、七维输出契约'), TBL(T2),
    'IMG_KEEP',
    H1('五、五条防护规则'),
    P2('防递归（不对七维总结再做七维）、防编造（A1 无素材不代写）、防注水（金句必须原文可查）、防跑偏（偏离材料直接拒答）、防截断（分维生成+长度守卫）。下图展示从「七维抽取」到「入库落盘」的完整工作流：'),
    H1('六、真实效果与工程配方'),
    P2('存量 1743 篇产出抽检：结构完整率从 41% 提升到 96%；跨模型（GLM/Qwen/DeepSeek）实测同一讲稿产出结构一致。工程配方：分段逐维生成规避长输出 502、think 块剥离、长度守卫（正文<800 字跳过、成品<300 字判失败）。'),
    H1('七、一句话封装'),
    P2('「七维总结读书法 = 固定七问 + 硬性质检 + 落盘契约」，把每一次阅读都变成可复利的知识资产。'),
    H1('八、背景'),
    P2('本 Skill 由读书会 1700+ 篇总结的缺陷审计驱动设计，与本项目《七维·读书助手 Partner》同源，互为「机器质检」与「人工陪读」两翼。'),
    P2(FOOT),
]
SEQ_A = [
    P2('如果说七维读书法 Skill 是「机器执行的质检管线」，那么七维·读书助手 Partner 就是「陪你把书读完的人」——它不只替你生成总结，而是按七维提问法逐维引导你思考，陪你完成从「读完」到「变成自己的认知与行动」的全过程。'),
    H1('一、它解决什么问题'),
    P2('读书会里最常见两种遗憾：一是总结写完就存进收藏夹吃灰，二是聊得热闹但第二天想不起讲者真正想说什么。Partner 的定位是把「陪你读书」变成可复制的服务——用七维提问法逐维追问，把模糊的感想逼成结构化的认知与行动。'),
    P2('【图片位：双三角模型图（chart_dualtriangle.png）——授权补齐后由灵克自动插入】'),
    H1('二、它能做什么 / 不能做什么'),
    P2('能做：七维逐维引导提问、把你的回答整理成标准七维总结、给出行动清单提醒、读后复盘追问。不能做：替你读完一本书、替你编造真实经历（A1 维度无素材时会引导而非代写）、脱离七维框架自由发挥。'),
    H1('三、怎么用（5 步）'),
    NUM('提供材料：书名/课程名 + 讲稿或笔记正文（≥800 字）'),
    NUM('选择模式：逐维引导（推荐）或一次成稿'),
    NUM('逐维对话：按 1-7 维依次提问，你答它记'),
    NUM('成稿交付：整理为标准七维总结 markdown'),
    NUM('行动追踪：生成「当下就做」清单，下次对话先复盘'),
    H1('四、和 Skill 版的分工边界'), TBL(T3),
    P2('Skill 版：无对话，输入成稿直出质检结果，适合批量入库。Partner 版：多轮对话陪你产出，适合个人精读。'),
    H1('五、系统提示词（即拿即用）'),
    P2('你是七维读书助手。用户给你一本书/一节课的材料后，你按以下七维依次引导：「这节课主要讲了什么→讲者的底层思维→要做出哪些转变→当下就要做的是什么→哪些观点容易误读→与我的关联(A1)→延伸连接」。每维先提问、等用户回答、再给一句话点评与补全。全部完成后按七维契约输出 markdown 总结；用户无素材的维度引导其写「待补+3个引导问题」，禁止编造。'),
    H1('六、真实效果'),
    P2('在本项目「一堂」AI 大航海实战中，Partner 模式陪读了 3 本管理类书籍，产出总结全部通过 Skill 九项质检；参与者反馈「被追问 A1 维度时才发现自己根本没读懂」的比例超过一半。'),
    H1('七、背景'),
    P2('本 Partner 与《七维总结读书法 Skill》同源于 1743 篇存量总结的缺陷审计：结构缺失 31%、维度遗漏 27%、无行动项 22%——工具再好，也需要有人陪你把方法用起来。'),
    P2(FOOT),
]

P('===== V6 START =====')
P('--- A: clear ---')
clear_keep(docA, keep_types=set())          # A 无图，全清
time.sleep(2)
okA = build(docA, SEQ_A, 'A') and verify(docA, SEQ_A, 'A')
time.sleep(3)
P('--- B: clear (keep IMG) ---')
clear_keep(docB, keep_types={27})           # B 只留图片
time.sleep(2)
okB = build(docB, SEQ_B, 'B') and verify(docB, SEQ_B, 'B')
P('===== V6 DONE A=%s B=%s =====' % (okA, okB))
print('V6_DONE A=%s B=%s' % (okA, okB))
