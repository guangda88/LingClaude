# 笔误修正台账（typo fixes ledger）

> 2026-09-25 补录。背景：验证误差台账 ERR-2026-0925-01 事故后，对建闸期各 commit message
> 的笔误做注记式修正（notes append，不改写历史提交）。纪律：只注记不改史（git notes 是唯一合法通道）。

## T-01：53a0166（建闸①熔断闸）回归测试文件名笔误

- **原文**：`回归 37/37 全绿（plan_c_v4+contract+s4_plugin_loader）`
- **实况**：测试文件实为 `tests/test_s4_plugin_loader_mechanism.py`（message 中缺 `_mechanism` 后缀）。
  测试结果本身真实（37 passed 于 2026-09-25 审计轮复现），仅文件名引用不精确。
- **注记**：`git notes append` 到 53a0166（2026-09-25 执行，输出 "notes appended" 实证）。

## T-02：口径备注——「3 个 import 点」旧账勘误

- 前轮记载"TRANSPORT 有 3 个 import 点待迁"，2026-09-25 实测：
  `SeamType.TRANSPORT` 在 seam.py 之外全仓零引用（grep 含测试外代码）。
- 旧数字来源待考（或为 wiring 内部字符串/或为更早版本残留），以本次 grep 为准。
- 影响：TRANSPORT 首刀按"撤缝"执行（既定裁决），无迁移动作。
