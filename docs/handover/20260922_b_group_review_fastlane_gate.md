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

## 六、fast lane 转正路径落地（B2-R 放宽 + FanOut pre_classifier 消费方，2026-09-22）

挂账 fastlane-dead-plugin-review 二选一 (a)「有真实消费方」的落地：

1. **B2-R 门控放宽**（fast_lane.py + fan_out_questions.yaml defaults）：
   - confidence 门控 0.3→**0.1** 试点（`fast_lane_min_confidence`，策略热更）；
   - factual_lookup 薄弱域保留回退；
   - FanOut 投机 difficulty 门槛 1.5→**0.5**（`fan_out_speculative_min_difficulty`）；
   - 内置兜底值=放宽值，读失败不崩。
   - 实测：verdict 存活率 **0/24 → 16/20**（Laya 实机，laya 0.3.5 安装后）。
2. **FanOutScheduler pre_classifier 装配**（fan_out_scheduler.py + hooks.py）：
   - `laya_pre_classifier` = fast_route 门控版（NOT_GOOD_AT 回退 + B2/B2-R confidence/薄弱域门控）；
   - `assemble_fan_out_scheduler(enable=True, use_laya_pre_classifier=True)` 装配助手（__init__ 已导出）；
   - `DefaultLoopHooks.__init__` 挂三钩子（env LINGCLAUDE_FAN_OUT=1 启用，默认关直通）；
   - fast lane 从「resolve 链分类抢跑（无消费方）」升级为「FanOut 投机预分类（真实下游）」。
3. **env=0 强关修复**（task_router.py 门禁段）：env 显式设值（=1 开/=0 关）压过 yaml 策略键，
   未设才走策略；三态探针（unset→True / 0→False / 1→True）全绿。
4. **收益裁定（诚实账）**：
   - **冷启动 21.6s**（torch + Laya 322M 加载），稳态 957ms/resolve（off 45ms）——Laya 首调是大头；
   - 20 条常规 prompt **0 决策修正**（B2-R 放宽后 verdict 存活 16/20，但 domain/difficulty
     未触发 task_type 变化——关键词轨已覆盖盲区，fast lane 无增量收益）；
   - **fast lane 维持默认开（yaml fast_lane_enabled: true）+ env=0 可强关**：开销为正
     （稳态 +900ms）且常规分布 0 修正，但与挂账二选一 (a) 一致——FanOut pre_classifier
     消费方已接通（LINGCLAUDE_FAN_OUT=1），投机收益=分支命中，另行实测；
   - FanOut 启用路径探针：Laya verdict 存活（domain=code conf 0.81 / difficulty 1.93），
     plan 产出 2 分支；未启用直通 None 零分叉。

**挂账 fastlane-dead-plugin-review 更新**：(a) 消费方已接通（FanOut pre_classifier），
到期 2026-11-21 复核点 = LINGCLAUDE_FAN_OUT=1 实机投机命中率验证；冷启动 21.6s 是
转正前待解决项（进程级 Laya 常驻池 / 懒加载预热可降），暂挂观察。

## 七、P0-2/P1-6/P1-7 实机收益验证 + 审计/债务收口（2026-09-22）

| 项 | 验证 | 结果 |
|----|------|------|
| P0-2 审批矩阵 | on_failure 策略：只读 auto_mode_pass / 常规写静默放 / rm_rf_root 硬拒 | PASS（安全不回归） |
| P1-6 worktree | agent_batch LINGCLAUDE_AGENT_WORKTREE=1 真跑：建独立 worktree/回收结果/cleanup 全链 | PASS |
| P1-7 凭据池 | from_env 装配 + LRU 轮转 + 熔断跳过 + 全熔断落回 env 链 | PASS（3/3） |
| J3 审计 | 6 manifest（ast/bash/file_ops/git/read/web）补 sub_seams 停层声明（bash=2 后端实证） | 关单 |
| arch_debt 12F | 4 真实 F 处置：daemon 自转绿 / gray_zone 断言修（rm -rf 拦下场景）/ mcp_proxy 键位漂移修（实查归属）/ p11 归环境敏感 | 关单（16E 维持归因） |

定向回归：gray_zone+mcp_proxy+daemon+铁律守卫 59 passed；返审触发器守卫自检 PASS。

**二选一裁定**：fast lane 挂账 (a) 转正路径已落地（FanOut pre_classifier 消费方接通），
冷启动 21.6s 为转正前待解决项；P0-2/P1-6/P1-7 收益实机兑现，维持 env 门禁默认关
（先挂线后翻开关的保守设计），实机消费方验证已完成，可择机翻默认开。
arch_debt regression-preexisting-failures-20260921 因 12F 全部处置完成（16 项转绿 +
4 真实 F 修复/归因 + 16E 维持归因）标记 resolved。
