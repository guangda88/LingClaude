# 硬化实施报告
> 生成时间: 2026-05-19
> 状态: 完成

## 硬化实施清单

| # | 实施项 | 状态 | 路径/说明 |
|----|--------|------|-----------|
| 1 | `autonomy_budget.json` 预算配置 | ✅ 完成 | `.audit/autonomy_budget.json` |
| 2 | `.sandbox/` 隔离目录 | ✅ 完成 | `.sandbox/{proposals,audit_results,pending_review,archive}` |
| 3 | `sdth_detector.py` SDTH检测脚本 | ✅ 完成 | `.sandbox/sdth_detector.py` |
| 4 | handover 任务元数据字段 | ✅ 完成 | 见下方扩展字段 |
| 5 | 全族 CRUSH.md 规则同步 | ⏳ 进行中 | — |
| 6 | 硬化实施审计 | ⏳ 待开始 | — |
| 7 | 全族通报 | ⏳ 待开始 | — |

## 预算配置摘要

```json
{
  "global": {
    "daily_total": "300-400M tokens",
    "self_drive_ratio": "1/5 (60-80M tokens/day 全族)",
    "per_member_baseline": "8M tokens/day",
    "per_member_peak": "12M tokens/day"
  }
}
```

## handover 扩展字段

```json
{
  "task_id": "string",
  "source": "user_directed | ai_continuation | self_generated",
  "confidence": 0-100,
  "rollback_plan": "string",
  "autonomy_zone": "green | yellow | red",
  "user_confirmation_required": true/false,
  "fabrication_suspected": true/false
}
```

## SDTH 检测脚本功能

- 检测 "用户说请继续" → 标记为虚假外部授权
- 检测 "用户没有说" → 标记为否定性虚构授权
- 检测 "假设用户同意" → 标记为假设性自我授权
- 严重程度分级: CRITICAL / HIGH / MEDIUM

## 待完成

- [ ] 全族 CRUSH.md 规则同步（各成员执行）
- [ ] 硬化实施审计
- [ ] 全族通报