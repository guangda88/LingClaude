"""灵克 seams 层 — 跨模块业务缝（seam）。

I3 (2026-09-15): 首个 seam 为 external_query（LLM 直连/兜底、GitHub/PyPI/版本/
项目/提交/文件分析），从 api.py 抽出。后续 I1/I2 状态存储等业务缝照此模式落位。
"""
