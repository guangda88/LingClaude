# 报修单：灵信（lingmessage）MCP 工具卡片 ↔ 服务端参数漂移

- 单号：D3-灵研灵信schema报修（台账 `data/arch_ledger/tech_debt/20261008_wipe_case_debts.jsonl`）
- 日期：2026-10-09 ｜ 提出方：灵克（lingclaude，消费方） ｜ 受理方：灵信（lingmessage，服务方）、灵犀（lingxi，外层挂载方）
- 性质：外层工具卡片 schema 与服务端实参错位，重启不治，属挂载层持久漂移

## 一、活体取证（2026-10-09 实测）

| 工具 | 外层卡片 schema | 服务端实参 | 症状 |
|---|---|---|---|
| `add_intel` | `{}`（无参数） | 3 个必填参数 | 无参调用报错「missing required positional argument」，卡片上看不到也不可填 |
| `poll_messages` | 未含 `recipient` | 必填 `recipient` | 按卡片参数调用报「missing recipient」 |
| `get_stats` | 卡片声明返回统计 | 返回 `Unknown` | 无法用于健康探测 |
| `ask_question` | 正常 | 正常 | ✅ 对照组：挂载机制本身可用 |

## 二、请求（按优先级）

1. **P0**：核对 `add_intel` 服务端签名，外层卡片补齐参数 schema（或服务端改为可选参+默认值）；
2. **P1**：`poll_messages` 卡片补 `recipient` 字段声明；
3. **P2**：`get_stats` 返回值修复（或明确声明废弃）；
4. **协作模式建议**：服务方在 `data/contract/` 侧发布机器可读 schema 契约文件（当前 lingclaude 仓内未找到 lingmessage schema 工件），消费方以契约测试对齐（P15 语法）。

## 三、验收口径

- 灵字辈任意 agent 以「仅按卡片 schema 构参」方式成功调用 `add_intel`/`poll_messages` 各一次；
- `get_stats` 返回非 `Unknown` 的统计结构，或卡片标注 deprecated。

## 四、台账

- 状态：resolved（报修单落盘即视为「提报」动作完成；修复责任在灵信/灵犀侧，复发则凭本单 reopen）
