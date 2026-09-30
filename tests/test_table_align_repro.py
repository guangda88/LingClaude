"""最小复现：表格对齐定位脚本。

跑法: python -m pytest tests/test_table_align_repro.py -s 或直接 python tests/test_table_align_repro.py
目的: 区分错位表属于 (A) 带 SGR 粗体/颜色 还是 (B) 纯 Markdown | 表无颜色。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lingclaude.cli.interface import (
    _extract_sgr_styles,
    _line_disp_width,
    _col_to_char_index,
)


def dump_case(name: str, raw_line: str) -> None:
    print(f"\n=== {name} ===")
    clean, spans = _extract_sgr_styles(raw_line)
    print(f"raw     : {raw_line!r}")
    print(f"clean   : {clean!r}")
    print(f"codepoints={len(clean)}  wcwidth={_line_disp_width(clean)}  diff={_line_disp_width(clean)-len(clean)}")
    print(f"spans   : {spans}")
    for lo, hi, st in spans:
        # 用 _col_to_char_index 把显示列端点落回字符下标，切出段文本
        a = _col_to_char_index(clean, lo)
        b = _col_to_char_index(clean, hi)
        seg = clean[a:b]
        print(f"  span ({lo},{hi}) {st:12s} char[{a}:{b}] seg={seg!r} seg_wcwidth={_line_disp_width(seg)}")


# ── 用例 A：带 SGR 粗体中文列（模拟 rich Table 某列 style='bold' 走 keep_sgr 清洗后）──
ESC = "\x1b"
# "步骤" 粗体 + "产出" 普通，全角列
case_a = f"{ESC}[1m步骤产出{ESC}[0m结论{ESC}[1m备注说明{ESC}[0m"

# ── 用例 B：纯 Markdown 表行（无 SGR，无任何 ESC）──
case_b = "| 步骤 | 产出 | 结论 |"

# ── 用例 C：带 SGR + 越界 span（模拟上一轮发现的 span 越界现象）──
case_c = f"{ESC}[1m我的使用方式{ESC}[0m一个个开窗口"

if __name__ == "__main__":
    dump_case("A: SGR 粗体中文列", case_a)
    dump_case("B: 纯 Markdown 表行(无颜色)", case_b)
    dump_case("C: SGR 粗体长中文(越界探测)", case_c)
