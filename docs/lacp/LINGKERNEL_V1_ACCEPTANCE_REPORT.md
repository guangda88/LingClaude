# LINGKERNEL_v1 工程验收报告 (2026-08-20)

**验收人**: 灵克 (lingclaude) 汇总
**验收对象**: 灵族 coding agent 内核 LINGKERNEL_v1 (D0-D8) + 智桥重建
**验收方式**: 证据链核查 (commit + 测试实测 + 文件存在性 + 三方确认帖)

---

## 一、灵克主线 (D0-D8)

### 1.1 交付物核查 (全部 ✅)

| 阶段 | 交付 | commit | 实测 |
|------|------|--------|------|
| D0 | ToolDefinition 4 字段 + ToolPipeline 5 段 | 41f62da | tools.py 127 行 / tool_pipeline.py 254 行 ✅ |
| D1 | 6 包 spine (session_store/model_adapter/audit_collector) | 41f62da | 188/115/110 行 ✅ |
| D2 | model_request_log (MV-1 预留) + coding.py 接线 | 41f62da | 192 行 ✅ |
| D3 | MV-1 上线 + query_engine 注入 4 模块 | 41f62da | test_mv1_wiring 7 测试 ✅ |
| D5 | checkpoint 委托 session_store | cd5095d | _sync_session_store 语义保持 ✅ |
| D6 | turn_learner + system_prompt_builder 抽离 | cd5095d | 127/169 行，query_engine 2078 行 ✅ |
| D7 | ToolDefinition 接口 (D2 已落地) | 41f62da | output/concurrency/finalize/present ✅ |
| D8 | MV-1b fold + 双点校验 + Mv1Violation | 0c0384e | test_d8_mv1b 16 测试 ✅ |

### 1.2 测试账本

```
基线 (D0 前): 2006 passed
最终 (D8 后): 2077 passed, 63 skipped, 0 failed (294s 实测)
净增: +71 项，全程零回归
```

### 1.3 评估标准 (灵研修订版两档)

| 指标 | 目标 | 实际 | 判定 |
|------|------|------|------|
| query_engine < 1500 (D+7) | 1500 | **2078** | ⚠️ 未达 (渐进掏空策略折中) |
| query_engine < 800 (D+14) | 800 | 2078 | ⏳ 未到期 |
| spine 模块化 | 6+ 包 | 9 新模块 3580-2078=1502 行外置 | ✅ |
| MV-1 不变量 | 上线 | MV-1a + MV-1b + 双点校验 | ✅ 超额 |

**行数指标说明**: 2078 > 1500 目标未达。原因: 激进拆包决策按"新包全量落地 + 内联渐进掏空"折中执行 (灵研 spec-review 确认此策略并据此主动下调了目标)。剩余掏空排期 D9+。

---

## 二、成员交付核查

| 成员 | 任务 | 交付物实测 | 测试 | 判定 |
|------|------|-----------|------|------|
| 灵犀 | #8 env scrub | proxy/manager.ts:135 修复 | 32/32 | ✅ |
| 灵犀 | #7 scoped registry | scoped_registry.ts (治理修订 3 项落实) | 12/12 | ✅ |
| 灵犀 | #7③ stderr 方言分类 | result_classifier.ts 双语签名 | 11/11 | ✅ |
| 灵极优 | #6 capability seam | optimization_seam.py 8191B + verify_rebuild | 13/13 全量 249 | ✅ |
| 灵信 | #5 L-a/L-b | message_schema/derived_views/invariants/layer_b 4 模块 | 47+4 全量 1004 | ✅ |
| 灵通 | #14 seam 三角色 | seam_registry.py (fail-loud) | 9/9 全量 3190 | ✅ |
| 灵研 | #5-spec | INVARIANT_MODEL_VISIBLE_v0.1.md | spec-review 有条件通过 | ✅ |
| 灵研 | spec-review | 3 发现 (R1/R2/R3) | 全部闭环于 D8 | ✅ |
| 灵安 | #11 security_gate | fail-closed + Decision 4 值 + dsh mapping | 452/452 | ✅ |

**文件存在性**: 灵极优路径为 `lingminopt/lingminopt/core/`（包内嵌套，非报告所称顶层），已实测确认存在。

---

## 三、智桥重建验收

| 项 | 结果 |
|----|------|
| 代码量 | 3977 -> 2945 (-26%) |
| 测试 | 90 passed / 0 failed (实测) |
| 核心端点 | healthz/v1/health/metrics/api-visible 全 200 (实测) |
| 端口契约 | 8767 四层不变 |
| 决策依据 | 三方确认 (灵通/灵安/灵研) + 流量实证 (45000 请求 0 调用) |
| 可回溯 | tag pre-rebuild-3977 |
| 落盘 | docs/lacp/ZHIBRIDGE_REBUILD_SUMMARY.md (974abcd) |

---

## 四、验收结论

### 通过项 (9/10)

1. ✅ 灵克 D0-D8 全部落库，测试 2077 全绿零回归
2. ✅ MV-1 不变量体系超额交付 (a/b 双层 + 双点校验)
3. ✅ 灵研 spec-review 3 发现全部闭环
4. ✅ 灵安 #11 sign (452 tests)
5. ✅ 灵信 L-b 消费点就绪 (1004 tests)
6. ✅ 灵极优/灵通/灵犀 P0+P1 交付齐备
7. ✅ 智桥重建验收全绿
8. ✅ 全部交付物 commit 落库 + HMAC 签名
9. ✅ 三方确认帖齐备 (智桥砍除决策)

### 遗留项 (不影响验收通过)

1. ⚠️ query_engine 2078 行 > D+7 目标 1500 (策略折中，D9+ 掏空)
2. ⏳ 灵安 #12 audit_trail_invariant.py 进行中 (不阻塞，已有排期)
3. ⏳ 灵信 AgentMV1Consumer 迁移结构化接口 (接口已生效)

### 最终判定

**LINGKERNEL_v1 P0 六项全部通过工程验收。**
建议: D+14 前完成 query_engine 掏空至 800 行目标 + 灵安 #12 收尾。

---

灵克 (lingclaude)，2026-08-20
验收证据: 本报告全数据实测，非转述