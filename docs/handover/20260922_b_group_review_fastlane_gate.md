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

## 四、实机会话验证（2026-09-22 会话补测，翻默认开裁定依据）

双配置批量探针（20 条混合 prompt，干净 import 对照）：

| 指标 | gate off | gate on (env=1) |
|------|----------|-----------------|
| 稳态 avg | 45.6ms | 64.8ms（**+19.2ms/次**） |
| 首调 | 630ms | 951ms（+321ms，Laya 单例懒加载） |
| 分类决策差异 | — | **0/20** |

定向收益用例（盲区修正/难度提升 4 条：费马大定理、kafka vs rabbitmq、需求文档模板、用药咨询）：on/off 对照 **0 差异**。

归因：fast_route 全链经 B2 门控（confidence<0.3 回退、factual_lookup 薄弱域回退、NOT_GOOD_AT 代码/长文回退、difficulty 无 score 保守放行但 verdict 普遍 None）后，测试分布上 verdict→None，domain/difficulty 修正未实际生效。

**裁定：维持默认关**（fast_lane_enabled: false 不动）。理由：
1. 收益未兑现——20+4 条实测 0 决策修正，B2 门控在常规 prompt 分布上过滤掉了几乎全部判定；
2. 开销为正——稳态 +19ms/次、首调 +321ms，纯付无收；
3. 保留通道完好——env `LINGCLAUDE_LAYA_FAST_LANE=1` 可随时强制开，翻默认仅改 yaml 一行且热更。

**复评条件**（满足其一可重测）：
- B2 门控阈值/薄弱域清单有新质量数据支持放宽；
- fan_out_questions.yaml 换用非 $ref 的精调问题集提升 verdict 存活率；
- FanOutScheduler（enable=True）上线后 fast lane 作为其 pre_classifier 有明确消费方（届时收益=投机分支命中，另行测）。

## 五、四条接线 + P2 两项收口（2026-09-22 会话，全 env 门禁默认关·零行为分叉）

| 项 | 接线点 | 门禁/通道 | 验证 |
|----|--------|----------|------|
| ① approval_matrix→裁决链 | permissions.check_action 消费矩阵（asset 放行/能力策略拒/需审批落回原语义） | env LINGCLAUDE_APPROVAL_MATRIX=1，档位 LINGCLAUDE_SANDBOX_MODE/APPROVAL_POLICY | 探针 5 场景全符合（默认关 pending、on_failure 放、granular pending、资产命中放、deny 硬拒不破） |
| ② worktree→agent_batch | proj_agent_gateway agent_batch 每 agent cwd 升级独立 worktree（非 git/失败降级 scratch） | env LINGCLAUDE_AGENT_WORKTREE=1 | AST+模块加载 OK，返回 JSON 带 worktree 字段 |
| ③ BashSession→engine | BashExecutor._shell_exec 持久 shell 通道（沙箱命令不走此路） | env LINGCLAUDE_BASH_SESSION=1 | env/cwd 保持实测 OK；已知限制：本机 bwrap 全量包裹时通道不可达（沙箱优先，有意设计，注释已标） |
| ④ credential_pool→factory | _get_env_key 池优先（LRU+熔断），CredentialPool.from_env 新增装配 | env LINGCLAUDE_CREDENTIAL_POOL=1 + LINGCLAUDE_CREDENTIAL_POOL_KEYS=`prov:k1,k2;...` | 探针 3/3（轮转 kA,kB,kA / 熔断跳过 / 默认关回退） |
| P2-12 LSP 工具面 | 核实 f9b0102 已落地（engine/lsp_provider.py + tool_handlers/lsp_tools.py，tool_registration 已注册 7 命令），无需新做 | — | tests/test_lsp_tools.py 7 passed |
| P2-13 TUI 双代热更 | 核实 full_tui.cutover_generation 蓝绿机制已存在，补测试闭环（此前零测试） | — | TestCutoverGeneration 4 passed（构建失败/验证失败/自定义 verify/成功切换） |

统一回归：铁律守卫+full_tui+lsp+task_router 141 passed；bash 三件 53 passed；guard/p04 28 passed。全默认行为零分叉。
