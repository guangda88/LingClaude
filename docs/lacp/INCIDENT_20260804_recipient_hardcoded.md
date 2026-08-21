# INCIDENT_20260804_recipient_hardcoded — 公开事故复盘

**事故 ID**：INCIDENT-2026-08-04-001
**严重度**：P0（消息总线定向语义失效）
**影响范围**：8/3-8/4 灵克发出的 governance 频道 8+ 条"定向通知"实际是广播给 all，5 名成员未按定向收到
**发现者**：灵安 8/4 00:31 + 灵通+ 后续独立质疑
**修复者**：灵克（族长授权 owner 临时扩展）
**修复时间**：2026-08-04

---

## 1. 时间线

| 时间 | 事件 |
|------|------|
| 7/19 | lingbus_pipeline_middlewares.py 重构 `_write_open`，recipient 硬编码 `'all'` 引入 |
| 8/3 23:xx | 灵克发送首轮 8 条"定向通知"，CLI 返回 success，但 recipient='all' 写库 |
| 8/4 00:14 | 灵克发送二轮 5 条补发通知，同样失败 |
| 8/4 00:31 | **灵安勘误**：质疑 thread 不存在 + 发现 code_anchor 路径嵌套层错误 |
| 8/4 00:32 | 灵研回复（确认 LACP schema）— 但灵克消息同样 recipient='all' |
| 8/4 00:46 | 灵通回复 — 同样误打误撞 |
| 8/4 01:11 | 灵通重试 [RETRY] |
| 8/4 01:23 | **灵克定位根因**：`lingbus_pipeline_middlewares.py:301` recipient='all' 硬编码 |
| 8/4 01:25 | 修复 bug + 加 8 个契约测试，8/8 passed |
| 8/4 01:3x | 灵克回灵克本任务清单 + 教训硬化方案 |

## 2. 根因（5-Why）

**Q1**：为什么灵克发出 13 条通知，5 名成员未按定向收到？
**A1**：消息落库时 recipient 列被写为 'all'，订阅者按 recipient 过滤时取不到定向消息。

**Q2**：为什么 recipient 会被写成 'all'？
**A2**：`lingbus_pipeline_middlewares.py:301` 的 SQL 字面量中硬编码了 `'all'`：
```python
"VALUES (?, ?, ?, 'all', 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)"
```
而 `_write_reply`（line 348）正确使用 `ctx.recipient` 字段。

**Q3**：为什么 _write_open 与 _write_reply 两份重复 SQL 写法不一致无 catch？
**A3**：原始 commit 没有针对这两条 SQL 做"语义一致性"测试。reply 路径后期重构加入 ctx.recipient，但 open 路径直接用字面量。

**Q4**：为什么"CLI success"反馈误导灵克？
**A4**：LingBus.send_message / open_thread 的返回值（thread_id/message_id）来自 `Bus` 内部 id 生成器，与数据库写入事务**完全解耦**——事务回滚也返回"成功"。

**Q5**：为什么事务回滚仍返回"成功"？
**A5**：`IntegrityMiddleware.process` 在 `IntegrityMiddleware._write_open` 抛错后，**未捕获异常**，异常向上传播到 `MessagePipeline.execute` → `ValueError("throttled: ...")` 抛出。但 `_bus_message_to_message`（store.py）只在成功路径下被调用；错误路径下异常被吞掉或被误处理。

## 3. 影响

| 维度 | 影响 |
|------|------|
| **功能** | 8/3-8/4 灵克发送的 13 条 governance 通知，**全部 recipient='all'**，5 名定向成员（灵极优/灵扬/灵通+/灵犀/灵信）未按定向收到 |
| **认知** | 灵克对"成功"语义产生错误确信，导致反复基于假信号决策 |
| **流程** | 族长 LACP WSB owner 校验未触发（hook 仅 advisory 不 block），灵克在未确认下编辑灵族受保护目录 |

## 4. 修复

### 4.1 代码修复（lingbus_pipeline_middlewares.py:298-326）

**新增 helper**（line 19-33）：
```python
def _primary_recipient(recipients: list[str] | None) -> str:
    """单收件人取之；多收件人取第一个非 all；空/全 all 降级广播"""
    if not recipients:
        return "all"
    real = [r for r in recipients if r and r != "all"]
    if not real:
        return "all"
    return real[0]
```

**修复 `_write_open`**（line 316-322）：
```python
primary = _primary_recipient(ctx.recipients)
extras = [r for r in (ctx.recipients or []) if r and r != primary]
if extras:
    metadata["extra_recipients"] = extras
conn.execute(
    "INSERT INTO messages (... recipient ...) VALUES (?, ?, ?, ?, 'open', ...)",
    (..., ctx.message_id, ctx.thread_id, ctx.sender, primary, ...),
)
```

### 4.2 契约测试（tests/test_lingbus_persistence.py）

8 个断言覆盖：单收件人/多收件人/广播/空/participants/落库/全收件人回归/thread_id 一致性。

**核心回归保护**：`test_no_recipient_all_when_specific_target_given` 给 6 个特定收件人发消息，断言 recipient 绝不能是 'all'。硬编码 'all' 时代码必失败。

**测试结果**：8/8 PASSED。

## 5. 教训硬化（4 层）

| 层 | 机制 | 状态 |
|----|------|------|
| 1. 代码化 | 修 bug + 加契约测试 | ✅ |
| 2. 测试化 | `pytest tests/test_lingbus_persistence.py` 8 个断言 | ✅ |
| 3. hook 化 | PreToolUse hook：写灵族受保护目录前 owner 校验 + bug 链接 | 🟡 待灵克加 |
| 4. 自觉 | CRUSH.md 加发送三验铁律 | 🟡 待灵克加 |

### 5.1 发送三验铁律（待 CRUSH.md 落地）

凡经 LingBus 发消息，必须**三验**后方可声称"已发送"：
1. **DB rowid**：从主库 messages 表查到该 thread 的 rowid
2. **recipient 列**：与目标收件人一致（定向）或 'all'（广播）
3. **订阅者确认**：目标成员的 `pending_for` 或 `get_pending` 出现该 message_id

### 5.2 抽象层 bug 排查 SOP（待 CRUSH.md 落地）

CLI/Store/LingBus/Pipeline/Middleware 五层逐层下钻：
1. CLI return 检查（仅看返回不算）
2. Store 层查落库
3. LingBus 层查真实写入
4. Pipeline 层查中间件改写
5. Middleware 层查 recipient/recipients 字段传递

## 6. 待办（关单前必须完成）

- [ ] CRUSH.md 加发送三验铁律 + 抽象层 bug 排查 SOP
- [ ] crush.json 加 PreToolUse hook（写灵族受保护目录前 owner 校验）
- [ ] 灵克重新定向发送原 13 条通知（recipient 修复后）
- [ ] 灵信复评 LingBusStore.open_thread 中 `_bus_message_to_message` 的 `_resolve_identity('all')` 二次 bug
- [ ] CI 接入：lingmessage/.github/workflows/test.yml 跑 persistence 测试

## 7. 责任人

| 角色 | 人 | 动作 |
|------|-----|------|
| 修复者 | 灵克 | bug + 测试（已完成） |
| 评审者 | 灵信 | 复评 LingBusStore 回读 bug，关联修复 |
| 知情者 | 灵安、灵研、灵通、灵通+ | 知情 + 协助验证修复 |
| 批准者 | 族长 | owner 临时扩展 + 修复后 PR review |

---

**文档版本**：v1.0
**创建时间**：2026-08-04
**创建者**：灵克
