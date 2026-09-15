# TUI 渲染插片化提案（灵元 1.0）

> 状态：已实证校验修订 v2（2026-09-15 全库 import 复核），待排期
> 关联：V2 提案 E 类债 / A3；docs/LINGCLAUDE_REFACTOR.md（灵元 1.0 路线）

## 一、实证基线（2026-09-15 复核）

| 文件 | 行数 | 职责 | 耦合 |
|---|---|---|---|
| cli/status.py | 108 | StatusModel + toolbar 片段 | 纯数据零依赖 |
| cli/input_queue.py | 144 | FIFO 队列 + EOF 哨兵 | 零依赖 |
| cli/interface.py | 192 | PT/Fallback 抽象 + install_bottom_toolbar | 依赖 prompt_toolkit（可选） |
| cli/display.py | 359 | Rich 面板 + Markdown/StatusBar | 依赖 rich（可选） |
| cli/app.py + repl.py + repl_io.py | — | 三处主流程混合调用上述 4 件 | 4 插片焊死 |

### 全库 TUI 子件直接 import 实况（**10 处 / 3 文件**）

| 文件 | 行号 | 子件 | 位置 |
|---|---|---|---|
| **cli/repl.py** | L23 | display（SessionSummary/print_session_summary） | 模块级 |
| cli/repl.py | L24 | input_queue（InputQueue） | 模块级 |
| cli/repl.py | L25 | interface（PromptSessionInterface 等） | 模块级 |
| cli/repl.py | L643 | input_queue（InputQueue） | 延迟（pump 重建） |
| cli/repl.py | L714 | input_queue（InputPump/InputQueue） | 延迟（pump 重建） |
| cli/repl.py | L715 | status（StatusModel/toolbar_fragments） | 延迟（toolbar 安装） |
| **cli/repl_io.py** | L13 | interface（PromptSessionInterface） | 延迟 |
| cli/repl_io.py | L182 | display（print_markdown） | 延迟 |
| **cli/app.py** | L21 | display | 模块级 |
| cli/app.py | L36 | interface | 模块级 |

**v2 关键修正**：初版记录"app.py 5 处"已过时——P0/P1 重构（commit 82f0c1d 会话假死/round 级消费）把原 app.py L558/L628-629 的延迟 import **迁入了 repl.py**（L643/L714 的 pump 重建、L715 的 toolbar 安装）。因此实际分布是 **repl.py 6 处（大头）+ repl_io.py 2 处 + app.py 2 处**，repl.py 才是改缝主战场，初版提案完全遗漏。

### input_queue 3 处例外决策

L24/L643/L714 的 input_queue import **不列入改缝**（保留 cli 内部件）：
- InputQueue 是**输入管道**（FIFO + pump 重建 + round 级消费），非渲染能力——P0/P1 刚重构的假死/中止核心路径，迁移风险 ≫ 收益
- 初版 Step A 本就没计划迁 input_queue（只迁 display/status/interface/prompt）
- 渲染插片化（tui seam）只覆盖 display/interface/status 三件；input_queue 作为"CLI 组装层正常引用的输入管道"明确例外

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

## 三、修订后实施步骤（v2 修订）

**Step A**（低风险）：capability_seam 最小扩展（required_signers 透传 + TUI_SEAM 注册表条目）+ lingclaude_plugins/tui/ 骨架：
- default_renderer.py ← display.py 的 Rich+Markdown 迁移
- bottom_toolbar.py ← status.py 的 StatusModel+toolbar_fragments 迁移
- simple_prompt.py / fallback_renderer.py
- __init__.py: install() → TUI_SEAM.register_provider(..., required_signers=[])

**Step B**（v2：改缝 7 处 / 2 文件，非初版 5 处 / 1 文件）：TUI 渲染三件（display/interface/status）直接 import 改缝调用：

| 文件 | 行号 | 改法 |
|---|---|---|
| cli/app.py | L21 | display 模块级 → `get_capability_seam("tui").get_provider("display")` |
| cli/app.py | L36 | interface 模块级 → `get_provider("interface")` |
| cli/repl.py | L23 | display 模块级 → 缝（SessionSummary/print_session_summary） |
| cli/repl.py | L25 | interface 模块级 → 缝（PromptSessionInterface 等） |
| cli/repl.py | L715 | status 延迟 → 缝（StatusModel/toolbar_fragments，toolbar 安装） |
| cli/repl_io.py | L13 | interface 延迟 → 缝（PromptSessionInterface） |
| cli/repl_io.py | L182 | display 延迟 → 缝（print_markdown） |

- **input_queue 3 处（repl.py L24/L643/L714）明确不改**——输入管道例外（见 §一）
- rich/fallback 双路径全通
- 新增 Step B 测试：tui 插件缺省 → 缝回退到 cli 内置实现（双路径不炸）

**Step C**（重写）：清 E 类 TUI 侧违规作插片哲学演示件；engine E6 不动（§二 R3）。
- **验收**：`cli.display/interface/status` 直接 import 全库清零（7 处改缝后剩余 0），`cli.input_queue` 保留 3 处例外

## 四、commit 拆分（v2 修订）

1. `feat(lacp): capability_seam 支持 required_signers 透传 + tui seam 注册` + 单测
2. `refactor(tui): 插片骨架迁移 + 免签注册` + 5 测试（display/interface/status 三件）
3. `refactor(cli): TUI 渲染 7 处 import 改缝调用（app 2 + repl 3 + repl_io 2）` + Step B 双路径测试
4. `docs: 本提案 v2 修订与 V2 对齐说明`

## 五、明确不做（灵元 1.0 暂缓）

- P2 全屏 TUI（Application+Layout 状态栏常驻）——V2 A1 范畴，独立工期
- prompt 内嵌轮次显示——toolbar 已有，避免信息冗余
