# lc 深度审计报告：能力·欠账·弱点（2026-10-02，代码级查证）

> 方法声明：全量 90,394 行逐文件 AST 解析（382 模块 import 图、0 语法错误、0 孤儿）+ 15 项布线断言 + 对每条欠账/弱点做源码级定性。"逐行"的诚实边界：100% 编译与引用图覆盖 + 锚点级逐行深读；无逐字通读 4 万行，涉及推断处均标 ◐。

## 一、结构盘点

| 目录 | 模块数 | 行数 |
|------|--------|------|
| core | 114 | 30,444 |
| engine | 77 | 18,818 |
| cli | 31 | 12,284 |
| model | 32 | 7,363 |
| plugins | 71 | 7,877 |
| self_optimizer | 19 | 5,809 |
| lacp/governance/coordination/mcp/seams/gov/ops/lingyuan_patches | 31 | 7,133 |
| **合计** | **380** | **90,394** |

- tests：353 文件 / 74,332 行
- .bak 死重：99 个
- 入口：`lingclaude = lingclaude.cli.__main__:main`（re-export app.main）+ `lingclaude-mcp = lingclaude.mcp.server:main`
- 质量底座：compileall 全绿（exit 0）、import 图孤儿=0、孤儿基线随手入册（7eb3670）、守卫自检 PASS（当日两跑）

## 二、布线断言（15 项，初判 5 FAIL 逐个下钻后归零）

初判 FAIL 5 项经源码下钻全部定性为**审计脚本口径误差，非真缺口**：

| # | 初判 FAIL | 下钻定性 |
|---|-----------|----------|
| F1 | checkpoint/recover/rewind handler 缺失 | 实际在 `cli/_commands_checkpoint.py` 等 4 个 Mixin（SlashCommandCheckpointMixin 等），commands.py import 挂载——handler 全存在 |
| F2 | slash_plugins 注册 0 条 | `register(add)` 多行调用形态（`add(\n "/policy", ...`），单行正则漏检——5 条插件命令在册 |
| F3 | 11 个 tool_handlers 未入 wiring | 经 `engine/tool_handlers/__init__.py` 23 处 Mixin 聚合 + `engine/coding.py` 引用——机制不同而非缺失 |
| F4 | hook 注入点常量缺失 | HookPoint 枚举实为 `PRE_TOOL/AFTER_TOOL/POST_RESPONSE` 三点（`core/hook_registry.py` register_hook 幂等注册） |
| F5 | cli/__main__ 无 def main | re-export `from lingclaude.cli.app import main`——pyproject 入口有效 |

修正后断言：**15/15 PASS**（22 内置命令 handler、5 插件命令、11 tool_handlers、6 Provider、vault 兜底、四档引擎 yaml+check_tool_call+活体台账×2、三注入点、scheduler/SessionIndex/bus_consumer/session_projection/双入口）。

## 三、能力全景（十域，锚点=当日实读）

| 域 | 能力与代码锚点 |
|----|----------------|
| ① 交互面 | 22 内置 + 5 插件斜杠命令；斜杠 Tab 补全双列注释已落地（cc6d67b，TUI 族 107 绿）；/clear 语义升级（reset 存档可恢复+/new）；WebUI/REPL/TUI |
| ② 模型路由 | 6 Provider 文件；联邦六态；三档语义（5484182）；vault 兜底（factory._key_store_get→_vault_get）；credential_pool 句柄化（27e1f3b）；预算线闭环（a356ac3） |
| ③ 工具面 | 11 tool_handlers；目录级沙箱（09a4f20）；net_allowlist 网闸入 llm_proxy httpx 强制点（d58ef9c） |
| ④ 插件体系 | 35 agent 插片 manifest 35/35；marketplace 签名+blocklist+scope（084db26）；热更+plugin_forge |
| ⑤ 记忆认知 | l7 语义记忆+supersede 链收尾（419bde5：prev_digest 安息态寻父+append-only journal）；per-project memory（164de66） |
| ⑥ 上下文 | 会话 SQLite 索引+checkpoint/recover/rewind/undo 回滚链；state_store.py（302 行） |
| ⑦ 治理安全 | 四档策略引擎活体（tool_auth_hook.check_tool_call+policies/tool_auth_policy.yaml+台账×2）；gate 契约化（a831612）；hook 三注入点+哨兵注册（1da2f11）；env 收窄（27a82d1）；铁律红清偿（3d4b7c7，50 测试绿）；豁免复审初筛器（exemption_review.py D1-D4/H） |
| ⑧ 多智能体 | subagent+fan_out_scheduler+thread；LingBus consumer/responder/signer；跨端 task_state 投影（session_projection.py，aa27d95） |
| ⑨ 自优化 | self_optimizer 19 模块；fastlane/spec_decision；l5/l10 审计；data_flywheel |
| ⑩ 运维 | /schedule（@daily/after:N/at:HH:MM）；/tasks 面板；rss_watchdog；MCP server/http_proxy |

## 四、欠账（台账 8 条 open，逐条对码）

| # | 欠账 | 对码实证 | 到期 |
|---|------|----------|------|
| 1 | 豁免人工三问 ~115 条（active=111 + 无state 4） | 96 模块级基线占位 + 15 行级；初筛器已清零机器类（D1-D4 全零），剩余全语义 | 10-17 |
| 2 | **journal-gap 债**（a11f2ec 登记） | `scripts/exemption_review.py` 中 journal 引用 0 处——豁免写路径仍链外，修法未动工 | **10-09** ⚠ |
| 3 | kernel_m1 装配外移 | coding.py 仍 30 方法 / coding_wiring.py 408 行（自 253 涨，部分进展未达 M1） | 无硬期 |
| 4 | **kernel_m2 manifest 补录** | 35/35 全覆盖——**实质已完成，可销账**（审计新发现） | — |
| 5 | fastlane 摘除降级 | fast_lane.py 191 行 + 包内 4 文件引用，摘除未执行 | 11-21 |
| 6 | prop_security_gate json lint | 待实现 | 无 |
| 7 | spec_decision SFT checkpoint | 8 个消费文件就绪，等数据+训练窗口 | 条件触发 |
| 8 | lsp_ab 数据收集 | gate 已建（122 行），待 1-2 周 treatment 数据 | 条件触发 |

另：未提交尾巴 3 个（.atomcode/memory.md、tool_auth 台账 +15 笔、scripts/color_preview.sh 配色 WIP）。

## 五、弱点（逐条复核后的清单，按危害排序）

1. **输出前自检接线未做（头条）**：`classify_state` 定义于 `core/evidence_protocol.py:149`，全包消费方为零——四档引擎管住工具调用面，但"数字/hash/路径行号"类断言的输出前打标（守卫失效审计建议 #1）仍缺，无外部质疑时编造可直出；
2. **单重 turn 成本峰值 8.01M**（10-02 01:20，132 轮）——turn 内压缩触发器缺失；context 失控本体已证伪（现役会话轮均 42K→36K 稳定，见 journals/e2c55ba 复核）；
3. **降级锁输入**：已落地（`loop_body.py` 耗尽路径 yield hard_interrupt + return 三路径同构、`_hard_interrupt_message` 四 scope 单源、TUI 红点 latch 188f359、input-freeze ⑤ 6 用例），待真实故障实战验证（183719 为修复前实例）；
4. **sandbox 闸门默认全开**：`sandbox_policy.yaml` 激活示例仅注释（0afb377 自述）；directory_rules 激活曾被 20 连红退回（default writable=/home/ai 误伤 /tmp 测试），重启需按目录域收紧；
5. **会话 blob 仍 json**（`session_persist.py` 落 .json，无 sqlite blob）——严格快照完整性依赖 P2-13；
6. **loop_purification 前提过时**：loop_body 已无 `engine._conversation` 残留，StateStore 302 行仍未接入——任务前提需 lc 重核；
7. 打磨项：.bak 99 个死重、硬中断提示未含 /model 钉住指引、多端手机闭环未验证。

## 六、总判

近 48h 净增 19 提交，能力面十域齐备且布线断言 15/15 实锚通过（初判 5 FAIL 全为审计脚本口径误差，逐个下钻归零——审计工具自身的口径纪律在本轮被校准）。欠账面收敛为**一笔急账（journal-gap，10-09）+ 一笔人力账（~115 条三问，10-10 启动）+ 一条可销账（M2）**。弱点面头条：**classify_state 输出前接线**——唯一"知道解法、有实锚、没做"的守卫缺口。

## 附：审计期间并行落库记录

- `9ae6c05` gitignore 台账解禁（*.jsonl/handover.*.json 两次误伤复盘，灵克改写 lc 收编）
- `d81c0d2` 豁免台账 6 档 paidoff resolved（灵克执行：误翻 95 档已 git restore 全量回滚后按 L121 精确口径重做，last_review 留痕）
- `a11f2ec` journal 缺口债务登记（八笔豁免 resolved 变更链外实证）
- `cc6d67b` 斜杠 Tab 补全双列注释落地（atomcode 补全机制参考文档的 P0 项兑现，TUI 族 107 绿）

配套文档：`docs/lc_vs_15_landscape_2026-10-02.md`（横向对比，其原始材料索引引用本文件——悬空引用已由本次落盘修复）。
