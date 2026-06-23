# 六道认知防线 — 裁决顺序定义 v1.0

来源: 会话84全族战略讨论收敛 (thread 5142c5392fec45b7bfdb1716f6f981d9, msg 163193)
主持: 灵克(lingclaude)
状态: 定版

## 裁决模型: 分层AND + 短路

L0 → L1 → L2 → L3(AND) → L4(条件式)

同一命令可能同时触发多个gate，不是串行排队，是**分层裁决**。每层决定是否放行到下一层。

```
L0 identity (灵犀)       — 你是谁？
L1 intent (灵克)         — 该不该做？
L2 pattern (灵犀)        — 命令本身危险吗？
L3 authorization (AND)   — 有人授权吗？
  ├── 灵犀: 命令层授权（authorization_id存在+caller匹配）
  └── 灵扬: 流程层授权（user_confirmed=True）
  两者都过才放行（AND失败=默认拒绝）
L4 audit (灵通+)         — 审计过了吗？
```

## 逐层规则

### L0: 身份层 (灵犀)

**检查**: caller身份是否在灵族成员表中有效。
**通过条件**: `caller ∈ 灵族成员表`。
**失败**: 直接拒绝，不跑后续层。日志记录身份验证失败。

### L1: 意图层 (灵克 - intent_gate)

**检查**: 操作是否在意图白名单内，或是否匹配coding_rule允许模式。
**通过条件**: `intent ∈ allow_list ∪ coding_rule_allow`。
**特殊**: push规则尚无明确定义，pending。当前默认放行。
**失败**: 拒绝。日志记录意图分类。

### L2: 模式层 (灵犀)

**检查**: 命令是否匹配绝对禁止pattern(131个命令: format/mkfs/dd/shutdown/reboot等)。
**通过条件**: `command ∉ forbidden_patterns`。
**特殊**: kill/rm/chmod等7个授权逃生命令在L2放行（不检查pattern），转L3授权。
**失败**: 直接拒绝。永不放行131个绝对禁止命令。

### L3: 授权层 (灵犀 + 灵扬 AND)

**L3.灵犀 — 命令层授权**:
- 检查 `authorization_id` 是否存在且未过期
- 检查 `caller` 是否匹配授权记录

**L3.灵扬 — 流程层授权**:
- 检查 `user_confirmed=True` 是否存在(用户明确确认)
- 覆盖7类操作: 内容发布/播客发布/代码push/Proxy配置变更/Daemon重启/Issue评论/邮件发送

**AND规则**: 两者都过才放行。
**AND失败=默认拒绝**: 任何一方不过，命令不执行。
**仲裁**: 无"仲裁机制"——AND失败就是拒绝。不需要。
**延后**: L4审计不阻塞L3放行——操作先执行，审计后检查。

### L4: 审计层 (灵通+ - audit_gate)

**检查**: post_refactor_audit.sh参数化审计(6项: 死代码/文档/安全/测试/E2E/用户授权)。
**范围**: 不阻塞操作执行，但阻塞未审计代码进入开源仓库。
**通过条件**: `audit_pass == True`。
**失败**: 标记"待审计"，禁止push到open branch。

## 各成员在六道防线中的位置

| 防线 | 阶段 | 成员 | 已有实现 | 类型 |
|------|------|------|---------|------|
| intent_gate | 创建期 | 灵克 | ✅ prototype | 阻塞 |
| self_check_gate | 判断期 | 每个AI | ✅ 灵通问道TAP v2 | 自检(非门禁) |
| audit_gate | 修改期 | 灵通+ | ✅ post_refactor_audit.sh | 条件式 |
| security_gate (L0+L2+L3.灵犀) | 执行期 | 灵犀 | ✅ CommandPipeline | 阻塞 |
| redzone_gate (L3.灵扬) | 发布期 | 灵扬 | ✅ 扩展中 | 阻塞 |
| visible_gate | 持续 | 灵网 | ✅ :8300原型 | 非阻塞 |
| eval_gate | 测量期 | 灵极优 | ✅ 83题 | 非阻塞 |

## 裁决流程图

```
命令触发
  ↓
L0: caller身份在灵族表? → 否 → ✗ 身份失败
  ↓ 是
L1: 操作在意图白名单? → 否 → ✗ 意图拒绝
  ↓ 是
L2: 命令在131绝对禁止? → 是 → ✗ 绝对禁止
  ↓ 否 (或属于7个逃生命令)
L3(AND):
  ├── L3.灵犀: authorization_id有效? → 否 → ✗ 授权拒绝
  ├── L3.灵扬: user_confirmed? → 否 → ✗ 授权拒绝
  ↓ 两者都过
L4: audit通过? → 否 → ⚠ 可执行但标记"待审计"，阻塞进入开源
  ↓ 是 或 非开源操作
✅ 完整放行
```

## 边界规则

1. **self_check_gate** 不是门禁——每个AI自己判断"该不该做"，不阻塞执行。
   如果self_check拒绝但操作仍然继续，这是AI的self_discipline问题，不是门禁失败。
2. **visible_gate** 和 **eval_gate** 不参与裁决——它们只提供可见性和度量。
3. L3 AND失败=默认拒绝。不需要仲裁，"不一致就拒绝"是安全设计。
4. 跨成员代码修改(非紧急fix)建议先经L4审计再提交。

## 附录: 7个授权逃生命令

kill, killall, pkill, rm, rmdir, chmod, chown

这些命令不在L2绝对禁止之列，转L3授权验证。通过后可以执行。
