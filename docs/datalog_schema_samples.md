# Datalog 统一 schema 草案 — 灵克样本

> 供 P0.8 schema review 使用。灵克侧 3 类事件样本。

## 1. L5 审计事件 (audit_history)

```json
{
  "event_type": "l5.audit.round",
  "timestamp": "2026-07-19T04:30:00.123Z",
  "member": "lingclaude",
  "session_id": "90de058b-469",
  "l5_session_id": "90de058b-469",
  "l5_round": 2,
  "consistency_score": 0.98,
  "should_early_exit": true,
  "should_fix": false,
  "t1_fact_check": {
    "total_claims": 3,
    "verified": 2,
    "weak": 1,
    "unverified": 0,
    "avg_completeness": 0.85
  },
  "t3_entity_conflict": {
    "conflict_score": 0.0,
    "ungrounded_claims": []
  },
  "model": "glm-5.2@zai",
  "latency_ms": 2340
}
```

## 2. T0 行为校验事件

```json
{
  "event_type": "t0.behavior.check",
  "timestamp": "2026-07-19T04:30:00.123Z",
  "member": "lingclaude",
  "session_id": "90de058b-469",
  "checks": {
    "edit_verify": "pass",
    "tool_repetition": "nudge",
    "consecutive_failure": "pass",
    "output_completeness": "pass"
  },
  "nudges": ["检测到编辑后未运行验证命令"],
  "should_block": false
}
```

## 3. 工具调用退化事件 (degradation_alert)

```json
{
  "event_type": "tool.degradation.alert",
  "timestamp": "2026-07-19T04:30:00.123Z",
  "member": "lingclaude",
  "session_id": "90de058b-469",
  "signal": "tool_repeat",
  "severity": "warning",
  "detail": "grep called 3 times with same args",
  "msg_index": 42
}
```
