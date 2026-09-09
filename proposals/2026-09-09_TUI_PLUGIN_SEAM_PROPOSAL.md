# TUI 渲染插片化提案（灵元 1.0）

> 状态：已实证校验修订，待排期
> 关联：V2 提案 E 类债 / A3；docs/LINGCLAUDE_REFACTOR.md（灵元 1.0 路线）

## 一、实证基线（2026-09-09 实测）

| 文件 | 行数 | 职责 | 耦合 |
|---|---|---|---|
| cli/status.py | 108 | StatusModel + toolbar 片段 | 纯数据零依赖 |
| cli/input_queue.py | 144 | FIFO 队列 + EOF 哨兵 | 零依赖 |
| cli/interface.py | 192 | PT/Fallback 抽象 + install_bottom_toolbar | 依赖 prompt_toolkit（可选） |
| cli/display.py | 359 | Rich 面板 + Markdown/StatusBar | 依赖 rich（可选） |
| cli/app.py | 1942* | _interactive_loop 混合调用上述 4 件 | 4 插片焊死 |

\* V2 提案记录 1911 行，+31 行来自 2026-09-09 的 args_preview list 参数防御修复（见 docs/LINGCLAUDE_REFACTOR.md 坑 2）。

app.py 对 TUI 子件的 import 实况（**5 处**，非 3 处）：
- L21 `from lingclaude.cli.display import ...`（模块级）
- L36 `from lingclaude.cli.interface import ...`（模块级）
- L558 `from lingclaude.cli.display import print_markdown`（函数内延迟）
- L628-629 `from lingclaude.cli.input_queue/status import ...`（函数内延迟）

## 二、对初版方案的三处硬伤修订

### R1：SeamType/Seam.register/Seam.get 是虚构 API
代码库中不存在。真实协议（lingclaude/lacp/capability_seam.py）：
- `CapabilitySeam(name, interface)` 实例 + `register_provider(provider, default)` + `get_provider(name)`
- 全局注册表 `_CAPABILITY_SEAMS` 现有 5 seam：fs/shell/llm/subagent/sandbox
- 入口 `get_capability_seam(name)` @ L220，未知 name 抛 ValueError

### R2：双签雷（"缝建而不用"的真实机理）
`SignedProvider.execute()` 未双签抛 PermissionError；
`register_provider` 写死 `SignedProvider(provider)`，required_signers 默认 {"lingclaude","lingminopt"}；
全库 **0 处调用 .sign()** → 现有 fs/shell/sandbox 默认 provider 均未审批，execute 必炸。
TUI 每 token 高频渲染，直接套用 = 第一个 token 即 PermissionError。
**解法**：`register_provider` 增加 `required_signers: list[str] | None = None` 透传参数（2 行改动，向后兼容），TUI 注册时传 `[]` 免签——纯展示无副作用，不适用能力执行的双签安全模型。

### R3：E6 归并错误
E6 = core→engine 倒装 7 处（query_engine.py:26→tool_router、tool_executor.py:13→mcp_proxy、tool_call_executor.py:77→verification_gate、mcp_tools.py:11,64,122），**无一处 TUI**。
TUI 插片化清的是 A3 的一部分 + E 类哲学反例，E6 一处不减，仍按 V2 原计划下沉 runtime 装配层。

## 三、修订后实施步骤

**Step A**（低风险）：capability_seam 最小扩展（required_signers 透传 + TUI_SEAM 注册表条目）+ lingclaude_plugins/tui/ 骨架：
- default_renderer.py ← display.py 的 Rich+Markdown 迁移
- bottom_toolbar.py ← status.py 的 StatusModel+toolbar_fragments 迁移
- simple_prompt.py / fallback_renderer.py
- __init__.py: install() → TUI_SEAM.register_provider(..., required_signers=[])

**Step B**：app.py 按实际 5 处 import 改缝调用（`get_capability_seam("tui").get_provider(...)`），rich/fallback 双路径全通。

**Step C**（重写）：清 E 类 TUI 侧违规作插片哲学演示件；engine E6 不动。

## 四、commit 拆分

1. `feat(lacp): capability_seam 支持 required_signers 透传 + tui seam 注册` + 单测
2. `refactor(tui): 插片骨架迁移 + 免签注册` + 5 测试
3. `refactor(app): _interactive_loop 5 处 TUI import 改缝调用`
4. `docs: 本提案与 V2 对齐说明`

## 五、明确不做（灵元 1.0 暂缓）

- P2 全屏 TUI（Application+Layout 状态栏常驻）——V2 A1 范畴，独立工期
- prompt 内嵌轮次显示——toolbar 已有，避免信息冗余
