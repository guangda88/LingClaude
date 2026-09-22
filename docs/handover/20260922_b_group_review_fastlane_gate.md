# B 组复核归因 + fast lane 策略门禁（2026-09-22 会话）

> 执行：灵克（lingclaude）· 上游：docs/handover/20260922_p1_p2_batch_handover.md 待办 2/3

## 一、B 组 12F 复核结论（终局回归 13F，任务 #2）

方法：逐项单跑对照（arch_debt regression-preexisting-failures-20260921 resolve_hint 指定路径）+ 相关测试文件 git log 断代。

| 归因 | 项 | 单跑结果 |
|------|----|---------|
| 环境敏感型（单跑绿） | l7_hook×2、t0_wiring×2、tool_plugins×3、lc_mcp_guard | 10/10 passed |
| 真实 F·既有债务 | daemon::test_run_cycle_no_trigger | 断言未随 P0-N5 更新（81819b5 前的断言），非本批引入 |
| 真实 F·既有债务 | gray_zone::test_ask_bash_escalated | H2 escalate 落地后断言漂移，非本批引入 |
| 真实 F·既有债务 | mcp_proxy::test_find_tool_across_servers | f84b4e8（安全审计批）后断言漂移，非本批引入 |
| 真实 F·既有债务 | security_sandbox_fixes::p11 killpg | pgrep 探到残留 sleep，环境+断言双重因素 |

**结论**：13F 全部为既有债务或环境敏感，无一本批 10 commits 引入。4 项真实 F 属测试断言随功能演进未同步，追加进 regression-preexisting-failures-20260921 台账（due 2026-10-05 不变）。

## 二、fast lane 默认开启评估（任务 #3）

实测（本机探针，5 prompt × 2 配置）：
- fast lane 开：avg 0.221s（首调 0.790s 含 import）；关：avg 0.153s（首调 0.454s）
- 开销 = 一次性 import + fast_route 本地判定，约 +70ms/首调，稳态持平
- 收益：OTHER 盲区修正（domain 映射）+ hard 提升 analysis 路由

**裁定**：数据支持默认开，但按交接文档要求需实机会话验证。落地为策略文件门禁（见下），默认 false，实机验证后翻 true 即可（改 yaml 不动代码，热更生效）。

## 三、fast lane 策略文件门禁（任务 #5，commit 本笔）

- `fan_out_questions.yaml` 新增 `fast_lane_enabled: false`（每次 resolve 实时读，PolicyLoader mtime watch，读失败视为关——fail-closed）
- `task_router.py resolve()` 门禁双通道：env `LINGCLAUDE_LAYA_FAST_LANE=1` 强制开（最高优先级）> 策略文件热开关
- 探针验证三场景：off 不引入 fast_lane 模块 / env=1 强制开（模块被加载）/ policy_get 抛异常 fail-closed
- 回归：test_task_router + 铁律守卫 30 passed
