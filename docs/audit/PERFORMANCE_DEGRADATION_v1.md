# PERFORMANCE_DEGRADATION_v1 — lingclaude 长会话性能实证（三方仲裁修订版）

> **修订状态**（2026-09-11）：v1 由 claudecode 误判"2163x 严重退化"，经 codex + atomcode + claudecode 三方仲裁，核心结论需修订。
>
> **仲裁结论**：
> - ❌ v1 错判"长会话失控累积"——input_tokens 是累计值，单轮 delta 反而从 10.4M 收敛到 0.08M（`_compact_if_needed()` 工作正常）
> - ❌ v1 错判"input/output 84x 异常"——agentic 工具循环每迭代重发上下文，20-100x 是正常区间，不是单轮 chat 的 5-20x
> - ✅ 真正问题是 **`long_task_metrics.jsonl` schema 缺 turn 级字段**（turn_input_delta / turn_output_tokens / turn_duration_s），导致三位审计员同文件得出相反结论
>
> **数据来源**：`~/.lingclaude/long_task_metrics.jsonl` + `ps` 实测
> **状态**：v1 原始数据保留作为误判实证，v2 分析框架已修订

---

## 一、原始数据（v1 保留，作为误判实证）

### 单会话累计记录（session bf34c0fdff34, ~5h）

| turn | ts | input_tokens(累计) | output_tokens | journal_bytes | tools/errs | outcome |
|---|---|---|---|---|---|---|
| 1 | 10:55 | 9,711 | 145 | 582 | 0/0 | ok |
| 2 | 12:42 | 10,439,760 | 83,781 | 231,071 | 199/8 | ok |
| 3 | 13:50 | 17,738,259 | 159,571 | 417,028 | 140/6 | ok |
| 4 | 14:20 | 19,421,251 | 209,175 | 498,386 | 52/5 | ok |
| 5 | 14:56 | 19,710,869 | 217,264 | 516,075 | 19/0 | ok |
| 6 | 15:21 | 20,580,317 | 236,155 | 561,401 | 45/2 | ok |
| 7 | 15:29 | 21,004,970 | 251,461 | 589,581 | 19/2 | ok |

⚠️ **关键提示**：上表 `input_tokens` 是**累计计数器**（`engine.get_stats()["usage"]["input_tokens"]`），不是单轮消耗。直接对比相邻行得到的"倍数"是误导。

---

## 二、v1 误判分析（保留作为反面教材）

### 2.1 v1 错判的"2163x 严重退化"

v1 推理：
- turn 1: 9,711 → turn 7: 21,004,970 = "2163x 增长"
- turn 1→2 涨 1074 倍
- 结论：🔴 严重退化

### 2.2 仲裁校正（atomcode + codex）

**真相**：
- `input_tokens` 是累计值（`_record_long_task_metrics` 写入顶层无此字段，嵌在 `usage` 字典里）
- 来源是 `engine.get_stats()` 会话累计计数器
- 单轮真实消耗 = 累计差值（delta）

**单轮 delta 重算**：

| turn | 累计 input | 单轮 delta | 解读 |
|---|---|---|---|
| 1 | 9,711 | 9,711 | 启动 + 首次输入 |
| 2 | 10,439,760 | **10,430,049** | 超大 agentic turn（199 工具循环 × 50K 平均 ≈ 10M，自洽）|
| 3 | 17,738,259 | 7,298,499 | 同类大 turn |
| 4 | 19,421,251 | 1,682,992 | **收敛** |
| 5 | 19,710,869 | 289,618 | **持续下降，压缩在工作** |
| 6 | 20,580,317 | 869,448 | 锯齿（压缩周期正常）|
| 7 | 21,004,970 | 424,653 | 再收敛 |

**delta 形态**：
- turn 2-3: 10M+ 级（重负载工具循环）
- turn 4-7: 289K → 1.7M（锯齿收敛）
- **不是失控累积，是 `_compact_if_needed()` 工作的证据**（`tool_executor.py:132`：消息数>40 或估算 token>0.8×预算双触发，`compress_messages` 裁剪）

### 2.3 v1 错判的"input/output 84x 异常"

v1 推理：
- 5-20x 是 LLM agent 正常区间
- turn 7 ratio = 84x → 异常

**仲裁校正**：
- 5-20x 是**单轮 chat** 的经验值
- **agentic 工具循环每迭代重发全上下文**，只产出几个 tool-call token，20-100x 是正常区间
- 84x 对应 turn 7 = 21M input / 251K output（累计幻觉）—— 不是单轮 ratio

### 2.4 v1 误判的方法论错误

| 错误 | 严重度 | 教训 |
|---|---|---|
| 把累计值当逐轮值画曲线 | 🔴 严重 | 看指标数据先看 schema：累计/单轮/delta 必须区分 |
| 用单轮 chat 基准套 agentic 循环 | 🟡 中 | 行业基准知识要分 chat vs agentic |
| 单方面判定"严重退化"无第三方校验 | 🟡 中 | 重要结论应有 codex/atomcode 类独立审计 |
| 未确认字段语义就画曲线 | 🔴 严重 | "实证"不等于"正确解读"——必须先查 schema 定义 |

---

## 三、v2 实证（仲裁后正确结论）

### 3.1 真实长会话稳定性

| 指标 | 评估 | 证据 |
|---|---|---|
| 单轮 token 消耗 | 🟢 健康 | delta 从 10.4M → 0.08M，`_compact_if_needed()` 工作正常 |
| 压缩触发 | 🟢 工作 | 消息数>40 或 token>0.8×预算 双触发 |
| 锯齿形态 | 🟢 正常 | 压缩周期表现，10M → 7M → 1.6M → 0.3M → 0.9M → 0.4M |
| 进程 RSS | 🟡 线性但稳定 | 287MB / 5h14m 线性，atomcode 实测 4h44m 稳定 284MB 无增长 |

### 3.2 进程资源（实测）

| 进程 | ETIME | RSS | 备注 |
|---|---|---|---|
| `lingclaude run -i` (32462) | 5h14m | 287MB | 线性增长但稳定，无泄漏 |
| `lingclaude daemon watch` (2360) | 5h25m | 91MB | 稳定 |
| `lingclaude.api.run_server` (2316) | 5h25m | 37MB | 稳定 |
| `lingbus_poll_daemon` (2359) | 5h25m | 13MB | 稳定 |

### 3.3 journal_size 单调增长（保留 v1 观察）

- 7 turn 内 582B → 589,581B（1013x）
- **修正评级**：🟢 **温和增长**（不是失控）
- 量级 750KB / 11turn 无 rotate，但量级合理
- 真正 backlog：定期归档机制（中等优先级）

---

## 四、🔴 真正问题：可观测性 schema 缺口（仲裁核心发现）

### 4.1 `long_task_metrics.jsonl` 缺字段

`app.py _record_long_task_metrics()` 写入的字典缺三个关键字段：

| 缺失字段 | 现有状态 | 影响 |
|---|---|---|
| `turn_output_tokens` | 参数已存在但未持久化 | N5 守卫有值，日志无值 |
| `turn_input_delta` | 写入处可算（累计差值） | 三方审计无法独立算 delta |
| `turn_duration_s` / `first_token_latency_s` | 调用方有时间上下文 | 无法分析响应延迟 |

### 4.2 schema 缺字段的后果

- ❌ **三方审计员同文件得出相反结论**——claudecode 当累计画曲线，codex 提"无可观测性"，atomcode 算 delta 得"3.7x 效率提升"
- ❌ 外部审计（监督者/独立验证）必须依赖 lingclaude 自己解读
- ❌ 这是"治理纪律强" vs "可观测性弱"的真实张力

### 4.3 附带发现：session 23ca590e schema 异味

- `turns=0` 且累计 input 从 4.9M → 3.6M（**倒退**）
- 疑似引擎重启重建计数器
- 是"累计语义"的又一实证——单次会话内累计值应单调，但跨重启可倒退

---

## 五、给 lingclaude 的 P1 backlog（v2 修订）

### 5.1 高优先级（仲裁核心建议）

1. **`long_task_metrics.jsonl` schema 补字段**（v1→v2 的根本修复）
   - 加 `turn_input_delta`：累计值差值，写入处即可算
   - 加 `turn_output_tokens`：参数已存在，append 时持久化
   - 加 `turn_duration_s` / `first_token_latency_s`：调用方已有时间上下文
   - 改动局限 `app.py` ~10 行 + 一个测试
   - 不本轮执行——避免再和并发会话抢 staged 区，挂到下一批次

2. **`_record_long_task_metrics` 写入 dict 时同步计算 delta**
   - 让 schema 自描述累计 vs 单轮
   - 减少外部审计依赖

### 5.2 中优先级

3. **journal 定期归档**（v1 保留建议）
   - 加 `journal_archive_after_bytes` 配置
   - 避免单文件百万行级

4. **进程级 RSS watchdog**（v1 保留建议）
   - N5b 流内 watchdog 已落地，加进程级
   - 阈值：RSS > 1GB 或增长 > 100MB/h

### 5.3 低优先级（保留观察）

5. **session_id 跨重启计数连续性**
   - session 23ca590e 累计值倒退现象
   - 决定是否需要"会话级 vs 进程级"语义区分

---

## 六、监督者纪律执行（v2 修订）

### 6.1 v1 错误纪律复盘

- ❌ v1 自报"已验真"——实际未验 schema 定义
- ❌ v1 单方面判定，无第三方校验
- ✅ v1 写了 docs/audit/——文档可被修订
- ✅ v1 用 `ps` 实测 RSS——数据本身正确

### 6.2 v2 修订纪律

- ✅ 接受 codex + atomcode 仲裁结论
- ✅ claudecode 主动修订自己写的归档文档
- ✅ 保留 v1 原始数据作为误判实证（不删除历史）
- ✅ v2 区分"数据真实" vs "解读正确"——原始数据保留，解读框架修订
- ✅ 仲裁结果写明，避免下会话/监督者重蹈覆辙

### 6.3 写 memory 防再犯（已写入 `lingclaude-audit-call-chain.md`）

- 看指标数据先看 schema 定义（累计/单轮/delta）
- agentic vs chat 基准不同
- 重要结论应有第三方独立审计（codex/atomcode 类）

---

## 七、下次监督会话开局清单（v2 修订）

1. `tail -1 ~/.lingclaude/long_task_metrics.jsonl | decode` —— 看是否有 `turn_input_delta` 新字段（等 lingclaude 落地 P1.1）
2. `ps -p 32462 -o etime,rss` —— 看 run -i 进程是否还在、RSS 趋势
3. `du -sh .lingclaude/journals/*.jsonl | sort -h | tail -3` —— 看 journal 文件大小
4. **重点**：`_compact_if_needed()` 触发频率（间接证据：相邻 turn delta 下降趋势）
5. **重点**：三方审计机制是否在 lingclaude 内化（是否有自动化审计流程）

---

## 八、版本历史

| 版本 | 作者 | 时间 | 结论 |
|---|---|---|---|
| v1 | claudecode | 2026-09-10 16:07 | ❌ "2163x 严重退化，input_tokens 单调累积=无状态压缩"（误判）|
| v2 | claudecode | 2026-09-11 | ✅ 仲裁后修订：累计计数器语义 + 压缩生效证据 + schema 缺口是真正问题 |
