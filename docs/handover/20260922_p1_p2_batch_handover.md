# 交接文档：2026-09-22 P1 收尾 + B1/B2 + A 组清偿批次

> 交接人：灵克（lingclaude，AtomCode glm5.3-flash）
> 交接对象：下一会话 / lc
> 基线：HEAD `f8a338f`（本文档落盘时），分支 master

## 一、本轮完成的批次（按提交序）

| 提交 | 批次 | 内容 |
|------|------|------|
| `d30911f` | #5 质量验证 | Laya fast lane 实机判定质量脚本入库（domain 82% / difficulty 88%） |
| `74c29c5` | B2 门控 | fast_lane confidence 门控（≥0.3 放行）+ factual_lookup 薄弱域回退，探针 10/10 |
| `9064ce9` | B1 真身 | FanOutScheduler 调度器（plan/verify/should_continue，默认 enable=False 直通），探针 6/6 |
| `f6cd652` | A2 台账 | M3 台账 20 条边梳理 + mcp_proxy 4 边"不列 L1 回收"裁定记录 |
| `43e2676` | P1 收尾 | 全量回归归因修复：model_call 回 import + G1 白名单行号校准 |
| `81e99ee` | B1-T 调优 | difficulty 分桶门控（score<1.5 不投机），探针 5/5 |
| `300512e` | A组清偿① | system prompt 断言 13 处锚点校准（P0-3 拆分对齐），锚点批 154 passed |
| `f8a338f` | A组清偿③ | 剩余 6 F 清偿（S3 懒导出 / lingxi manifest / sandbox 粒度 / EXEMPT 7 模块 / lsp_tools 拆分） |

## 二、关键裁定与归因结论

1. **mcp_server 16E = 环境敏感型，非代码回归**（A组清偿②）：全量并发跑时
   MCP server 子进程拉起受资源挤压报 `subprocess.CalledProcessError`；
   单跑 `TestGlob` 1 passed、整文件 34 passed。不列代码清偿。
2. **model_call 模块级倒装改 PEP 562**：`:23` 模块级 `from lingclaude.engine…`
   违反 S3 守卫（core 模块级倒装=0），改 `__getattr__` 懒导出保
   `mc.AGENT_MAX_TOOL_ROUNDS` 消费面；G1 白名单与 M3 台账同步收缩 `:23`
   （棘轮允许方向），新 `:456` 懒导出边入账。
3. **fast lane 部署裁定依据**（不写 SLA）：conf≥0.3 放行（<0.3 的 2 条明细
   全误判）；factual_lookup 域无论置信回退（3 条 2 误，最薄弱域）；
   difficulty score≥1.5 才值得投机（trivial/easy 投机无收益）。
4. **dead_modules 7 文件入 EXEMPT**（治理/协议预留面）：evidence_protocol
   （经 model_call:127 动态 import）、rollout（经 submission:345）、
   approval_matrix / goal_receipt / manifest_lock / verify_ledger / worktree
   为预留 API 面，生产接线随 arch_debt B 组推进。

## 三、验证状态

- 铁律守卫：**8 绿**（`tests/test_iron_law_guards.py`，本轮实测）
- 锚点批：154 passed；清偿批定向 17+26+51 passed
- 上轮全量回归基线：**29F+16E**（修复前）；P1 引入的 2 个新 F 已清
- **终局全量回归后台在跑**（pid 2981255，日志 `/tmp/full_regression_final.log`），
  预期显著低于 29F+16E——**下一会话第一件事：收终局结果对基线 diff**，
  确认净减数并更新 arch_debt 台账（任务 #5 未完项，仅剩这一步）

## 四、下一会话待办（按优先级）

1. **收终局回归**：`grep -E "^[0-9]+ (failed|passed|error)" /tmp/full_regression_final.log`，
   F 名单与 `/tmp/final_f.txt`（29F 基线名单）diff，净减数入台账，任务 #5 收口
2. **arch_debt B 组**：governance/verify_ledger/worktree 等 7 个预留模块的
   生产接线推进（EXEMPT 注释里已标注依赖关系）
3. **fast lane 默认开启评估**：终局回归确认无回归后，env 门禁
   `LINGCLAUDE_LAYA_FAST_LANE` 是否翻默认开的裁定（需实机会话验证冷启动开销）
4. **B1 策略扩展**：FanOutScheduler 分支 prompt 模板走策略文件、should_continue
   重复度终止判定（代码里已留扩展位）

## 五、坑与纪律（本轮新增内化）

- 外部结果解析 4 纪律（详见 AGENTS.md 速记 + .atomcode/memory.md）：
  先看结构再写读法 / 聚合与源冲突以源为准 / 后台任务 mtime 核对 /
  kill 用精确 pid。
- S3 守卫与 G1 白名单是**两套独立检查**：G1 允许的"函数内动态 import"
  在 S3 有更严的"模块级=0"约束——core 加回 import 前先查 S3，
  PEP 562 `__getattr__` 是合规通道。
- `pytest --co`（collect-only）先跑一遍可提前暴露 import 级断裂，
  比等全量跑到 45% 才炸省 40 分钟。
