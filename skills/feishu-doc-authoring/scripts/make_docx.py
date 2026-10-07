#!/usr/bin/env python3
"""Path B: markdown说明书 -> 图文docx (真标题/真表格/插图, 样式统一)"""
import sys, re
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

BLUE = RGBColor(0x33, 0x70, 0xFF)
DEEP = RGBColor(0x1F, 0x23, 0x29)
GRAY = RGBColor(0x64, 0x6A, 0x73)
CJK = 'Noto Sans CJK SC'

def set_font(run, size=11, bold=False, color=None):
    run.font.name = CJK
    run._element.rPr.rFonts.set(qn('w:eastAsia'), CJK)
    run.font.size = Pt(size)
    run.font.bold = bold
    if color: run.font.color.rgb = color

def add_md_runs(p, text):
    """处理 **加粗** 与 `代码` 内联标记"""
    pos = 0
    for m in re.finditer(r'\*\*(.+?)\*\*|`(.+?)`', text):
        if m.start() > pos:
            set_font(p.add_run(text[pos:m.start()]), color=DEEP)
        if m.group(1) is not None:
            set_font(p.add_run(m.group(1)), bold=True, color=DEEP)
        else:
            r = p.add_run(m.group(2)); set_font(r, color=BLUE)
            r.font.name = 'monospace'
        pos = m.end()
    if pos < len(text):
        set_font(p.add_run(text[pos:]), color=DEEP)

def md2docx(md_path, docx_path, images):
    doc = Document()
    # 统一默认样式
    style = doc.styles['Normal']
    style.font.name = CJK
    style.element.rPr.rFonts.set(qn('w:eastAsia'), CJK)
    style.font.size = Pt(11)

    lines = open(md_path, encoding='utf-8').read().split('\n')
    img_keys = list(images.keys())  # 按顺序替换【图N：...】占位
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1; continue
        # 图片占位
        m = re.match(r'【(图\d+)[：:](.+?)】', s)
        if m:
            key = m.group(1)
            path = images.get(key)
            if path:
                p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.add_run().add_picture(path, width=Inches(6.0))
                cap = doc.add_paragraph(); cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                set_font(cap.add_run(m.group(2)), size=9, color=GRAY)
            i += 1; continue
        # 表格
        if s.startswith('|'):
            tbl_lines = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                tbl_lines.append(lines[i].strip()); i += 1
            rows = [[c.strip() for c in r.strip('|').split('|')] for r in tbl_lines
                    if not re.match(r'^\|[\s:\-|]+\|$', r)]
            if rows:
                t = doc.add_table(rows=len(rows), cols=len(rows[0]))
                t.style = 'Light Grid Accent 1'
                for ri, row in enumerate(rows):
                    for ci, cell in enumerate(row):
                        c = t.cell(ri, ci)
                        c.text = ''
                        add_md_runs(c.paragraphs[0], cell)
                        for r_ in c.paragraphs[0].runs:
                            if ri == 0: r_.font.bold = True
                            r_.font.size = Pt(10)
            continue
        # 标题
        m = re.match(r'^(#{1,3})\s+(.*)', s)
        if m:
            lvl = len(m.group(1)); txt = m.group(2)
            h = doc.add_heading('', level=lvl)
            r = h.add_run(txt); set_font(r, size={1:20,2:15,3:13}[lvl], bold=True,
                                          color=DEEP if lvl>1 else BLUE)
            i += 1; continue
        # 列表
        if re.match(r'^[-*]\s+', s) or re.match(r'^\d+\.\s+', s):
            txt = re.sub(r'^([-*]|\d+\.)\s+', '', s)
            p = doc.add_paragraph(style='List Bullet' if not re.match(r'^\d+\.', s) else 'List Number')
            add_md_runs(p, txt)
            i += 1; continue
        # 引用
        if s.startswith('>'):
            p = doc.add_paragraph(); p.paragraph_format.left_indent = Inches(0.3)
            add_md_runs(p, s.lstrip('> '))
            for r_ in p.runs: r_.font.color.rgb = GRAY
            i += 1; continue
        # 普通段落
        p = doc.add_paragraph()
        add_md_runs(p, s)
        i += 1
    doc.save(docx_path)
    print('DOCX saved:', docx_path)

if __name__ == '__main__':
    md, out = sys.argv[1], sys.argv[2]
    imgs = {}
    for kv in sys.argv[3:]:
        k, v = kv.split('=', 1)
        imgs[k] = v
    md2docx(md, out, imgs)
