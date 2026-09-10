# HANDOFF_LINGKE_20260911 — 灵克会话移交（P3.2 恢复 → N6 → 三方仲裁 → P1.1/P1.2）

> 移交时点快照：HEAD=`e26f764`，staged=0，两个守望 job 在跑。
> 新会话开场第一条指令：`job_status 2aaf52e70dec`（收割 e26f764 的 N1 复核终态）。

## 一、状态快照

| 项 | 值 |
|----|-----|
| HEAD | `e26f764` feat(observability): P1.1/P1.2 metrics schema 补齐 turn 级字段 |
| 前序关键提交 | `b7300fe`(StateStore,并发会话) / `c6227ad`(N5a+N5b 完整批次) / `dfaab65`(N5+N1) / `97c3e82`(P3.2 双写) |
| staged | 空 |
| 工作树残留（4 项，归属明确） | ① `PERFORMANCE_DEGRADATION_v1.md` 已是三方仲裁修订版（P1.3 已完成，待提交）② `ROUTING_TOPOLOGY_v1.md` §七 N6 观测补齐补记（我写的，待提交）③ `tests/temp_test_files/test.txt` 删除（测试残留清理）④ `workspace/better-harness` 子模块指针（并发会话领域，勿动） |

## 二、守望 job（独立进程，跨会话存活）

| job_id | 盯什么 | 收割方式 |
|--------|--------|----------|
| `2aaf52e70dec` | `e26f764` 的 N1 全量复核终态 | `job_status 2aaf52e70dec`；exit 0=终态 JSON+日志尾；exit 2=疑似 OOM（看日志尾确认，本沙箱 OOM 假阴性高发）；PASS→P1.1/P1.2 彻底闭环 |
| `ca7217e743d9` | `b7300fe` 旧复核（可能已被 OOM 杀过一轮） | 同上；结果出来顺带收割即可，不阻塞任何事 |

## 三、下一步工作清单（按优先级）

1. **收割两个守望 job** → PASS 则观测线闭环
2. **提交工作树两份文档修订**（顺手带 test.txt 清理）：`docs: P1.3 仲裁修订 + N6 §七补记`——建议等守望 job 收割后一起，避免抢复核窗口
3. **P3.3 核心三件**：layered_memory → memory_engine → l7_cognitive（动刀前先读 `docs/P3_STATE_MIGRATION_MAP.md` §二难度分级；state_store.py 有并发会话的 unstaged 改动，先协调）
4. **协调项**（需并发会话/用户）：`HANDOFF_SUPERVISOR_20260910.md` §七 N1/N5 标注；LingBus readonly db 排查（直连可写、经 LingBus 报 readonly，疑打开路径环境差异）
5. **codex P2 backlog**：LingBus 反向确认通道；`_streaks` 内存淘汰（长跑边界）

## 四、环境坑速查表（全部实证，勿重蹈）

| 坑 | 正确姿势 |
|----|----------|
| 前台 bash 是 bwrap 包装，`/dev/urandom` EACCES | **git 操作一律走 `run_in_background`**（裸进程 urandom 直读正常，urandom_shim.so 垫片已可退役） |
| 前台 bash 120s 硬超时 | 任何 >2 分钟的命令走 `run_in_background` |
| 全量 pytest 常态 750s+，且沙箱 OOM 假阴性高发（exit 137 / can't start new thread / execnet MemoryError） | 复杂测试类粒度拆跑；**一切"全量 PASS"结论必须核对日志尾部**；提交走 `LING_AUDIT_FAST=1` + N1 异步复核兜底（多会话并发跑全量复核会 OOM，需错峰） |
| edit 工具对含 import 的替换片段做 verify-pre 编译误报 | 复杂编辑改用 python 断言唯一命中 + `ast.parse` 自验 |
| 钩子链路 | pre-commit 快子集门禁（`LING_AUDIT_SCOPE=fast`）→ post-commit 拉起 `exempt_review.py --watch`（marker=`/tmp/lingclaude_exempt_review/`，存在=未终态）；审计 JSON 含 `pytest_output_tail` 字段（诊断盲区已修） |
| 根 FS 只读 | 钩子写 `~/.ling-audit` 会 Errno 30，export `LING_AUDIT_DIR=/tmp/lingclaude_audit`（签名钥已迁移，签名链连续） |

## 五、多会话协作纪律（本会话踩出来的）

- staged 区是共享资源：`git add` 后立即 commit，超时命令里的 add 会"僵尸入 index"被并发提交顺走（b7300fe 混批事件）
- commit 前先 `job_status` 检查自己挂的自动 commit 守望，避免双 commit 竞争
- 文档在别人手里就不代改（PERF 文档 claudecode 已自行修订为仲裁版，P1.3 就地闭环）
- 审计员分歧时先查 schema 语义（累计 vs 逐轮 vs delta）再下结论——三方审计仲裁流程本身是 lingclaude 的结构性优势，继续用

## 六、当前任务语义备忘（防新会话重新踩坑）

- `long_task_metrics.jsonl` 的 `usage` 字段是**累计值**；turn 级真相在 `turn_input_delta` / `turn_output_tokens` / `turn_duration_s`（e26f764 起才有）
- agentic 工具循环 input/output 比 20-100x 是正常区间；delta 锯齿是 `_compact_if_needed()` 压缩周期的正常形态
- N5a 守卫读的是 done 事件的**单轮** usage（c6227ad 起 model_call.py 三处 done 均注入）；N5b watchdog 首事件 120s / 事件间隙 60s→300s；N6 RSS 基线增长 500MB WARN / 绝对 1000MB ERROR
- l7 两测失败=并发会话领域（d8cd9f7 三重取证），非观测线回归
