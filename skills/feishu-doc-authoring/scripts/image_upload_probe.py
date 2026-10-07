# -*- coding: utf-8 -*-
"""终局: 用户身份上传(带media scope的新token) -> 插图到A/B图片位 -> 删占位块 -> 验收."""
import json, time, urllib.request, urllib.parse, ssl

CTXJ = json.load(open('/tmp/feishu_doc_work/inplace_ctx.json'))
docA, docB = CTXJ['A']['obj'], CTXJ['B']['obj']
LOG = open('/tmp/feishu_doc_work/img_final.log', 'a', buffering=1)
CTXS = ssl.create_default_context()
BASE = 'https://open.feishu.cn/open-apis/docx/v1/documents/%s/blocks/%s/children'
DBASE = 'https://open.feishu.cn/open-apis/docx/v1/documents/%s/blocks/%s/children/batch_delete'

def P(s): LOG.write('%s %s\n' % (time.strftime('%H:%M:%S'), s))

tok = json.load(open('/home/ai/lingclaude/tmp/feishu_user_token.json'))
T = tok.get('user_access_token') or tok.get('access_token')
P('scope=%s' % tok.get('scope', ''))

def call(method, url, body=None, tok=T):
    rq = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json'}, method=method)
    try: return json.load(urllib.request.urlopen(rq, timeout=60, context=CTXS))
    except urllib.error.HTTPError as e:
        try: return json.loads(e.read().decode())
        except Exception: return {'code': e.code, 'msg': str(e)[:150]}

def upload(png, node, tok=T):
    bnd = '----lk%d' % time.time()
    parts = []
    def field(name, val):
        parts.extend([('--%s' % bnd).encode(), ('Content-Disposition: form-data; name="%s"' % name).encode(), b'',
                      str(val).encode()])
    field('file_name', png.split('/')[-1]); field('parent_type', 'docx_image'); field('parent_node', node)
    field('size', len(open(png, 'rb').read()))
    parts.extend([('--%s' % bnd).encode(), b'Content-Disposition: form-data; name="file"; filename="x.png"',
                  b'Content-Type: image/png', b'', open(png, 'rb').read(), ('--%s--' % bnd).encode()])
    rq = urllib.request.Request('https://open.feishu.cn/open-apis/drive/v1/medias/upload_all',
        data=b'\r\n'.join(parts), headers={'Authorization': 'Bearer ' + tok,
        'Content-Type': 'multipart/form-data; boundary=' + bnd})
    try:
        r = json.loads(urllib.request.urlopen(rq, timeout=60, context=CTXS).read())
        return r.get('code'), (r.get('data') or {}).get('file_token'), str(r.get('msg'))[:50]
    except urllib.error.HTTPError as e:
        try: j = json.loads(e.read()); return j.get('code'), None, str(j.get('msg'))[:80]
        except Exception: return 'HTTP%s' % e.code, None, str(e)[:80]

def blocks_flat(doc):
    items, pt = [], ''
    while True:
        q = {'page_size': 500}
        if pt: q['page_token'] = pt
        u = ('https://open.feishu.cn/open-apis/docx/v1/documents/%s/blocks?' % doc) + urllib.parse.urlencode(q)
        r = call('GET', u)
        if r.get('code') != 0: break
        d = r.get('data') or {}
        items += d.get('items') or []
        pt = d.get('page_token') or ''
        if not pt: break
    pid = items[0]['block_id']
    return [b for b in items if b.get('parent_id') == pid], pid

def placeholder_pos(doc):
    ch, _ = blocks_flat(doc)
    for k, b in enumerate(ch):
        t = ''
        for key in ('text', 'heading1', 'heading2'):
            if key in b:
                t = ''.join(e.get('text_run', {}).get('content', '') for e in (b[key].get('elements') or []))
        if '图片位：' in t: return k, len(ch)
    return None, len(ch)

def install(doc, png, wh):
    ppos, n = placeholder_pos(doc)
    P('%s placeholder@%s of %d' % (doc[:8], ppos, n))
    if ppos is None:
        P('%s no placeholder (already has img?)' % doc[:8]); return False
    c1, mt, m1 = upload(png, doc)
    P('upload code=%s media=%s msg=%s' % (c1, (mt or 'NONE')[:20], m1))
    if not mt: return False
    r = call('POST', BASE % (doc, doc), {'children': [{'block_id': '', 'block_type': 27,
        'image': {'token': mt, 'width': wh[0], 'height': wh[1]}}], 'index': ppos})
    P('insert code=%s msg=%s' % (r.get('code'), str(r.get('msg'))[:60]))
    if r.get('code') != 0: return False
    time.sleep(2)
    ppos2, _ = placeholder_pos(doc)
    if ppos2 is not None:
        r2 = call('POST', DBASE % (doc, doc), {'start_index': ppos2, 'end_index': ppos2 + 1})
        P('del placeholder code=%s msg=%s' % (r2.get('code'), str(r2.get('msg'))[:60]))
    return True

okA = install(docA, '/tmp/feishu_doc_work/assets/chart_dualtriangle.png', (1076, 630))
time.sleep(3)
okB = install(docB, '/tmp/feishu_doc_work/assets/chart_workflow.png', (1308, 515))

for doc, tag in ((docA, 'A'), (docB, 'B')):
    ch, _ = blocks_flat(doc)
    imgs = sum(1 for b in ch if b.get('block_type') == 27)
    tbls = sum(1 for b in ch if b.get('block_type') == 31)
    h1 = sum(1 for b in ch if b.get('block_type') == 3)
    ph = sum(1 for b in ch if any('图片位：' in ''.join(
        e.get('text_run', {}).get('content', '') for e in (b.get(k, {}).get('elements') or []))
        for k in ('text', 'heading1', 'heading2')))
    P('[%s] FINAL blocks=%d img=%d tbl=%d h1=%d placeholder_left=%d' % (tag, len(ch), imgs, tbls, h1, ph))
P('IMG_FINAL DONE okA=%s okB=%s' % (okA, okB))
