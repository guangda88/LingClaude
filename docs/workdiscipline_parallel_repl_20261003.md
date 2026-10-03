# 并行 REPL 工作纪律（2026-10-03 族长裁决：分批提交）

- 生效: 2026-10-03 20:45
- 权威: 族长指令「网关 REPL 高频并行提交的治理 → 分批提交」
- 适用: 所有在 lingclaude 仓工作的并行会话/REPL/agent

## 纪律三条

1. **分批提交**：相关联的改动按逻辑单元成批提交，避免高频碎提交。背景：今日 16:00–20:41 并行方累计 4 笔独立提交（f1d5327/dc5238b/befe5c2/2a95fef），其中 2 笔恰落在他人推送门禁 pytest 窗口内，造成 5+3 支假红（复判定均单跑绿）与 3 轮无效重推。

2. **门禁窗口期静默**：推送链（pre-push 全量 pytest）运行期间（起跑至 ALL_DONE，约 35-40 分钟）不提交新工作。窗口期查询推送状态：`tail /tmp/push_1003_round*.log | grep -E "exit|ALL_DONE"`。

3. **推送链责任制**：谁发射谁负责。发射前必须三防齐备（flock 互斥 + ls-remote 幂等 + **cwd 锚定与远端 URL 断言**）。标准模板：`/tmp/push_1003_round8.sh`（今日 round7 错仓推送事故后的整改版）。

## 违例处置

窗口期扰动造成的假红由发射方复判定自行消化（今日惯例：单跑复判定，绿则放行）。

## 留痕

- 今日事故链: `data/arch_ledger/incidents/ghost_push_chains_20261003.json`（幽灵链）、`incidents/round7_wrong_cwd_push_20261003.json`（错仓）
- 假红复判定记录: round6/round8 日志 + 会话台账
