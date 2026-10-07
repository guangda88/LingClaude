#!/usr/bin/env python3
"""生成三张统一风格中文图表: 七维流程 / 五条防护规则 / 双三角协作"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import os

# 中文字体
FP = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
if not os.path.exists(FP):
    FP = '/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc'
fm.fontManager.addfont(FP)
PROP = fm.FontProperties(fname=FP)
plt.rcParams['font.family'] = PROP.get_name()
plt.rcParams['axes.unicode_minus'] = False

OUT = '/tmp/feishu_doc_work/assets'
BLUE, GREEN, ORANGE, GRAY = '#3370ff', '#34c724', '#ff8800', '#646a73'
DEEP = '#1f2329'

def save(fig, name):
    fig.savefig(f'{OUT}/{name}', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    print('saved', name)

# ============ 图1: 七维总结读书法工作流 ============
fig, ax = plt.subplots(figsize=(11, 4.2))
ax.set_xlim(0, 11); ax.set_ylim(0, 4.2); ax.axis('off')
dims = [
    ('①讲了什么', '内容提炼\n金句/数字', BLUE),
    ('②底层思维', '3-6条\n方法论内核', BLUE),
    ('③认知转变', '旧vs新\n对照表', BLUE),
    ('④当下行动', '可执行\n清单', GREEN),
    ('⑤易误读', '主张-证据\n-核查', ORANGE),
    ('⑥A1关联', '真实经历\n对照', GREEN),
    ('⑦关联节点', '概念5-10个\n图谱检索', GRAY),
]
for i, (t, sub, c) in enumerate(dims):
    x = 0.25 + i * 1.52
    box = FancyBboxPatch((x, 1.5), 1.32, 1.7, boxstyle='round,pad=0.08',
                         fc='white', ec=c, lw=2.2)
    ax.add_patch(box)
    ax.text(x+0.66, 2.75, t, ha='center', va='center', fontsize=11.5,
            fontweight='bold', color=DEEP, fontproperties=PROP)
    ax.text(x+0.66, 2.0, sub, ha='center', va='center', fontsize=8.5,
            color=GRAY, fontproperties=PROP)
    if i < 6:
        ax.add_patch(FancyArrowPatch((x+1.42, 2.35), (x+1.62, 2.35),
                     arrowstyle='-|>', mutation_scale=14, color=GRAY, lw=1.4))
ax.text(5.5, 3.75, '七维总结读书法 · 固定输出契约', ha='center', fontsize=15,
        fontweight='bold', color=DEEP, fontproperties=PROP)
ax.text(5.5, 0.75, '输入: 课程讲稿/读书笔记 ≥800字    质检: 9项清单 + 缺陷防护规则 R1-R5',
        ha='center', fontsize=10, color=GRAY, fontproperties=PROP)
save(fig, 'chart_workflow.png')

# ============ 图2: 五条防护规则 ============
fig, ax = plt.subplots(figsize=(10, 4.6))
ax.set_xlim(0, 10); ax.set_ylim(0, 4.6); ax.axis('off')
rules = [
    ('R1 围栏泄漏', '代码围栏\n包裹全文', 12.8),
    ('R2 标题重复', '维度标题\n重复出现', 8.7),
    ('R3 表格层级', '对照表被写成\n独立标题', 18.0),
    ('R4 末维过短', '关联节点\n<40字', 2.9),
    ('R5 零编造', '无素材标"待补"\n禁止虚构', 0.0),
]
for i, (t, sub, v) in enumerate(rules):
    x = 0.3 + i * 1.95
    box = FancyBboxPatch((x, 1.35), 1.75, 2.35, boxstyle='round,pad=0.1',
                         fc='white', ec=BLUE, lw=2)
    ax.add_patch(box)
    ax.text(x+0.875, 3.3, t, ha='center', fontsize=11, fontweight='bold',
            color=DEEP, fontproperties=PROP)
    ax.text(x+0.875, 2.55, sub, ha='center', fontsize=8.5, color=GRAY, fontproperties=PROP)
    vc = ORANGE if v > 10 else (GREEN if v == 0 else BLUE)
    ax.text(x+0.875, 1.8, f'{v}%', ha='center', fontsize=15, fontweight='bold',
            color=vc, fontproperties=PROP)
ax.text(5, 4.25, '五条防护规则 · 来自 1743 篇存量产物缺陷审计', ha='center',
        fontsize=14.5, fontweight='bold', color=DEEP, fontproperties=PROP)
ax.text(5, 0.8, '缺陷率 = 该缺陷在 1743 篇七维总结中的占比 · 生成后必须过 9 项质检',
        ha='center', fontsize=9.5, color=GRAY, fontproperties=PROP)
save(fig, 'chart_rules.png')

# ============ 图3: 人机协作双三角 ============
fig, ax = plt.subplots(figsize=(9, 5.2))
ax.set_xlim(0, 9); ax.set_ylim(0, 5.2); ax.axis('off')
def tri(cx, cy, title, items, color):
    pts = [(cx, cy+1.15), (cx-1.25, cy-0.75), (cx+1.25, cy-0.75)]
    t = plt.Polygon(pts, fc='white', ec=color, lw=2.4)
    ax.add_patch(t)
    ax.text(cx, cy+1.38, title, ha='center', fontsize=13, fontweight='bold',
            color=color, fontproperties=PROP)
    for (ly, txt) in [(cy+0.35, items[0]), (cy-0.6, items[1]), (cy-0.6, items[2])]:
        pass
    ax.text(pts[0][0], cy+0.32, items[0], ha='center', fontsize=9.5, color=DEEP, fontproperties=PROP)
    ax.text(pts[1][0]-0.12, cy-0.92, items[1], ha='center', fontsize=9.5, color=DEEP, fontproperties=PROP)
    ax.text(pts[2][0]+0.12, cy-0.92, items[2], ha='center', fontsize=9.5, color=DEEP, fontproperties=PROP)
tri(2.3, 2.5, '人类三角', ['审美·知道什么是好', '体系·七维固定流程', '创造力·批判与A1'], ORANGE)
tri(6.7, 2.5, 'AI 三角', ['会听话·守契约', '数据包·模板+样例', '帮你找场景·追问行动'], BLUE)
ax.add_patch(FancyArrowPatch((3.7, 2.5), (5.3, 2.5), arrowstyle='<|-|>',
             mutation_scale=18, color=GRAY, lw=1.8))
ax.text(4.5, 2.72, '协作', ha='center', fontsize=10.5, color=GRAY, fontproperties=PROP)
ax.text(4.5, 4.7, '七维读书助手 · 人机分工双三角', ha='center', fontsize=14.5,
        fontweight='bold', color=DEEP, fontproperties=PROP)
ax.text(4.5, 0.35, '人管目标/边界/价值判断 · AI管执行/检索/规模化', ha='center',
        fontsize=10, color=GRAY, fontproperties=PROP)
save(fig, 'chart_dualtriangle.png')
print('ALL DONE')
