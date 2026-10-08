#!/usr/bin/env python3
"""visualizer.py 大图防爆补丁（备份+锚点断言+py_compile 三重防呆）"""
import shutil, time, subprocess, sys

TARGET = '/home/ai/yitang/knowledge/graph_core/visualizer.py'
BAK = TARGET + '.bak_biggraph_' + str(int(time.time()))

src = open(TARGET, encoding='utf-8').read()

OLD = """        # 配色：取节点首个类型
        colors = []
        for n in G.nodes():
            t = G.nodes[n].get('types', ['Unknown'])[0]
            colors.append(TYPE_COLORS.get(t, TYPE_COLORS['Unknown']))

        # 布局 + 绘图
        plt.figure(figsize=(14, 10))
        try:
            pos = nx.spring_layout(G, k=1.2, seed=42, iterations=50)
        except Exception:
            pos = nx.circular_layout(G)
        nx.draw_networkx_nodes(G, pos, node_color=colors, node_size=1200, alpha=0.9)
        nx.draw_networkx_edges(G, pos, arrows=True, arrowstyle='->', width=1.2, edge_color='#bbbbbb', alpha=0.7)
        nx.draw_networkx_labels(G, pos, font_size=9, font_family='sans-serif')"""

NEW = """        # 大图防爆内存（2026-10-08 事故修复：3.2万节点 spring 布局会构造 8GB+ 稠密矩阵，
        # 曾致两次渲染期进程超时/被杀——改画度数最高子图，全部节点仍完整保留在 JSON 输出中）
        render_G = G
        if G.number_of_nodes() > 3000:
            top = sorted(G.nodes, key=G.degree, reverse=True)[:3000]
            render_G = G.subgraph(top).copy()
            title = f"{title}（仅展示度数最高3000/共{G.number_of_nodes()}节点）"

        # 配色：取节点首个类型
        colors = []
        for n in render_G.nodes():
            t = render_G.nodes[n].get('types', ['Unknown'])[0]
            colors.append(TYPE_COLORS.get(t, TYPE_COLORS['Unknown']))

        # 布局 + 绘图
        plt.figure(figsize=(14, 10))
        try:
            pos = nx.spring_layout(render_G, k=1.2, seed=42, iterations=50)
        except Exception:
            pos = nx.circular_layout(render_G)
        nx.draw_networkx_nodes(render_G, pos, node_color=colors, node_size=1200, alpha=0.9)
        nx.draw_networkx_edges(render_G, pos, arrows=True, arrowstyle='->', width=1.2, edge_color='#bbbbbb', alpha=0.7)
        nx.draw_networkx_labels(render_G, pos, font_size=9, font_family='sans-serif')"""

# 防呆1：锚点唯一性
if src.count(OLD) != 1:
    print(f"ANCHOR_FAIL count={src.count(OLD)}")
    sys.exit(1)

# 备份
shutil.copy2(TARGET, BAK)
print(f"BACKUP_OK {BAK}")

# 替换
open(TARGET, 'w', encoding='utf-8').write(src.replace(OLD, NEW))

# 防呆2：py_compile，失败回滚
r = subprocess.run([sys.executable, '-m', 'py_compile', TARGET], capture_output=True, text=True)
if r.returncode != 0:
    shutil.copy2(BAK, TARGET)
    print(f"COMPILE_FAIL_ROLLBACK {r.stderr[:300]}")
    sys.exit(1)
print("COMPILE_OK")
