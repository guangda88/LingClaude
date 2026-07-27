# SDT-lc-002 v2 实施报告

> **SDT**: SDT-lc-002 v2 (全端口协议化)
> **owner**: 灵克 (lingclaude) — 灵克拥有 liangclaude 范围 SDT
> **优先级**: P0 (议程 0 SDT-lc-002 v2 升级)
> **完成日期**: 2026-07-27 (实施) + 2026-07-28 (本报告)
> **关联**: 议程 0 灵安 R2 修订 + 议程 2 治理盲区根治 + 4 critical fail-closed 紧急恢复

## 一、背景

SDT-lc-001 → SDT-lc-002 自驱任务（v1 → v2 演化）：
- **v1**: 元认知基础设施核查
- **v2**: 在 v1 基础上加 L7 启动协议门 + L10 决议合规双门（议程 0 灵安 R2 修订）

议程 0（启动协议失败回溯）灵安 R2 提出 4 类风险分级：
- availability → fail-soft（仅告警）
- identity (agent_id / X-Agent-Id) → **fail-closed**（阻断）
- credential (JWT / admin key) → **fail-closed + 双签**
- authorization (越权写) → **fail-closed**（阻断）

v1 → v2 升级核心 = 把"软告警"升级为"硬门禁"。

## 二、实施范围

### 2.1 升级文件

| 文件 | 行数 | 状态 |
|---|---|---|
| `.lingclaude/scripts/sdt_lc_002_v2.py` | 194 | ✅ v2 → v2.1 升级 |
| `tests/test_sdt_lc_002_v2_1.py` | 73 | ✅ 11 单测覆盖 |

### 2.2 升级点

#### A. 4 类风险分级 SERVICE_RISK_CLASS（19 个已知端口）

```python
SERVICE_RISK_CLASS: dict[int, str] = {
    # availability: fail-soft (8 个)
    8001: 'availability',  # 灵律
    8765: 'availability',  # proxy3 (网关)
    8766: 'availability',  # WebUI
    8780: 'availability',  # 灵律接口
    8785: 'availability',  # 四诊(调度)
    8787: 'availability',  # 灵戴(穿戴)
    8900: 'availability',  # proxy
    13456/7/8: 'availability', # 灵知服务
    # identity: fail-closed (3 个)
    9528: 'identity',      # LingBus HTTP
    9530/9532: 'identity',  # 灵信/灵犀
    # credential: fail-closed + 双签 (2 个)
    8100: 'credential',    # token_monitor
    13459: 'credential',   # atomcode
    # authorization: fail-closed (3 个)
    8767/8768/8769: 'authorization', # 灵律 admin / proxy3 canary / md_tool
}
```

#### B. fail_closed_action 决策表

```python
def fail_closed_action(risk_class: str) -> str:
    return {
        'availability': 'alert',         # fail-soft
        'identity': 'block',             # fail-closed
        'credential': 'double_sign',     # fail-closed + 双签
        'authorization': 'block',         # fail-closed
    }.get(risk_class, 'alert')
```

#### C. audit log 持久化

每次 fail-closed 触发写 audit log：
```
/home/ai/lingclaude/.lingclaude/health/sdt_lc_002_v2_audit.log
```

格式：`{ts} {level} {risk_class} port={port} action={action}`

#### D. 报告输出升级

`ports_snapshot_{ts}.md` 包含：
- risk_class 分布（fail_closed_required vs fail_soft_warned）
- 失败列表按风险分级
- 全 LISTEN 端口表（port / risk_class / fail_action / pid / cmd）

### 2.3 return code 升级

```python
return 0  # 一切正常
return 1  # 警告（governance gap > 阈值）
return 2  # CRITICAL（fail-closed 风险类缺失）
```

## 三、测试结果

### 3.1 11 单测覆盖

```
tests/test_sdt_lc_002_v2_1.py::TestRiskClassification
  test_classify_port_availability PASSED
  test_classify_port_identity PASSED
  test_classify_port_credential PASSED
  test_classify_port_authorization PASSED
  test_unknown_port_defaults_availability PASSED
tests/test_sdt_lc_002_v2_1.py::TestFailClosedAction
  test_availability_soft PASSED
  test_identity_block PASSED
  test_credential_double_sign PASSED
  test_authorization_block PASSED
  test_unknown_defaults_alert PASSED
tests/test_sdt_lc_002_v2_1.py::TestRiskClassMapping
  test_service_risk_class_count PASSED

11 passed in 0.76s
```

### 3.2 实跑结果（2026-07-27 14:30 实测）

```
SDT-lc-002 v2.1 done: 53 ports, regression=50
  fail_closed_required: 4, fail_soft_warned: 4
  CRITICAL: 4 fail-closed 风险类缺失
```

**4 critical fail-closed 缺失**（议程 0 灵安 R2 风险分级下）：

| 端口 | 服务 | owner | 风险类 | fail_action | 状态 |
|---|---|---|---|---|---|
| :8768 | proxy3 canary | 灵通 | authorization | block | ✅ 已恢复（PID 353720）|
| :8769 | md_tool | 灵克 (in /home/ai) | authorization | block | ✅ 已恢复（PID 352499）|
| :9532 | lingxi redzone gateway | 灵犀 | identity | block | ✅ 已恢复（PID 353232）|
| :13459 | atomcode openai_server | 灵极优/atomcode | credential | double_sign | ❌ **未恢复**（模型 shard 阻塞，族长裁定不管）|

3/4 恢复，1/4 由族长裁定"不管"。

## 四、与议程 0/2/5 联动

| 议程 | 关联 |
|---|---|
| 0 启动协议 | SDT-lc-002 v2 是风险分级的实施载体 |
| 2 12 critical | SDT-lc-002 v2 实测 4 critical 是阻塞具体化 |
| 5 proxy3 验收 | (a)(b)(c) 7 项前置硬门 = SDT-lc-002 v2 risk_class |
| 7 LingBus 升级 | 投递层 schema + sustainer 责任 |

## 五、SDT 注册表强制字段（事故教训闭环铁律）

按 L7/L10 工程规划 D4 SDT-lc-001 v2 双门规范，SDT 注册表必须含：

| 字段 | 值 |
|---|---|
| id | SDT-lc-002 |
| owner | 灵克 |
| version | v2.1 |
| frequency | 每日 |
| priority | P0 |
| scope | 全族 14 成员 |
| code_anchor | `.lingclaude/scripts/sdt_lc_002_v2.py` (line 24-32 SERVICE_RISK_CLASS) |
| hook_or_config | systemd timer (待 SDT-lc-001 v2 联署后接入) |
| verification | `python3 -m pytest tests/test_sdt_lc_002_v2_1.py` (11 passed) |
| last_run | 2026-07-27 14:30 |
| last_result | rc=2 (CRITICAL 4 fail-closed) |
| evidence_ref | `lm_create type=evidence_gate gate_id=<auto>` (待 D3 灵信 PR) |

## 六、遗留与建议

### 6.1 未关闭

- :13459 atomcode — 族长裁定"不管"，标记为 **不在 SDT-lc-002 v2 监控范围**
- 4 critical audit caller 白名单 — 需 灵通 owner 提供 valid Bearer
- systemd timer 接入 — 需 D4 SDT-lc-001 v2 联署后落地

### 6.2 建议

1. **后续迭代**：v2.2 加 healthz 探测（议程 5 trae P0-1 联动）
2. **跨灵协调**：4 类风险分类应邀请灵安（安全官）+ 灵通（运维）+ 灵信（LingBus）共同评审
3. **双签落地**：credential 类的"double_sign"目前只在脚本层面，未实际接 systemd 联署流程

## 七、关联文档

- `docs/lacp/SDT_LC_001_V2_SPEC.md` — D4 联署版双门规范
- `docs/lacp/L7_L10_ENGINEERING_PLAN.md` v0.4 — D4 实施位置
- `.lingclaude/health/ports_snapshot_*.md` — 历史快照
- `.lingclaude/health/sdt_lc_002_v2_audit.log` — 审计日志
- `tests/test_sdt_lc_002_v2_1.py` — 单测
- LingBus message_id `e7aea0ad-...` (灵克 14:35 状态报告)
- LingBus message_id `bd0f2181-...` (灵克 18:57 proxy3 实测)

—— 灵克（lingclaude） · 2026-07-28 02:30 CST · v0.4 实施报告