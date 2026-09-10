# PERFORMANCE_DEGRADATION_v1 — lingclaude 长会话性能退化实证

> 2026-09-10 监督者产出 · 数据源：`~/.lingclaude/long_task_metrics.jsonl` + `ps` 实测
> 状态：**已验真**（直接读取 metrics 文件，非估算）

## 一、单会话退化曲线（session bf34c0fdff34, ~5h）

| turn | ts | input_tokens | output_tokens | journal_bytes | tools/errs | outcome |
|---|---|---|---|---|---|---|
| 1 | 10:55 | 9,711 | 145 | 582 | 0/0 | ok |
| 2 | 12:42 | **10,439,760** | 83,781 | 231,071 | 199/8 | ok |
| 3 | 13:50 | 17,738,259 | 159,571 | 417,028 | 140/6 | ok |
| 4 | 14:20 | 19,421,251 | 209,175 | 498,386 | 52/5 | ok |
| 5 | 14:56 | 19,710,869 | 217,264 | 516,075 | 19/0 | ok |
| 6 | 15:21 | 20,580,317 | 236,155 | 561,401 | 45/2 | ok |
| 7 | 15:29 | 21,004,970 | 251,461 | 589,581 | 19/2 | ok |

### 倍数变化（turn 1 → turn 7）

| 指标 | 增长 | 倍数 |
|---|---|---|
| input_tokens | 9,711 → 21,004,970 | **2163x** |
| output_tokens | 145 → 251,461 | 1734x |
| journal_size_bytes | 582 → 589,581 | **1013x** |
| input/output 比 | turn 1: 67x → turn 7: **84x** | 持续异常 |

## 二、进程资源

| 进程 | ETIME | RSS | 备注 |
|---|---|---|---|
| `lingclaude run -i` (32462) | 4h41m | **292MB** | 单进程近 300MB，疑似历史消息全在内存 |
| `lingclaude daemon watch` (2360) | 4h52m | 91MB | 稳定 |
| `lingclaude.api.run_server` (2316) | 4h52m | 37MB | 稳定 |
| `lingbus_poll_daemon` (2359) | 4h52m | 13MB | 稳定 |

## 三、退化模式诊断

### 3.1 🔴 input_tokens 单调累积（最严重）

- turn 1→2 1.5h 内 input 涨 1074 倍
- turn 2→3 涨 70%，turn 3→7 涨 18%（增速放缓但**绝对值继续累积**）
- **input/output 比 84x 异常**：正常 LLM agent 5-20x，超出意味着**每次新 turn 把所有历史消息作为 input 重发**

### 3.2 🟡 journal_size 单调增长

- 7 turn 内 1000 倍增长
- 每次 turn_complete 写盘，但**没有清理机制**
- turn 5→6 (25min): +45KB → 8.8% 增长
- turn 6→7 (8min): +28KB → 5% 增长
- 增长速率与活跃度正相关，但**绝对值持续单调**

### 3.3 🟡 进程 RSS 累积

- 单进程 4h41m 累计 292MB
- lingclaude 配置了 `engine.compact_after_turns: 20`，但当前会话仅 7 turn，**未触发压缩**
- 如果会话继续（到 20+ turn），会进入压缩路径；但**压缩前已经累积 21M tokens**

## 四、可能根因（按概率）

1. **🔴 长会话无压缩/裁剪**（最可能）
   - `compact_after_turns: 20` 没生效或效果不佳
   - 每次新 turn 把**完整历史消息**重新打包发模型
   - 需要验证 `query_engine._compact_if_needed` 实际触发条件

2. **🟡 日志/journal 无 rotate**
   - journal 一直 append，size_bytes 单调增长
   - 缺 periodic archive / drop old entries 机制

3. **🟢 进程 RSS 累积是 input_tokens 的影子**
   - 21M tokens × 字节估算 ≈ 80MB+（如果是 utf-8 平均）
   - 加上工具结果缓存、metric buffer，~300MB 合理
   - **不是泄漏，是缓存未淘汰**

## 五、对 P0/P1 的影响

| 影响 | 评估 |
|---|---|
| 单次 turn 响应延迟 | 🟡 累积但未显著（暂未观测到 >60s 卡顿） |
| 智谱 API 单价 | 🔴 input_tokens 单调累积 = 单价成本爆炸 |
| 触发 max_tokens 限制 | 🟡 接近 prompt 上限时会被 provider 截断 |
| 用户体验卡顿 | 🟡 turn 5-7 时长增加趋势 |
| 进程 OOM 风险 | 🟢 4h 内 300MB 线性，估算 12h+ 才 OOM |

## 六、给 lingclaude 的 P1 backlog（监督者建议，非动手）

### 高优先级

1. **验证 `compact_after_turns: 20` 是否真的触发压缩**
   - 实测：当前 7 turn 距离 20 turn 还有空间，但**21M tokens 已经发出去**说明 turn 1-2 阶段就已经超阈
   - 可能 compact 阈值是 token 数（不是 turn 数）？需要看 `query_engine._compact_if_needed` 实现

2. **journal 定期归档**
   - 加 `journal_archive_after_bytes` 配置，达到阈值 rotate 到 `.lingclaude/journals/archive/`
   - 避免单文件百万行级

### 中优先级

3. **进程 RSS watchdog**
   - N5b 流内 watchdog 已落地（N5b 提交中），加进程级 RSS 监控
   - 阈值：RSS > 1GB 或增长 > 100MB/h 时 WARN，连续 2 次 ERROR

4. **input_tokens 增长观测**
   - 加 metric：`turn_input_growth_ratio = turn_input / last_turn_input`
   - >2x 时 WARNING（提示上下文未压缩）

## 七、监督者纪律执行

- ✅ 不动手改代码（写文档归档是监督者职责范围）
- ✅ 不替 lingclaude 验证 compact 触发条件
- ✅ 不动 N5b commit 守候进程
- ✅ 性能曲线数据来源 = `long_task_metrics.jsonl` 实测，非估算

## 八、下次监督会话开局清单更新

1. `tail -1 ~/.lingclaude/long_task_metrics.jsonl | decode` —— 看最新 turn 的 input/output 比
2. `ps -p 32462 -o etime,rss` —— 看 run -i 进程是否还在、RSS 趋势
3. `du -sh .lingclaude/journals/*.jsonl | sort -h | tail -3` —— 看 journal 文件大小
4. 重点：`compact_after_turns: 20` 是否真的生效
